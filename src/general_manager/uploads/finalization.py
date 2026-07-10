"""Atomic ORM upload claims and recoverable post-commit materialization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import partial
import os
import re
import secrets
import sqlite3
import time
import unicodedata
import warnings
from typing import TYPE_CHECKING, IO, Any, TypeVar, cast
from uuid import UUID

from django.db import (
    DEFAULT_DB_ALIAS,
    OperationalError,
    connections,
    models,
    transaction,
)
from django.utils import timezone

from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.uploads import services
from general_manager.uploads.adapters import (
    ClaimedObject,
    UploadAdapter,
    UploadFinalizationAdapter,
)
from general_manager.uploads.config import (
    FileInspection,
    FileUploadPolicy,
    get_file_upload_settings,
)
from general_manager.uploads.errors import (
    InvalidFileTypeError,
    InvalidImageError,
    InvalidUploadSizeError,
    UploadBackendUnsupportedError,
    UploadBindingMismatchError,
    UploadError,
    UploadExpiredError,
    UploadStorageChangedError,
    UploadStorageError,
    stable_upload_error,
)
from general_manager.uploads.models import UploadIntent, UploadQuotaLock
from general_manager.uploads.types import (
    ObjectVersion,
    UploadCandidate,
    UploadIntentState,
    UploadOperation,
)

if TYPE_CHECKING:
    from general_manager.interface.orm_interface import OrmInterfaceBase

type OrmInterfaceClass = type["OrmInterfaceBase[models.Model]"]
type MutationPayload = dict[str, object]

_T = TypeVar("_T")
_SQLITE_RETRY_ATTEMPTS = 6
_SQLITE_RETRY_DEADLINE_SECONDS = 0.25
_FRAMEWORK_STORAGE_PREFIXES = (
    "gm-upload-old-claims/",
    "gm-upload-claim/",
    "gm-upload-meta/",
    "gm-upload-stage-claim/",
    "gm-upload-stage-meta/",
)
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class _PreparedCandidate:
    field_name: str
    candidate: UploadCandidate
    intent_id: UUID
    model_field: models.FileField
    adapter: UploadAdapter
    version: ObjectVersion
    old_key: str | None
    old_version: ObjectVersion | None
    image_width: int | None
    image_height: int | None


@dataclass(frozen=True, slots=True)
class PreparedUploadClaims:
    """Storage-validated mutation inputs ready for a short database claim."""

    interface_cls: OrmInterfaceClass
    database_alias: str
    manager_name: str
    operation: UploadOperation
    actor_id: object
    target_id: str | None
    candidates: tuple[_PreparedCandidate, ...]


@dataclass(frozen=True, slots=True)
class LockedUploadClaims:
    """Intent rows locked and revalidated inside the mutation transaction."""

    prepared: PreparedUploadClaims
    intents: Mapping[UUID, UploadIntent]


def has_upload_candidates(values: Mapping[str, object]) -> bool:
    """Return whether the normalized payload contains an internal candidate."""

    return any(isinstance(value, UploadCandidate) for value in values.values())


def prepare_upload_claims(
    interface_cls: OrmInterfaceClass,
    values: Mapping[str, object],
    *,
    operation: UploadOperation,
    actor_id: object,
    target_pk: object | None,
) -> PreparedUploadClaims:
    """Inspect every exact staged version before acquiring database locks."""

    candidate_items = tuple(
        (name, value)
        for name, value in values.items()
        if isinstance(value, UploadCandidate)
    )
    if not candidate_items:
        raise UploadBindingMismatchError
    intent_ids = [candidate.intent_id for _name, candidate in candidate_items]
    if len(set(intent_ids)) != len(intent_ids):
        raise UploadBindingMismatchError

    settings = get_file_upload_settings()
    if not settings.enabled:
        raise UploadStorageError
    database_alias = _database_alias(interface_cls)
    if _has_unsafe_sqlite_outer_atomic(database_alias):
        raise UploadStorageError
    if database_alias != services._normalized_database_alias(settings.intent_database):
        raise UploadBindingMismatchError
    manager_name = _manager_name(interface_cls)
    manager_class = cast(Any, interface_cls._parent_class)
    target_id = _target_identifier(operation, target_pk)
    now = timezone.now()

    prepared: list[_PreparedCandidate] = []
    for field_name, candidate in sorted(candidate_items):
        try:
            intent = UploadIntent.objects.using(database_alias).get(
                pk=candidate.intent_id
            )
        except UploadIntent.DoesNotExist:
            raise UploadBindingMismatchError from None
        model_field = _file_field(interface_cls, field_name)
        _validate_candidate_binding(
            intent,
            candidate,
            manager_name=manager_name,
            field_name=field_name,
            operation=operation,
            target_id=target_id,
            actor_id=actor_id,
            at=now,
        )
        adapter = services._resolve_intent_adapter(intent, model_field)
        if not isinstance(adapter, UploadFinalizationAdapter):
            raise UploadBackendUnsupportedError
        recorded = services._stored_object_version(intent)
        try:
            policy = services._resolve_policy(manager_class, field_name, settings)
            services._normalize_size(recorded.size, policy)
            services._normalize_content_type(
                recorded.content_type or intent.declared_content_type,
                policy,
            )
            services._validate_extension(intent.original_filename, policy)
        except (InvalidUploadSizeError, InvalidFileTypeError) as error:
            _reject_content_intent(intent, error, database_alias=database_alias)
            raise
        except UploadError:
            raise
        except Exception:  # noqa: BLE001 - policy resolution boundary fails closed
            raise UploadStorageError from None
        try:
            inspected = adapter.inspect_staged(intent.staging_key)
        except UploadError:
            raise
        except Exception:  # noqa: BLE001 - custom adapter boundary fails closed
            raise UploadStorageError from None
        if type(inspected) is not ObjectVersion or inspected != recorded:
            raise UploadStorageChangedError
        try:
            image_width, image_height = _validate_staged_content(
                adapter,
                model_field,
                intent.staging_key,
                recorded,
                policy=policy,
                original_filename=intent.original_filename,
                max_image_pixels=settings.max_image_pixels,
                max_image_width=settings.max_image_width,
                max_image_height=settings.max_image_height,
                max_inspection_bytes=settings.max_inspection_bytes,
            )
        except (InvalidImageError, InvalidFileTypeError) as error:
            _reject_content_intent(intent, error, database_alias=database_alias)
            raise
        old_key, old_version = _inspect_old_object(
            model_field,
            adapter,
            operation=operation,
            target_pk=target_pk,
            database_alias=database_alias,
        )
        prepared.append(
            _PreparedCandidate(
                field_name=field_name,
                candidate=candidate,
                intent_id=intent.id,
                model_field=model_field,
                adapter=adapter,
                version=recorded,
                old_key=old_key,
                old_version=old_version,
                image_width=image_width,
                image_height=image_height,
            )
        )
    return PreparedUploadClaims(
        interface_cls=interface_cls,
        database_alias=database_alias,
        manager_name=manager_name,
        operation=operation,
        actor_id=actor_id,
        target_id=target_id,
        candidates=tuple(prepared),
    )


def run_upload_transaction(
    prepared: PreparedUploadClaims,
    operation: Callable[[], _T],
) -> _T:
    """Run and, on SQLite only, safely retry the complete database mutation."""

    deadline = time.monotonic() + _SQLITE_RETRY_DEADLINE_SECONDS
    for attempt in range(_SQLITE_RETRY_ATTEMPTS):
        try:
            with transaction.atomic(using=prepared.database_alias):
                _serialize_sqlite_claims(prepared.database_alias)
                return operation()
        except OperationalError as error:
            if not _is_sqlite_busy(error, prepared.database_alias):
                raise
            remaining = deadline - time.monotonic()
            if attempt + 1 >= _SQLITE_RETRY_ATTEMPTS or remaining <= 0:
                raise UploadStorageError from None
            delay = min(0.002 * (2**attempt), 0.02)
            jitter = delay * secrets.randbelow(1001) / 1000
            time.sleep(min(delay + jitter, remaining))
    raise AssertionError("unreachable")


def lock_upload_claims(prepared: PreparedUploadClaims) -> LockedUploadClaims:
    """Lock candidate intents in UUID order and revalidate their snapshots."""

    ids = sorted(item.intent_id for item in prepared.candidates)
    locked_rows = tuple(
        UploadIntent.objects.using(prepared.database_alias)
        .select_for_update()
        .filter(pk__in=ids)
        .order_by("id")
    )
    intents = {intent.id: intent for intent in locked_rows}
    if len(intents) != len(ids):
        raise UploadBindingMismatchError
    now = timezone.now()
    for item in prepared.candidates:
        intent = intents[item.intent_id]
        _validate_candidate_binding(
            intent,
            item.candidate,
            manager_name=prepared.manager_name,
            field_name=item.field_name,
            operation=prepared.operation,
            target_id=prepared.target_id,
            actor_id=prepared.actor_id,
            at=now,
        )
        if services._stored_object_version(intent) != item.version:
            raise UploadStorageChangedError
        current_adapter = services._resolve_intent_adapter(intent, item.model_field)
        if (
            not isinstance(current_adapter, UploadFinalizationAdapter)
            or current_adapter.adapter_id != item.adapter.adapter_id
            or current_adapter.adapter_version != item.adapter.adapter_version
        ):
            raise UploadStorageChangedError
    return LockedUploadClaims(prepared=prepared, intents=intents)


def reserve_upload_names(
    locked: LockedUploadClaims,
    instance: models.Model,
    values: Mapping[str, object],
) -> MutationPayload:
    """Replace candidates with reserved strings without assigning candidates."""

    result: MutationPayload = {}
    staging_prefix = get_file_upload_settings().staging_prefix
    for item in locked.prepared.candidates:
        intent = locked.intents[item.intent_id]
        old_value = getattr(instance, item.field_name, None)
        old_key = _field_name(old_value) or None
        if old_key != item.old_key:
            raise UploadBindingMismatchError
        qualified_name = _qualified_basename(item.candidate, intent.id)
        try:
            final_key = item.model_field.generate_filename(instance, qualified_name)
        except Exception:  # noqa: BLE001 - callable upload_to boundary fails closed
            raise UploadStorageError from None
        _validate_final_key(
            final_key,
            item.model_field,
            staging_prefix,
            intent_id=intent.id,
        )
        intent.final_key = final_key
        intent.old_key = old_key
        intent.old_object_version = (
            asdict(item.old_version) if item.old_version is not None else {}
        )
        result[item.field_name] = final_key
    return result


def mark_uploads_finalizing(
    locked: LockedUploadClaims,
    *,
    target_pk: object,
) -> None:
    """Durably claim intents and register materialization after commit."""

    for item in locked.prepared.candidates:
        intent = locked.intents[item.intent_id]
        intent.final_target_pk = str(target_pk)
        intent.state = UploadIntentState.FINALIZING.value
        intent.finalization_error_code = ""
        intent.verified_width = item.image_width
        intent.verified_height = item.image_height
        intent.save(
            using=locked.prepared.database_alias,
            update_fields=(
                "final_target_pk",
                "final_key",
                "old_key",
                "old_object_version",
                "state",
                "finalization_error_code",
                "verified_width",
                "verified_height",
                "updated_at",
            ),
        )
    for item in locked.prepared.candidates:
        transaction.on_commit(
            partial(
                finalize_upload_intent,
                item.intent_id,
                database_alias=locked.prepared.database_alias,
            ),
            using=locked.prepared.database_alias,
            robust=True,
        )


def finalize_upload_intent(
    intent_id: UUID,
    *,
    database_alias: str | None = None,
) -> None:
    """Idempotently materialize one durable ``FINALIZING`` intent."""

    alias = services._normalized_database_alias(
        database_alias or get_file_upload_settings().intent_database
    )
    try:
        initial = UploadIntent.objects.using(alias).get(pk=intent_id)
        if initial.state in {
            UploadIntentState.CONSUMED.value,
            UploadIntentState.SUPERSEDED.value,
        }:
            _retry_terminal_cleanup(initial, alias=alias)
            return
        if initial.state != UploadIntentState.FINALIZING.value:
            return
        if not _start_finalization_attempt(intent_id, alias=alias):
            current = UploadIntent.objects.using(alias).get(pk=intent_id)
            if current.state in {
                UploadIntentState.CONSUMED.value,
                UploadIntentState.SUPERSEDED.value,
            }:
                _retry_terminal_cleanup(current, alias=alias)
            return
        intent = UploadIntent.objects.using(alias).get(pk=intent_id)
        manager_class = services._resolve_manager(intent.manager_name)
        interface_cls = cast(OrmInterfaceClass, manager_class.Interface)
        model, model_field = services._resolve_file_field(
            cast(Any, interface_cls), intent.field_name
        )
        adapter = services._resolve_intent_adapter(intent, model_field)
        version = services._stored_object_version(intent)
        target_pk = _parse_target_pk(intent.final_target_pk, model)
        attempt_state = _begin_finalization_attempt(
            intent_id,
            alias=alias,
            model=model,
            model_field=model_field,
            target_pk=target_pk,
        )
        if attempt_state == "done":
            return
        if attempt_state == "superseded":
            _cleanup_superseded(adapter, intent, version)
            return
        actual_key = adapter.materialize(
            intent.staging_key,
            version,
            cast(str, intent.final_key),
            intent_id=intent.id,
        )
        _validate_final_key(
            actual_key,
            model_field,
            get_file_upload_settings().staging_prefix,
            intent_id=intent.id,
        )
        finalization_adapter = _require_finalization_adapter(
            adapter,
            actual_key=actual_key,
            reserved_key=intent.final_key,
        )
        final_version = finalization_adapter.inspect_materialized(
            actual_key,
            version,
            intent_id=intent.id,
        )
        _validate_materialized_version(final_version, version)
        consumed = _complete_finalization(
            intent_id,
            alias=alias,
            model=model,
            model_field=model_field,
            target_pk=target_pk,
            final_version=final_version,
        )
        intent.refresh_from_db(using=alias)
        if not consumed:
            _cleanup_superseded(adapter, intent, version)
            return
        _cleanup_consumed(
            adapter, intent, version, model, model_field, target_pk, alias
        )
    except Exception as error:  # noqa: BLE001 - callbacks never invalidate a commit
        _record_finalization_failure(intent_id, alias=alias, error=error)


def _validate_candidate_binding(
    intent: UploadIntent,
    candidate: UploadCandidate,
    *,
    manager_name: str,
    field_name: str,
    operation: UploadOperation,
    target_id: str | None,
    actor_id: object,
    at: datetime,
) -> None:
    if intent.state != UploadIntentState.UPLOADED.value:
        services._raise_for_unusable_state(intent)
    if intent.expires_at <= at:
        raise UploadExpiredError
    if (
        getattr(intent, "user_id", None) != actor_id
        or intent.manager_name != manager_name
        or intent.field_name != field_name
        or intent.operation != operation.value
        or intent.target_id != target_id
    ):
        raise UploadBindingMismatchError
    version = services._stored_object_version(intent)
    if (
        candidate.intent_id != intent.id
        or candidate.filename != intent.original_filename
        or candidate.size != version.size
        or candidate.content_type
        != (version.content_type or intent.declared_content_type)
        or candidate.checksum_sha256 != version.checksum_sha256
    ):
        raise UploadStorageChangedError


def _begin_finalization_attempt(
    intent_id: UUID,
    *,
    alias: str,
    model: type[models.Model],
    model_field: models.FileField,
    target_pk: object,
) -> str:
    with transaction.atomic(using=alias):
        intent = UploadIntent.objects.using(alias).select_for_update().get(pk=intent_id)
        if intent.state != UploadIntentState.FINALIZING.value:
            return "done"
        target = _locked_target(model, target_pk, alias)
        if (
            target is None
            or _field_name(getattr(target, model_field.name)) != intent.final_key
        ):
            intent.state = UploadIntentState.SUPERSEDED.value
            intent.save(using=alias, update_fields=("state", "updated_at"))
            return "superseded"
        intent.finalization_error_code = ""
        intent.save(
            using=alias,
            update_fields=(
                "finalization_error_code",
                "updated_at",
            ),
        )
        return "proceed"


def _start_finalization_attempt(intent_id: UUID, *, alias: str) -> bool:
    with transaction.atomic(using=alias):
        intent = UploadIntent.objects.using(alias).select_for_update().get(pk=intent_id)
        if intent.state != UploadIntentState.FINALIZING.value:
            return False
        intent.finalization_attempt_count += 1
        intent.save(
            using=alias,
            update_fields=("finalization_attempt_count", "updated_at"),
        )
        return True


def _complete_finalization(
    intent_id: UUID,
    *,
    alias: str,
    model: type[models.Model],
    model_field: models.FileField,
    target_pk: object,
    final_version: ObjectVersion,
) -> bool:
    with transaction.atomic(using=alias):
        intent = UploadIntent.objects.using(alias).select_for_update().get(pk=intent_id)
        if intent.state == UploadIntentState.CONSUMED.value:
            return True
        if intent.state != UploadIntentState.FINALIZING.value:
            return False
        target = _locked_target(model, target_pk, alias)
        if (
            target is None
            or _field_name(getattr(target, model_field.name)) != intent.final_key
        ):
            intent.state = UploadIntentState.SUPERSEDED.value
            intent.save(using=alias, update_fields=("state", "updated_at"))
            return False
        intent.state = UploadIntentState.CONSUMED.value
        intent.consumed_at = timezone.now()
        intent.finalization_error_code = ""
        intent.final_object_version = asdict(final_version)
        intent.save(
            using=alias,
            update_fields=(
                "state",
                "consumed_at",
                "finalization_error_code",
                "final_object_version",
                "updated_at",
            ),
        )
        return True


def _cleanup_consumed(
    adapter: UploadAdapter,
    intent: UploadIntent,
    version: ObjectVersion,
    model: type[models.Model],
    model_field: models.FileField,
    target_pk: object,
    alias: str,
) -> None:
    try:
        adapter.delete_stage(intent.staging_key, version)
    except Exception as error:  # noqa: BLE001 - retained bytes are safer
        del error
    if not intent.old_key:
        return
    if intent.old_cleanup_completed_at is not None:
        return
    try:
        target = cast(Any, model)._base_manager.using(alias).get(pk=target_pk)
        if _field_name(getattr(target, model_field.name)) != intent.final_key:
            return
        if not isinstance(adapter, UploadFinalizationAdapter):
            return
        planned = _plan_old_cleanup_claim(
            adapter,
            intent.id,
            alias=alias,
            allow_new=get_file_upload_settings().delete_replaced_files,
        )
        if planned is None:
            return
        old_key, claimed = planned
        adapter.claim_replaced_object(
            old_key,
            claimed,
            cleanup_id=intent.id,
        )
        adapter.delete_claimed_object(claimed, cleanup_id=intent.id)
        _mark_old_cleanup_completed(intent.id, alias=alias)
    except Exception as error:  # noqa: BLE001 - retain old files on uncertainty
        del error


def _claimed_old_object(intent: UploadIntent) -> ClaimedObject | None:
    if not intent.old_cleanup_key:
        return None
    version = _version_from_metadata(intent.old_cleanup_version)
    if version is None:
        raise UploadStorageChangedError
    return ClaimedObject(key=intent.old_cleanup_key, version=version)


def _plan_old_cleanup_claim(
    adapter: UploadFinalizationAdapter,
    intent_id: UUID,
    *,
    alias: str,
    allow_new: bool,
) -> tuple[str, ClaimedObject] | None:
    with transaction.atomic(using=alias):
        intent = UploadIntent.objects.using(alias).select_for_update().get(pk=intent_id)
        if intent.state != UploadIntentState.CONSUMED.value:
            raise UploadStorageChangedError
        if intent.old_cleanup_completed_at is not None or not intent.old_key:
            return None
        existing = _claimed_old_object(intent)
        if existing is not None:
            return intent.old_key, existing
        if not allow_new:
            return None
        old_version = _version_from_metadata(intent.old_object_version)
        if old_version is None:
            return None
        claimed = adapter.plan_replaced_object_claim(
            intent.old_key,
            old_version,
            cleanup_id=intent.id,
        )
        intent.old_cleanup_key = claimed.key
        intent.old_cleanup_version = asdict(claimed.version)
        intent.save(
            using=alias,
            update_fields=(
                "old_cleanup_key",
                "old_cleanup_version",
                "updated_at",
            ),
        )
        return intent.old_key, claimed


def _mark_old_cleanup_completed(intent_id: UUID, *, alias: str) -> None:
    UploadIntent.objects.using(alias).filter(
        pk=intent_id,
        state=UploadIntentState.CONSUMED.value,
        old_cleanup_key__isnull=False,
        old_cleanup_completed_at__isnull=True,
    ).update(
        old_cleanup_completed_at=timezone.now(),
        updated_at=timezone.now(),
    )


def _cleanup_superseded(
    adapter: UploadAdapter,
    intent: UploadIntent,
    version: ObjectVersion,
) -> None:
    try:
        adapter.delete_stage(intent.staging_key, version)
    except Exception as error:  # noqa: BLE001 - retain unverified objects
        del error
    if intent.final_key and isinstance(adapter, UploadFinalizationAdapter):
        try:
            final_version = _version_from_metadata(intent.final_object_version)
            if final_version is None:
                final_version = adapter.inspect_materialized(
                    intent.final_key,
                    version,
                    intent_id=intent.id,
                )
            adapter.delete_materialized(
                intent.final_key,
                final_version,
                intent_id=intent.id,
            )
        except Exception as error:  # noqa: BLE001 - uncertain ownership means retain
            del error


def _retry_terminal_cleanup(intent: UploadIntent, *, alias: str) -> None:
    manager_class = services._resolve_manager(intent.manager_name)
    interface_cls = cast(OrmInterfaceClass, manager_class.Interface)
    model, model_field = services._resolve_file_field(
        cast(Any, interface_cls), intent.field_name
    )
    adapter = services._resolve_intent_adapter(intent, model_field)
    source_version = services._stored_object_version(intent)
    if intent.state == UploadIntentState.SUPERSEDED.value:
        _cleanup_superseded(adapter, intent, source_version)
        return
    if intent.state != UploadIntentState.CONSUMED.value:
        return
    target_pk = _parse_target_pk(intent.final_target_pk, model)
    _cleanup_consumed(
        adapter,
        intent,
        source_version,
        model,
        model_field,
        target_pk,
        alias,
    )


def _record_finalization_failure(
    intent_id: UUID,
    *,
    alias: str,
    error: Exception,
) -> None:
    code = (
        stable_upload_error(error).code
        if isinstance(error, UploadError)
        else UploadStorageError.code
    )
    try:
        UploadIntent.objects.using(alias).filter(
            pk=intent_id,
            state=UploadIntentState.FINALIZING.value,
        ).update(finalization_error_code=code, updated_at=timezone.now())
    except Exception as persistence_error:  # noqa: BLE001 - callback stays robust
        del persistence_error


def _database_alias(interface_cls: OrmInterfaceClass) -> str:
    value = getattr(interface_cls, "database", None)
    return value if isinstance(value, str) and value else DEFAULT_DB_ALIAS


def _manager_name(interface_cls: OrmInterfaceClass) -> str:
    manager = getattr(interface_cls, "_parent_class", None)
    name = getattr(manager, "__name__", None)
    if not isinstance(name, str) or not name:
        raise UploadBindingMismatchError
    return name


def _target_identifier(
    operation: UploadOperation, target_pk: object | None
) -> str | None:
    if operation is UploadOperation.CREATE:
        if target_pk is not None:
            raise UploadBindingMismatchError
        return None
    if target_pk is None:
        raise UploadBindingMismatchError
    return serialize_dependency_identifier({"id": target_pk})


def _file_field(interface_cls: OrmInterfaceClass, name: str) -> models.FileField:
    _model, field = services._resolve_file_field(cast(Any, interface_cls), name)
    return field


def _qualified_basename(candidate: UploadCandidate, intent_id: UUID) -> str:
    stem, suffix = os.path.splitext(candidate.filename)
    available = max(1, 255 - len(suffix) - len(intent_id.hex) - 2)
    return f"{stem[:available]}__{intent_id.hex}{suffix}"


def _inspect_old_object(
    model_field: models.FileField,
    adapter: UploadAdapter,
    *,
    operation: UploadOperation,
    target_pk: object | None,
    database_alias: str,
) -> tuple[str | None, ObjectVersion | None]:
    if operation is UploadOperation.CREATE:
        return None, None
    if target_pk is None:
        raise UploadBindingMismatchError
    try:
        model = cast(Any, model_field.model)
        target = model._base_manager.using(database_alias).get(pk=target_pk)
    except model_field.model.DoesNotExist:
        raise UploadBindingMismatchError from None
    old_key = _field_name(getattr(target, model_field.name)) or None
    if not old_key or not get_file_upload_settings().delete_replaced_files:
        return old_key, None
    try:
        if not isinstance(adapter, UploadFinalizationAdapter):
            return old_key, None
        old_version = adapter.inspect_replaced_object(old_key)
    except Exception:  # noqa: BLE001 - replacement deletion is optional and fail-closed
        return old_key, None
    return old_key, old_version if _object_version_is_safe(old_version) else None


def _validate_final_key(
    key: object,
    model_field: models.FileField,
    staging_prefix: str,
    *,
    intent_id: UUID,
) -> None:
    if not isinstance(key, str) or not key or key != unicodedata.normalize("NFC", key):
        raise UploadStorageChangedError
    max_length = model_field.max_length
    limit = min(1024, max_length) if isinstance(max_length, int) else 1024
    parts = key.split("/")
    if (
        len(key) > limit
        or key.startswith(("/", "\\"))
        or "\\" in key
        or any(part in {"", ".", ".."} for part in parts)
        or any(unicodedata.category(char).startswith("C") for char in key)
        or key == staging_prefix.rstrip("/")
        or key.startswith(staging_prefix)
        or key.startswith(_FRAMEWORK_STORAGE_PREFIXES)
        or intent_id.hex not in key
    ):
        raise UploadStorageChangedError


def _version_from_metadata(value: object) -> ObjectVersion | None:
    if not isinstance(value, Mapping) or not value:
        return None
    if set(value) != {
        "version_id",
        "etag",
        "checksum_sha256",
        "size",
        "content_type",
    }:
        return None
    try:
        version = ObjectVersion(
            version_id=cast(str | None, value.get("version_id")),
            etag=cast(str | None, value.get("etag")),
            checksum_sha256=cast(str, value.get("checksum_sha256")),
            size=cast(int, value.get("size")),
            content_type=cast(str | None, value.get("content_type")),
        )
    except (TypeError, ValueError):
        return None
    return version if _object_version_is_safe(version) else None


def _require_finalization_adapter(
    adapter: UploadAdapter,
    *,
    actual_key: str,
    reserved_key: str | None,
) -> UploadFinalizationAdapter:
    if actual_key != reserved_key or not isinstance(adapter, UploadFinalizationAdapter):
        raise UploadStorageChangedError
    return adapter


def _validate_materialized_version(
    final_version: ObjectVersion,
    source_version: ObjectVersion,
) -> None:
    if not _object_version_is_safe(final_version) or (
        final_version.checksum_sha256 != source_version.checksum_sha256
        or final_version.size != source_version.size
        or final_version.content_type != source_version.content_type
    ):
        raise UploadStorageChangedError


def _object_version_is_safe(value: object) -> bool:
    if type(value) is not ObjectVersion:
        return False
    version = value
    if (
        isinstance(version.size, bool)
        or not isinstance(version.size, int)
        or version.size < 0
        or not isinstance(version.checksum_sha256, str)
        or _HEX_SHA256.fullmatch(version.checksum_sha256) is None
    ):
        return False
    for identity in (version.version_id, version.etag):
        if identity is not None and (
            not isinstance(identity, str)
            or not identity
            or len(identity) > 1024
            or any(unicodedata.category(char).startswith("C") for char in identity)
        ):
            return False
    content_type = version.content_type
    return content_type is None or (
        isinstance(content_type, str)
        and 0 < len(content_type) <= 255
        and not any(unicodedata.category(char).startswith("C") for char in content_type)
    )


def _validate_staged_content(
    adapter: UploadAdapter,
    model_field: models.FileField,
    staging_key: str,
    version: ObjectVersion,
    *,
    policy: FileUploadPolicy,
    original_filename: str,
    max_image_pixels: int,
    max_image_width: int,
    max_image_height: int,
    max_inspection_bytes: int,
) -> tuple[int | None, int | None]:
    if not isinstance(model_field, models.ImageField):
        _inspect_file_content(
            adapter,
            staging_key,
            version,
            policy=policy,
            original_filename=original_filename,
            max_inspection_bytes=max_inspection_bytes,
        )
        return None, None
    try:
        from PIL import Image, UnidentifiedImageError

        try:
            staged_object = adapter.open_stage(staging_key, version)
        except UploadError:
            raise
        except Exception:  # noqa: BLE001 - custom adapter boundary fails closed
            raise UploadStorageError from None
        with staged_object as staged:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = Image.open(staged)
                width, height = image.size
                actual_content_type = Image.MIME.get(image.format or "")
                if actual_content_type != version.content_type:
                    raise InvalidFileTypeError
                if (
                    width <= 0
                    or height <= 0
                    or width > max_image_width
                    or height > max_image_height
                    or width * height > max_image_pixels
                ):
                    _raise_invalid_image()
                image.verify()
                return width, height
    except InvalidImageError:
        raise
    except UploadError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        ValueError,
        Warning,
    ):
        raise InvalidImageError from None


def _inspect_file_content(
    adapter: UploadAdapter,
    staging_key: str,
    version: ObjectVersion,
    *,
    policy: FileUploadPolicy,
    original_filename: str,
    max_inspection_bytes: int,
) -> None:
    inspector = policy.content_inspector
    if inspector is None:
        if policy.allowed_content_types is not None:
            raise UploadBackendUnsupportedError
        return
    try:
        with adapter.open_stage(staging_key, version) as staged:
            inspected = _read_bounded_prefix(
                staged,
                limit=max_inspection_bytes,
                expected_size=version.size,
            )
    except UploadError:
        raise
    except Exception:  # noqa: BLE001 - adapter read boundary fails closed
        raise UploadStorageError from None
    context = FileInspection(
        content=inspected[:max_inspection_bytes],
        size=version.size,
        original_filename=original_filename,
        declared_content_type=version.content_type or "",
        truncated=version.size > max_inspection_bytes,
    )
    try:
        detected = inspector(context)
    except (InvalidFileTypeError, InvalidImageError):
        raise
    except Exception:  # noqa: BLE001 - project inspector boundary fails closed
        raise UploadStorageError from None
    if not isinstance(detected, str):
        raise InvalidFileTypeError
    detected = services._normalize_content_type(detected, policy)
    if detected != version.content_type:
        raise InvalidFileTypeError


def _read_bounded_prefix(
    staged: IO[bytes],
    *,
    limit: int,
    expected_size: int,
) -> bytes:
    target = min(expected_size, limit + 1)
    chunks: list[bytes] = []
    received = 0
    while received < target:
        chunk = staged.read(target - received)
        if not isinstance(chunk, bytes):
            raise UploadStorageError
        if not chunk:
            raise UploadStorageChangedError
        chunks.append(chunk)
        received += len(chunk)
    return b"".join(chunks)


def _raise_invalid_image() -> None:
    raise InvalidImageError


def _reject_content_intent(
    intent: UploadIntent,
    error: UploadError,
    *,
    database_alias: str,
) -> None:
    public_error = stable_upload_error(error)
    try:
        UploadIntent.objects.using(database_alias).filter(
            pk=intent.pk,
            state=UploadIntentState.UPLOADED.value,
            user_id=getattr(intent, "user_id", None),
            manager_name=intent.manager_name,
            field_name=intent.field_name,
            operation=intent.operation,
            target_id=intent.target_id,
            object_version=intent.object_version,
        ).update(
            state=UploadIntentState.REJECTED.value,
            finalization_error_code=public_error.code,
            updated_at=timezone.now(),
        )
    except Exception:  # noqa: BLE001 - content rejection persistence fails closed
        raise UploadStorageError from None


def _field_name(value: object) -> str:
    name = getattr(value, "name", value)
    return name if isinstance(name, str) else ""


def _has_unsafe_sqlite_outer_atomic(alias: str) -> bool:
    connection = connections[alias]
    if connection.vendor != "sqlite":
        return False
    return any(
        not getattr(block, "_from_testcase", False)
        for block in connection.atomic_blocks
    )


def _parse_target_pk(value: str | None, model: type[models.Model]) -> object:
    if not isinstance(value, str):
        raise UploadStorageChangedError
    try:
        pk_field = model._meta.pk
        if pk_field is None:
            raise UploadStorageChangedError
        return pk_field.to_python(value)
    except (TypeError, ValueError):
        raise UploadStorageChangedError from None


def _locked_target(
    model: type[models.Model],
    target_pk: object,
    alias: str,
) -> models.Model | None:
    try:
        manager = cast(Any, model)._base_manager
        return cast(
            models.Model,
            manager.using(alias).select_for_update().get(pk=target_pk),
        )
    except model.DoesNotExist:
        return None


def _serialize_sqlite_claims(alias: str) -> None:
    if connections[alias].vendor != "sqlite":
        return
    manager = UploadQuotaLock.objects.using(alias)
    manager.get_or_create(pk=1, defaults={"generation": 0})
    manager.filter(pk=1).update(generation=models.F("generation") + 1)


def _is_sqlite_busy(error: OperationalError, alias: str) -> bool:
    if connections[alias].vendor != "sqlite":
        return False
    code = getattr(error.__cause__, "sqlite_errorcode", None)
    if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return True
    return "locked" in str(error).casefold() or "busy" in str(error).casefold()
