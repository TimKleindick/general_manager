"""Creation service for durable, field-bound upload intents."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import inspect
import ntpath
import re
import secrets
import sqlite3
import time
import unicodedata
from typing import Protocol, cast
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID, uuid4

from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.cache import cache, caches
from django.core.cache.backends.base import BaseCache
from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from django.core import signing
from django.core.signing import BadSignature
from django.db import (
    DEFAULT_DB_ALIAS,
    DatabaseError,
    OperationalError,
    connections,
    models,
    transaction,
)
from django.utils import timezone

from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.uploads.adapters import (
    UploadAdapter,
    UploadAdapterRegistry,
    UploadInstructions,
)
from general_manager.uploads.config import (
    FileUploadPolicy,
    FileUploadSettings,
    get_file_upload_settings,
    merge_file_upload_policy,
)
from general_manager.uploads.errors import (
    InvalidFileTypeError,
    InvalidUploadChecksumError,
    InvalidUploadFilenameError,
    InvalidUploadSizeError,
    UploadAlreadyConsumedError,
    UploadError,
    UploadAuthenticationError,
    UploadBackendUnsupportedError,
    UploadBindingMismatchError,
    UploadDatabaseMismatchError,
    UploadExpiredError,
    UploadFieldInvalidError,
    UploadIncompleteError,
    UploadManagerInvalidError,
    UploadOperationInvalidError,
    UploadQuotaExceededError,
    UploadRateLimitExceededError,
    UploadChecksumMismatchError,
    UploadSizeMismatchError,
    UploadStorageError,
    UploadStorageChangedError,
    UploadSupersededError,
    UploadTargetUnavailableError,
    UploadTokenInvalidError,
    stable_upload_error,
)
from general_manager.uploads.models import UploadIntent, UploadQuotaLock
from general_manager.uploads.tokens import issue_upload_token
from general_manager.uploads.types import (
    ChecksumAlgorithm,
    ObjectVersion,
    UploadCandidate,
    UploadIntentState,
    UploadOperation,
    UploadTransport,
)


_MAX_FILENAME_LENGTH = 255
_CONTENT_TYPE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)
_HEX_SHA256 = re.compile(r"^[0-9A-Fa-f]{64}$")
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_ADAPTER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STORAGE_FINGERPRINT = re.compile(r"^[!-~]{1,255}$")
_EXPIRING_QUOTA_STATES = (
    UploadIntentState.PENDING.value,
    UploadIntentState.TRANSFERRING.value,
    UploadIntentState.UPLOADED.value,
)
_TRANSFER_CREDENTIAL_SALT = "general_manager.uploads.transfer"
_SQLITE_BUSY_RETRY_ATTEMPTS = 6
_SQLITE_BUSY_RETRY_DEADLINE_SECONDS = 0.25
_SQLITE_BUSY_BASE_DELAY_SECONDS = 0.002
_SQLITE_BUSY_MAX_DELAY_SECONDS = 0.02


@dataclass(frozen=True, slots=True)
class UploadChecksum:
    """Client-declared checksum metadata for one upload."""

    algorithm: ChecksumAlgorithm | str
    digest: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BeginFileUploadRequest:
    """Storage-independent inputs accepted by :func:`begin_file_upload`."""

    manager: str
    field: str
    operation: UploadOperation | str
    filename: str
    size: int
    content_type: str
    checksum: UploadChecksum | Mapping[str, object]
    object_id: object | None = None


@dataclass(frozen=True, slots=True)
class BeginFileUploadResult:
    """One-time token and client-safe transfer instructions."""

    intent_id: UUID
    token: str = field(repr=False)
    instructions: UploadInstructions
    expires_at: datetime


class _BeginRequest(Protocol):
    manager: object
    field: object
    operation: object
    object_id: object
    filename: object
    size: object
    content_type: object
    checksum: object


class _UploadInterface(Protocol):
    _model: type[models.Model]

    @classmethod
    def get_attribute_types(cls) -> Mapping[str, Mapping[str, object]]: ...


type BeginUploadRateLimitHook = Callable[[object, object], object | None]


_begin_upload_rate_limit_hook: BeginUploadRateLimitHook | None = None
upload_adapter_registry = UploadAdapterRegistry()


def preflight_upload_tokens(
    *,
    user: object,
    manager_class: type[GeneralManager],
    operation: UploadOperation,
    target_id: object | None,
    file_field_names: tuple[str, ...],
    values: MutableMapping[str, object],
) -> None:
    """Replace provided file tokens with redacted, field-bound candidates.

    The input mapping is scrubbed of raw file tokens before database or storage
    work begins. On success the corresponding keys are restored as immutable
    :class:`UploadCandidate` values. On failure no candidate is installed and
    only a fresh framework-owned upload error crosses this boundary.
    """

    token_fields = tuple(
        name
        for name in sorted(set(file_field_names))
        if name in values and values[name] is not None
    )
    raw_tokens = [(name, values.pop(name)) for name in token_fields]
    if not raw_tokens:
        return

    failure: UploadError | None = None
    candidates: dict[str, UploadCandidate] | None = None
    try:
        candidates = _preflight_upload_tokens(
            user=user,
            manager_class=manager_class,
            operation=operation,
            target_id=target_id,
            raw_tokens=raw_tokens,
        )
    except UploadError as error:
        failure = stable_upload_error(error)
    except Exception:  # noqa: BLE001 - public boundary fails closed
        failure = UploadStorageError()

    # Raw tokens exist only in this service frame. Explicitly discard the
    # references before raising a traceback-free public error.
    raw_tokens.clear()
    if failure is not None:
        failure.__cause__ = None
        failure.__context__ = None
        failure.__traceback__ = None
        failure.__suppress_context__ = True
        raise failure
    if candidates is None:  # pragma: no cover - success always assigns it
        raise AssertionError("unreachable")
    values.update(candidates)


@dataclass(frozen=True, slots=True)
class _PreflightPlan:
    field_name: str
    intent: UploadIntent
    token: str = field(repr=False)
    version: ObjectVersion
    direct: bool


def _preflight_upload_tokens(
    *,
    user: object,
    manager_class: type[GeneralManager],
    operation: UploadOperation,
    target_id: object | None,
    raw_tokens: list[tuple[str, object]],
) -> dict[str, UploadCandidate]:
    settings = get_file_upload_settings()
    if not settings.enabled:
        raise UploadBackendUnsupportedError
    try:
        registered_manager = _resolve_manager(manager_class.__name__)
    except UploadError:
        raise UploadBindingMismatchError from None
    if registered_manager is not manager_class:
        raise UploadBindingMismatchError
    owner_pk = _authenticated_user_pk(user)
    interface = cast(_UploadInterface, manager_class.Interface)
    database_alias = _normalized_database_alias(getattr(interface, "database", None))
    intent_database_alias = _normalized_database_alias(settings.intent_database)
    if database_alias != intent_database_alias:
        raise UploadDatabaseMismatchError

    canonical_target = _canonical_target_id(
        manager_class,
        operation=operation,
        target_id=target_id,
    )
    plans: list[_PreflightPlan] = []
    now = timezone.now()
    for field_name, raw_token in raw_tokens:
        if not isinstance(raw_token, str) or not raw_token:
            raise UploadTokenInvalidError
        intent = _intent_for_token(
            raw_token,
            database_alias=intent_database_alias,
        )
        _validate_intent_binding(
            intent,
            token=raw_token,
            owner_pk=owner_pk,
            manager_name=manager_class.__name__,
            field_name=field_name,
            operation=operation,
            target_id=canonical_target,
            at=now,
        )
        _model, model_field = _resolve_file_field(interface, field_name)
        adapter = _resolve_intent_adapter(intent, model_field)
        if intent.state == UploadIntentState.UPLOADED.value:
            version = _stored_object_version(intent)
            direct = False
        elif intent.state == UploadIntentState.PENDING.value:
            try:
                supports_direct = type(adapter).supports_direct(model_field.storage)
            except Exception:  # noqa: BLE001 - custom adapter boundary fails closed
                raise UploadStorageChangedError from None
            if supports_direct is not True:
                raise UploadIncompleteError
            try:
                inspected = adapter.inspect_staged(intent.staging_key)
            except (FileNotFoundError, ObjectDoesNotExist):
                raise UploadIncompleteError from None
            except UploadError:
                raise
            except Exception:  # noqa: BLE001 - untrusted adapter boundary
                raise UploadStorageError from None
            version = _validate_preflight_version(inspected, intent)
            direct = True
        else:
            _raise_for_unusable_state(intent)
        plans.append(
            _PreflightPlan(
                field_name=field_name,
                intent=intent,
                token=raw_token,
                version=version,
                direct=direct,
            )
        )

    candidates = {plan.field_name: _candidate_from_plan(plan) for plan in plans}
    if any(plan.direct for plan in plans):
        _persist_direct_preflight(
            plans,
            owner_pk=owner_pk,
            manager_name=manager_class.__name__,
            operation=operation,
            target_id=canonical_target,
            database_alias=intent_database_alias,
        )

    return candidates


def _candidate_from_plan(plan: _PreflightPlan) -> UploadCandidate:
    try:
        filename = _normalize_filename(plan.intent.original_filename)
    except UploadError:
        raise UploadStorageChangedError from None
    if filename != plan.intent.original_filename:
        raise UploadStorageChangedError
    return UploadCandidate(
        intent_id=plan.intent.id,
        filename=filename,
        size=plan.version.size,
        content_type=plan.version.content_type or plan.intent.declared_content_type,
        checksum_sha256=plan.version.checksum_sha256,
    )


def _canonical_target_id(
    manager_class: type[GeneralManager],
    *,
    operation: UploadOperation,
    target_id: object | None,
) -> str | None:
    if operation is UploadOperation.CREATE:
        if target_id is not None:
            raise UploadBindingMismatchError
        return None
    if operation is not UploadOperation.UPDATE or target_id is None:
        raise UploadBindingMismatchError
    try:
        target = manager_class(id=target_id)
        return serialize_dependency_identifier(target.identification)
    except (ObjectDoesNotExist, AttributeError, KeyError, TypeError, ValueError):
        raise UploadBindingMismatchError from None


def _intent_for_token(token: str, *, database_alias: str) -> UploadIntent:
    from general_manager.uploads.tokens import digest_upload_token

    digest = digest_upload_token(token)
    try:
        intent = UploadIntent.objects.using(database_alias).get(token_digest=digest)
    except UploadIntent.DoesNotExist:
        raise UploadTokenInvalidError from None
    except (DatabaseError, UploadIntent.MultipleObjectsReturned):
        raise UploadStorageError from None
    if not intent.matches_token(token):
        raise UploadTokenInvalidError
    return intent


def _validate_intent_binding(
    intent: UploadIntent,
    *,
    token: str,
    owner_pk: object,
    manager_name: str,
    field_name: str,
    operation: UploadOperation,
    target_id: str | None,
    at: datetime,
) -> None:
    if not intent.matches_token(token) or getattr(intent, "user_id", None) != owner_pk:
        raise UploadTokenInvalidError
    if intent.expires_at <= at or intent.state == UploadIntentState.EXPIRED.value:
        raise UploadExpiredError
    if (
        intent.manager_name != manager_name
        or intent.field_name != field_name
        or intent.operation != operation.value
        or intent.target_id != target_id
    ):
        raise UploadBindingMismatchError


def _raise_for_unusable_state(intent: UploadIntent) -> None:
    if intent.state in {
        UploadIntentState.FINALIZING.value,
        UploadIntentState.CONSUMED.value,
    }:
        raise UploadAlreadyConsumedError
    if intent.state == UploadIntentState.SUPERSEDED.value:
        raise UploadSupersededError
    if intent.state == UploadIntentState.EXPIRED.value:
        raise UploadExpiredError
    if intent.state in {
        UploadIntentState.PENDING.value,
        UploadIntentState.TRANSFERRING.value,
    }:
        raise UploadIncompleteError
    raise UploadTokenInvalidError


def _resolve_intent_adapter(
    intent: UploadIntent,
    model_field: models.FileField,
) -> UploadAdapter:
    try:
        adapter_version = int(intent.adapter_version)
        adapter = upload_adapter_registry.resolve_by_id(
            intent.adapter_id,
            adapter_version,
            model_field.storage,
        )
        current_identity = (
            _validate_adapter_identity(adapter) if adapter is not None else None
        )
        fingerprint = adapter.storage_fingerprint() if adapter is not None else None
        if fingerprint is not None:
            _validate_storage_fingerprint(fingerprint)
    except Exception:  # noqa: BLE001 - custom adapter boundary fails closed
        raise UploadStorageChangedError from None
    if (
        adapter is None
        or str(adapter_version) != intent.adapter_version
        or current_identity != (intent.adapter_id, adapter_version)
        or fingerprint != intent.storage_fingerprint
    ):
        raise UploadStorageChangedError
    return adapter


def _stored_object_version(intent: UploadIntent) -> ObjectVersion:
    metadata = intent.object_version
    if not isinstance(metadata, Mapping) or set(metadata) != {
        "version_id",
        "etag",
        "checksum_sha256",
        "size",
        "content_type",
    }:
        raise UploadStorageChangedError
    try:
        version = ObjectVersion(
            version_id=cast(str | None, metadata.get("version_id")),
            etag=cast(str | None, metadata.get("etag")),
            checksum_sha256=cast(str, metadata.get("checksum_sha256")),
            size=cast(int, metadata.get("size")),
            content_type=cast(str | None, metadata.get("content_type")),
        )
    except (TypeError, ValueError):
        raise UploadStorageChangedError from None
    version = _validate_preflight_version(version, intent)
    if (
        intent.verified_size != version.size
        or intent.verified_content_type != version.content_type
        or intent.verified_checksum_sha256 != version.checksum_sha256
    ):
        raise UploadStorageChangedError
    return version


def _validate_preflight_version(
    version: object,
    intent: UploadIntent,
) -> ObjectVersion:
    if type(version) is not ObjectVersion:
        raise UploadStorageChangedError
    typed = version
    for identity in (typed.version_id, typed.etag):
        if identity is not None and (
            not isinstance(identity, str)
            or not identity
            or len(identity) > 1024
            or any(
                ord(character) < 32 or ord(character) == 127 for character in identity
            )
        ):
            raise UploadStorageChangedError
    if typed.size != intent.declared_size:
        raise UploadSizeMismatchError
    if typed.checksum_sha256 != intent.declared_checksum_sha256:
        raise UploadChecksumMismatchError
    if typed.content_type != intent.declared_content_type:
        raise InvalidFileTypeError
    return typed


def _persist_direct_preflight(
    plans: list[_PreflightPlan],
    *,
    owner_pk: object,
    manager_name: str,
    operation: UploadOperation,
    target_id: str | None,
    database_alias: str,
) -> None:
    now = timezone.now()
    with transaction.atomic(using=database_alias):
        locked = {
            intent.id: intent
            for intent in UploadIntent.objects.using(database_alias)
            .select_for_update()
            .filter(id__in=sorted(plan.intent.id for plan in plans))
            .order_by("id")
        }
        if len(locked) != len({plan.intent.id for plan in plans}):
            raise UploadTokenInvalidError
        for plan in plans:
            current = locked[plan.intent.id]
            _validate_intent_binding(
                current,
                token=plan.token,
                owner_pk=owner_pk,
                manager_name=manager_name,
                field_name=plan.field_name,
                operation=operation,
                target_id=target_id,
                at=now,
            )
            if plan.direct:
                if current.state == UploadIntentState.UPLOADED.value:
                    if _stored_object_version(current) != plan.version:
                        raise UploadStorageChangedError
                    continue
                if current.state != UploadIntentState.PENDING.value:
                    _raise_for_unusable_state(current)
                metadata = {
                    "version_id": plan.version.version_id,
                    "etag": plan.version.etag,
                    "checksum_sha256": plan.version.checksum_sha256,
                    "size": plan.version.size,
                    "content_type": plan.version.content_type,
                }
                current.state = UploadIntentState.UPLOADED.value
                current.verified_size = plan.version.size
                current.verified_content_type = plan.version.content_type
                current.verified_checksum_sha256 = plan.version.checksum_sha256
                current.object_version = metadata
                current.uploaded_at = now
                current.save(
                    using=database_alias,
                    update_fields=(
                        "state",
                        "verified_size",
                        "verified_content_type",
                        "verified_checksum_sha256",
                        "object_version",
                        "uploaded_at",
                        "updated_at",
                    ),
                )
            else:
                if current.state != UploadIntentState.UPLOADED.value:
                    _raise_for_unusable_state(current)
                if _stored_object_version(current) != plan.version:
                    raise UploadStorageChangedError


def set_begin_upload_rate_limit_hook(
    hook: BeginUploadRateLimitHook | None,
) -> BeginUploadRateLimitHook | None:
    """Install a process-local rate-limit hook and return the previous hook.

    Hooks should raise :class:`UploadRateLimitExceededError` when admission is
    denied. Returning a truthy value is also treated as denial so cache-backed
    limiters can expose a lightweight boolean contract.
    """

    global _begin_upload_rate_limit_hook
    previous = _begin_upload_rate_limit_hook
    _begin_upload_rate_limit_hook = hook
    return previous


def _rate_limit_key(scope: str) -> str:
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    return f"general_manager:upload_begin:{digest}"


def _increment_rate_limit_counter(key: str, *, window_seconds: int) -> int:
    try:
        if cache.add(key, 1, timeout=window_seconds):
            return 1
        try:
            return int(cache.incr(key, 1))
        except ValueError:
            if cache.add(key, 1, timeout=window_seconds):
                return 1
            return int(cache.incr(key, 1))
    except Exception as exc:
        raise UploadStorageError from exc


def _enforce_default_begin_rate_limit(
    user: object,
    settings: FileUploadSettings,
) -> None:
    try:
        backend = caches["default"]
    except Exception as exc:
        raise UploadStorageError from exc
    if type(backend).incr is BaseCache.incr:
        raise UploadStorageError
    global_total = _increment_rate_limit_counter(
        _rate_limit_key("global"),
        window_seconds=settings.begin_rate_limit_window_seconds,
    )
    if global_total > settings.max_begin_attempts_global:
        raise UploadRateLimitExceededError

    owner_pk = getattr(user, "pk", None)
    if getattr(user, "is_authenticated", False) is not True or owner_pk is None:
        return
    user_total = _increment_rate_limit_counter(
        _rate_limit_key(f"user:{owner_pk}"),
        window_seconds=settings.begin_rate_limit_window_seconds,
    )
    if user_total > settings.max_begin_attempts_per_user:
        raise UploadRateLimitExceededError


def issue_upload_transfer_credential(
    *,
    intent_id: UUID,
    owner_pk: object,
    adapter_id: str,
) -> str:
    """Return a timestamped transfer credential bound to one intent and owner."""

    return signing.dumps(
        {
            "intent": str(intent_id),
            "owner": str(owner_pk),
            "adapter": adapter_id,
        },
        salt=_TRANSFER_CREDENTIAL_SALT,
    )


def verify_upload_transfer_credential(
    credential: object,
    *,
    intent_id: UUID,
    owner_pk: object,
    adapter_id: str,
    max_age: int,
) -> bool:
    """Verify one transfer credential without raising or exposing its payload."""

    if not isinstance(credential, str) or not credential or max_age <= 0:
        return False
    try:
        payload = cast(
            object,
            signing.loads(
                credential,
                salt=_TRANSFER_CREDENTIAL_SALT,
                max_age=max_age,
            ),
        )
    except (BadSignature, TypeError, ValueError):
        return False
    return payload == {
        "intent": str(intent_id),
        "owner": str(owner_pk),
        "adapter": adapter_id,
    }


def begin_file_upload(user: object, request: object) -> BeginFileUploadResult:
    """Create an upload intent without exposing internal exception chains."""

    failure: UploadError | None = None
    try:
        return _begin_file_upload(user, request)
    except UploadError as error:
        # Public upload errors can originate while handling user, cache, database,
        # or adapter exceptions. Detach every underlying exception before the
        # stable error crosses the service boundary.
        failure = stable_upload_error(error)
        failure.__cause__ = None
        failure.__context__ = None
        failure.__traceback__ = None
        failure.__suppress_context__ = True

    if failure is None:  # pragma: no cover - the except branch always assigns it
        raise AssertionError("unreachable")
    raise failure


def _begin_file_upload(user: object, request: object) -> BeginFileUploadResult:
    """Validate and durably create one upload intent for ``user``.

    This admission step deliberately does not evaluate create/update permission
    expressions against an incomplete mutation payload. Update targets receive
    only the ordinary manager construction and instance-read gate needed to
    avoid issuing intents for absent or unreadable objects.
    """

    settings = get_file_upload_settings()
    intent_database_alias = _normalized_database_alias(settings.intent_database)
    if (
        intent_database_alias in connections.databases
        and _sqlite_has_application_atomic_block(intent_database_alias)
    ):
        # SQLite cannot restart a nested savepoint as a fresh transaction after
        # SQLITE_BUSY. Fail before rate-limit, adapter, or database side effects;
        # deployments using ATOMIC_REQUESTS must exempt their GraphQL view.
        raise UploadStorageError
    if _begin_upload_rate_limit_hook is None:
        _enforce_default_begin_rate_limit(user, settings)
    else:
        try:
            denied = bool(_begin_upload_rate_limit_hook(user, request))
        except UploadRateLimitExceededError:
            raise UploadRateLimitExceededError from None
        except UploadError:
            raise UploadStorageError from None
        except Exception as exc:
            raise UploadStorageError from exc
        if denied:
            raise UploadRateLimitExceededError

    owner_pk = _authenticated_user_pk(user)
    values = cast(_BeginRequest, request)
    if not settings.enabled:
        raise UploadBackendUnsupportedError

    manager_class = _resolve_manager(values.manager)
    interface = cast(_UploadInterface, manager_class.Interface)
    _model, model_field = _resolve_file_field(interface, values.field)
    policy = _resolve_policy(manager_class, str(values.field), settings)
    database_alias = _normalized_database_alias(getattr(interface, "database", None))
    if database_alias != _normalized_database_alias(settings.intent_database):
        raise UploadDatabaseMismatchError

    operation = _normalize_operation(values.operation)
    _validate_supported_operation(interface, operation)
    target_id = _resolve_target_id(
        manager_class,
        operation=operation,
        object_id=values.object_id,
        user=user,
    )
    filename = _normalize_filename(values.filename)
    size = _normalize_size(values.size, policy)
    content_type = _normalize_content_type(values.content_type, policy)
    _validate_extension(filename, policy)
    checksum_sha256 = _normalize_checksum(values.checksum)

    try:
        adapter = upload_adapter_registry.resolve(model_field.storage)
        adapter_id, adapter_version = _validate_adapter_identity(adapter)
        storage_fingerprint = adapter.storage_fingerprint()
        _validate_storage_fingerprint(storage_fingerprint)
    except UploadError:
        raise UploadStorageError from None
    except Exception as exc:
        raise UploadStorageError from exc
    intent_id = uuid4()
    stage_key = f"{settings.staging_prefix}{uuid4().hex}/{uuid4().hex}"
    token, token_digest = issue_upload_token()
    upload_url = f"/{settings.http_upload_path}{intent_id}"

    user_model = get_user_model()

    def admit() -> tuple[UploadInstructions, datetime]:
        with transaction.atomic(using=database_alias):
            _acquire_global_quota_lock(database_alias)
            user_manager = user_model._default_manager.using(database_alias)
            try:
                user_manager.select_for_update().get(pk=owner_pk)
            except user_model.DoesNotExist as exc:
                raise UploadAuthenticationError from exc

            _enforce_pending_quotas(
                owner_pk=owner_pk,
                size=size,
                settings=settings,
                database_alias=database_alias,
            )
            expires_at = timezone.now() + timedelta(seconds=settings.token_ttl_seconds)
            transfer_credential = issue_upload_transfer_credential(
                intent_id=intent_id,
                owner_pk=owner_pk,
                adapter_id=adapter_id,
            )
            transfer_headers = {"Authorization": f"GMUpload {transfer_credential}"}
            expires_in = max(
                1,
                int((expires_at - timezone.now()).total_seconds()),
            )
            try:
                instructions = adapter.create_upload_instructions(
                    stage_key=stage_key,
                    upload_url=upload_url,
                    content_type=content_type,
                    size=size,
                    checksum_sha256=checksum_sha256,
                    headers=transfer_headers,
                    expires_in=expires_in,
                )
                _validate_upload_instructions(
                    instructions,
                    stage_key=stage_key,
                    required_proxy_authorization=transfer_headers["Authorization"],
                    allow_insecure_http=bool(
                        django_settings.DEBUG and settings.allow_insecure_http
                    ),
                )
                current_adapter_identity = _validate_adapter_identity(adapter)
            except UploadError:
                raise UploadStorageError from None
            except Exception as exc:
                raise UploadStorageError from exc
            if current_adapter_identity != (adapter_id, adapter_version):
                raise UploadStorageError
            UploadIntent.objects.using(database_alias).create(
                id=intent_id,
                user_id=owner_pk,
                token_digest=token_digest,
                manager_name=manager_class.__name__,
                field_name=model_field.name,
                operation=operation.value,
                target_id=target_id,
                adapter_id=adapter_id,
                adapter_version=str(adapter_version),
                storage_fingerprint=storage_fingerprint,
                staging_key=stage_key,
                original_filename=filename,
                declared_size=size,
                declared_content_type=content_type,
                declared_checksum_sha256=checksum_sha256,
                expires_at=expires_at,
            )
        return instructions, expires_at

    instructions, expires_at = _run_admission_transaction(
        database_alias=database_alias,
        operation=admit,
    )

    return BeginFileUploadResult(
        intent_id=intent_id,
        token=token,
        instructions=instructions,
        expires_at=expires_at,
    )


def _authenticated_user_pk(user: object) -> object:
    is_authenticated = getattr(user, "is_authenticated", False)
    pk = getattr(user, "pk", None)
    if is_authenticated is not True or pk is None:
        raise UploadAuthenticationError
    return pk


def _validate_adapter_identity(adapter: object) -> tuple[str, int]:
    adapter_id = getattr(adapter, "adapter_id", None)
    adapter_version = getattr(adapter, "adapter_version", None)
    if (
        not isinstance(adapter_id, str)
        or _ADAPTER_ID.fullmatch(adapter_id) is None
        or isinstance(adapter_version, bool)
        or not isinstance(adapter_version, int)
        or adapter_version <= 0
        or len(str(adapter_version)) > 64
    ):
        raise UploadStorageError
    return adapter_id, adapter_version


def _validate_storage_fingerprint(value: object) -> None:
    if not isinstance(value, str) or _STORAGE_FINGERPRINT.fullmatch(value) is None:
        raise UploadStorageError


def _validate_upload_instructions(
    instructions: object,
    *,
    stage_key: str,
    required_proxy_authorization: str,
    allow_insecure_http: bool,
) -> None:
    if type(instructions) is not UploadInstructions:
        raise UploadStorageError
    typed = instructions
    if (
        not isinstance(typed.transport, UploadTransport)
        or typed.method != "PUT"
        or typed.fields
    ):
        raise UploadStorageError

    url = typed.url
    if (
        not isinstance(url, str)
        or not url
        or len(url) > 8192
        or any(unicodedata.category(character).startswith("C") for character in url)
    ):
        raise UploadStorageError
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise UploadStorageError from exc
    if typed.transport is UploadTransport.DIRECT:
        allowed_schemes = {"https"}
        if allow_insecure_http:
            allowed_schemes.add("http")
        if (
            parsed.scheme not in allowed_schemes
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise UploadStorageError
    else:
        try:
            decoded_url = unquote(url, errors="strict")
        except UnicodeError as exc:
            raise UploadStorageError from exc
        if (
            parsed.scheme
            or parsed.netloc
            or not parsed.path.startswith("/")
            or parsed.query
            or parsed.fragment
            or any(
                unicodedata.category(character).startswith("C")
                for character in decoded_url
            )
            or stage_key in decoded_url
        ):
            raise UploadStorageError

    if len(typed.headers) > 64:
        raise UploadStorageError
    encoded_stage_key = quote(stage_key, safe="")
    normalized_header_names: set[str] = set()
    authorization_values: list[str] = []
    for name, value in typed.headers.items():
        if (
            not isinstance(name, str)
            or _HTTP_HEADER_NAME.fullmatch(name) is None
            or len(name) > 255
            or not isinstance(value, str)
            or not value
            or len(value) > 8192
            or any(
                unicodedata.category(character).startswith("C") for character in value
            )
            or stage_key in name
            or stage_key in value
            or encoded_stage_key in name
            or encoded_stage_key in value
        ):
            raise UploadStorageError
        normalized_name = name.casefold()
        if normalized_name in normalized_header_names:
            raise UploadStorageError
        normalized_header_names.add(normalized_name)
        if normalized_name == "authorization":
            authorization_values.append(value)

    if typed.transport is UploadTransport.PROXY:
        if authorization_values != [required_proxy_authorization]:
            raise UploadStorageError
    elif authorization_values:
        raise UploadStorageError


def _run_admission_transaction(
    *,
    database_alias: str,
    operation: Callable[[], tuple[UploadInstructions, datetime]],
) -> tuple[UploadInstructions, datetime]:
    """Retry a complete SQLite admission transaction after transient locking."""

    deadline = time.monotonic() + _SQLITE_BUSY_RETRY_DEADLINE_SECONDS
    for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
        try:
            return operation()
        except OperationalError as exc:
            if not _is_sqlite_busy_error(exc, database_alias=database_alias):
                raise
            remaining = deadline - time.monotonic()
            if attempt + 1 >= _SQLITE_BUSY_RETRY_ATTEMPTS or remaining <= 0:
                raise UploadStorageError from exc
            base_delay = min(
                _SQLITE_BUSY_BASE_DELAY_SECONDS * (2**attempt),
                _SQLITE_BUSY_MAX_DELAY_SECONDS,
            )
            jitter = base_delay * secrets.randbelow(1_001) / 1_000
            time.sleep(min(base_delay + jitter, remaining))
    raise AssertionError("unreachable")


def _sqlite_has_application_atomic_block(database_alias: str) -> bool:
    connection = connections[database_alias]
    if connection.vendor != "sqlite":
        return False
    return any(
        not getattr(block, "_from_testcase", False)
        for block in connection.atomic_blocks
    )


def _is_sqlite_busy_error(error: OperationalError, *, database_alias: str) -> bool:
    if connections[database_alias].vendor != "sqlite":
        return False
    cause = error.__cause__
    error_code = getattr(cause, "sqlite_errorcode", None)
    if error_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return True
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "database is busy",
            "database is locked",
            "database table is locked",
        )
    )


def _acquire_global_quota_lock(database_alias: str) -> None:
    """Serialize global quota checks on one fixed, migration-seeded row."""

    manager = UploadQuotaLock.objects.using(database_alias)
    manager.get_or_create(pk=1, defaults={"generation": 0})
    if connections[database_alias].features.has_select_for_update:
        manager.select_for_update().get(pk=1)
    # SQLite ignores SELECT FOR UPDATE. Updating this same row acquires its
    # database write lock before any quota reads and is harmless elsewhere.
    manager.filter(pk=1).update(generation=models.F("generation") + 1)


def _resolve_manager(value: object) -> type[GeneralManager]:
    if not isinstance(value, str) or not value:
        raise UploadManagerInvalidError
    # Import lazily so this service always reads the live, resettable registry.
    from general_manager.api.graphql import GraphQL

    manager_class = GraphQL.manager_registry.get(value)
    if manager_class is None or manager_class.__name__ != value:
        raise UploadManagerInvalidError
    return manager_class


def _resolve_file_field(
    interface: _UploadInterface,
    value: object,
) -> tuple[type[models.Model], models.FileField]:
    if not isinstance(value, str) or not value:
        raise UploadFieldInvalidError
    model = getattr(interface, "_model", None)
    if (
        not isinstance(interface, type)
        or not issubclass(interface, OrmInterfaceBase)
        or not isinstance(model, type)
        or not issubclass(model, models.Model)
    ):
        raise UploadFieldInvalidError
    try:
        model_field = model._meta.get_field(value)
    except (FieldDoesNotExist, LookupError) as exc:
        raise UploadFieldInvalidError from exc
    try:
        metadata = interface.get_attribute_types().get(value)
    except (AttributeError, NotImplementedError) as exc:
        raise UploadFieldInvalidError from exc
    expected_kind = "image" if isinstance(model_field, models.ImageField) else "file"
    if (
        not isinstance(model_field, models.FileField)
        or not isinstance(metadata, Mapping)
        or metadata.get("orm_field_kind") != expected_kind
        or metadata.get("is_editable") is not True
        or model_field.editable is not True
    ):
        raise UploadFieldInvalidError
    return model, model_field


def _resolve_policy(
    manager_class: type[GeneralManager],
    field_name: str,
    settings: FileUploadSettings,
) -> FileUploadPolicy:
    base = FileUploadPolicy(max_bytes=settings.max_bytes, public=False)
    declaration = inspect.getattr_static(manager_class, "FileUploads", None)
    fields = getattr(declaration, "fields", {}) if declaration is not None else {}
    if fields is None:
        fields = {}
    if not isinstance(fields, Mapping):
        raise UploadFieldInvalidError
    override = fields.get(field_name, FileUploadPolicy())
    if not isinstance(override, FileUploadPolicy):
        raise UploadFieldInvalidError
    return merge_file_upload_policy(base, override)


def _normalized_database_alias(value: object) -> str:
    return value if isinstance(value, str) and value else DEFAULT_DB_ALIAS


def _normalize_operation(value: object) -> UploadOperation:
    if isinstance(value, UploadOperation):
        return value
    if isinstance(value, str):
        try:
            return UploadOperation(value.lower())
        except ValueError:
            pass
    raise UploadOperationInvalidError


def _validate_supported_operation(
    interface: _UploadInterface,
    operation: UploadOperation,
) -> None:
    get_capabilities = getattr(interface, "get_capabilities", None)
    if not callable(get_capabilities):
        raise UploadOperationInvalidError
    capabilities = get_capabilities()
    method = getattr(interface, operation.value, None)
    base_method = getattr(InterfaceBase, operation.value)
    method_overridden = bool(
        callable(method)
        and getattr(method, "__code__", None) is not None
        and getattr(method, "__code__", None) != getattr(base_method, "__code__", None)
    )
    if operation.value not in capabilities and not method_overridden:
        raise UploadOperationInvalidError


def _resolve_target_id(
    manager_class: type[GeneralManager],
    *,
    operation: UploadOperation,
    object_id: object,
    user: object,
) -> str | None:
    if operation is UploadOperation.CREATE:
        if object_id is not None:
            raise UploadOperationInvalidError
        return None
    if object_id is None:
        raise UploadOperationInvalidError

    try:
        target = manager_class(id=object_id)
    except (ObjectDoesNotExist, KeyError, TypeError, ValueError) as exc:
        raise UploadTargetUnavailableError from exc
    permission_class = inspect.getattr_static(manager_class, "Permission", None)
    if permission_class is not None:
        try:
            can_read = permission_class(target, user).can_read_instance()
        except (NotImplementedError, PermissionError) as exc:
            raise UploadTargetUnavailableError from exc
        if not can_read:
            raise UploadTargetUnavailableError
    return serialize_dependency_identifier(target.identification)


def _normalize_filename(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidUploadFilenameError
    normalized = unicodedata.normalize("NFC", value)
    try:
        normalized.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise InvalidUploadFilenameError from exc
    drive, _tail = ntpath.splitdrive(normalized)
    if (
        "/" in normalized
        or "\\" in normalized
        or bool(drive)
        or normalized in {".", ".."}
        or any(
            unicodedata.category(character).startswith("C") for character in normalized
        )
        or not normalized
        or len(normalized) > _MAX_FILENAME_LENGTH
    ):
        raise InvalidUploadFilenameError
    # Validation intentionally precedes basename extraction; extraction is not
    # allowed to turn an unsafe client path into an apparently safe filename.
    basename = ntpath.basename(normalized)
    if basename != normalized:
        raise InvalidUploadFilenameError
    return basename


def _normalize_size(value: object, policy: FileUploadPolicy) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidUploadSizeError
    if policy.max_bytes is not None and value > policy.max_bytes:
        raise InvalidUploadSizeError
    return value


def _normalize_content_type(value: object, policy: FileUploadPolicy) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 255
        or _CONTENT_TYPE.fullmatch(value) is None
    ):
        raise InvalidFileTypeError
    normalized = value.lower()
    allowed = policy.allowed_content_types
    if allowed is not None and normalized not in {
        item.strip().lower() for item in allowed
    }:
        raise InvalidFileTypeError
    return normalized


def _validate_extension(filename: str, policy: FileUploadPolicy) -> None:
    allowed = policy.allowed_extensions
    if allowed is None:
        return
    suffix = "." + filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    normalized_allowed = {
        item.strip().lower()
        if item.strip().startswith(".")
        else f".{item.strip().lower()}"
        for item in allowed
    }
    if suffix not in normalized_allowed:
        raise InvalidFileTypeError


def _checksum_values(checksum: object) -> tuple[object, object]:
    if isinstance(checksum, Mapping):
        return checksum.get("algorithm"), checksum.get("digest")
    return getattr(checksum, "algorithm", None), getattr(checksum, "digest", None)


def _normalize_checksum(checksum: object) -> str:
    algorithm, digest = _checksum_values(checksum)
    if isinstance(algorithm, ChecksumAlgorithm):
        normalized_algorithm = algorithm
    elif isinstance(algorithm, str):
        try:
            normalized_algorithm = ChecksumAlgorithm(algorithm.lower())
        except ValueError as exc:
            raise InvalidUploadChecksumError from exc
    else:
        raise InvalidUploadChecksumError
    if normalized_algorithm is not ChecksumAlgorithm.SHA256 or not isinstance(
        digest, str
    ):
        raise InvalidUploadChecksumError
    if _HEX_SHA256.fullmatch(digest):
        return digest.lower()
    try:
        decoded = base64.b64decode(digest, validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidUploadChecksumError from exc
    if len(decoded) != 32:
        raise InvalidUploadChecksumError
    return decoded.hex()


def _enforce_pending_quotas(
    *,
    owner_pk: object,
    size: int,
    settings: FileUploadSettings,
    database_alias: str,
) -> None:
    active = UploadIntent.objects.using(database_alias).filter(
        models.Q(state=UploadIntentState.FINALIZING.value)
        | models.Q(
            state__in=_EXPIRING_QUOTA_STATES,
            expires_at__gt=timezone.now(),
        )
    )
    global_count = active.count()
    if global_count >= settings.max_pending_intents_global:
        raise UploadQuotaExceededError
    global_bytes = active.aggregate(total=models.Sum("declared_size"))["total"] or 0
    if global_bytes + size > settings.max_pending_bytes_global:
        raise UploadQuotaExceededError

    user_active = active.filter(user_id=owner_pk)
    if user_active.count() >= settings.max_pending_intents_per_user:
        raise UploadQuotaExceededError
    total = user_active.aggregate(total=models.Sum("declared_size"))["total"] or 0
    if total + size > settings.max_pending_bytes_per_user:
        raise UploadQuotaExceededError
