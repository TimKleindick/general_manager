"""Creation service for durable, field-bound upload intents."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import inspect
import ntpath
import re
import unicodedata
from typing import Protocol, cast
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from django.core import signing
from django.core.signing import BadSignature
from django.db import DEFAULT_DB_ALIAS, models, router, transaction
from django.utils import timezone

from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.uploads.adapters import (
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
    UploadError,
    UploadAuthenticationError,
    UploadBackendUnsupportedError,
    UploadDatabaseMismatchError,
    UploadFieldInvalidError,
    UploadManagerInvalidError,
    UploadOperationInvalidError,
    UploadQuotaExceededError,
    UploadRateLimitExceededError,
    UploadStorageError,
    UploadTargetUnavailableError,
)
from general_manager.uploads.models import UploadIntent
from general_manager.uploads.tokens import issue_upload_token
from general_manager.uploads.types import (
    ChecksumAlgorithm,
    UploadIntentState,
    UploadOperation,
)


_MAX_FILENAME_LENGTH = 255
_CONTENT_TYPE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)
_HEX_SHA256 = re.compile(r"^[0-9A-Fa-f]{64}$")
_ACTIVE_QUOTA_STATES = (
    UploadIntentState.PENDING.value,
    UploadIntentState.TRANSFERRING.value,
    UploadIntentState.UPLOADED.value,
)
_TRANSFER_CREDENTIAL_SALT = "general_manager.uploads.transfer"


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


def _allow_begin_upload(_user: object, _request: object) -> None:
    """Default hook for projects that do not configure request-rate limiting."""


_begin_upload_rate_limit_hook: BeginUploadRateLimitHook = _allow_begin_upload
upload_adapter_registry = UploadAdapterRegistry()


def set_begin_upload_rate_limit_hook(
    hook: BeginUploadRateLimitHook | None,
) -> BeginUploadRateLimitHook:
    """Install a process-local rate-limit hook and return the previous hook.

    Hooks should raise :class:`UploadRateLimitExceededError` when admission is
    denied. Returning a truthy value is also treated as denial so cache-backed
    limiters can expose a lightweight boolean contract.
    """

    global _begin_upload_rate_limit_hook
    previous = _begin_upload_rate_limit_hook
    _begin_upload_rate_limit_hook = hook or _allow_begin_upload
    return previous


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
    """Validate and durably create one upload intent for ``user``.

    This admission step deliberately does not evaluate create/update permission
    expressions against an incomplete mutation payload. Update targets receive
    only the ordinary manager construction and instance-read gate needed to
    avoid issuing intents for absent or unreadable objects.
    """

    owner_pk = _authenticated_user_pk(user)
    if _begin_upload_rate_limit_hook(user, request):
        raise UploadRateLimitExceededError

    values = cast(_BeginRequest, request)
    settings = get_file_upload_settings()
    if not settings.enabled:
        raise UploadBackendUnsupportedError

    manager_class = _resolve_manager(values.manager)
    interface = cast(_UploadInterface, manager_class.Interface)
    model, model_field = _resolve_file_field(interface, values.field)
    policy = _resolve_policy(manager_class, str(values.field), settings)
    database_alias = _effective_database_alias(interface, model)
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
        storage_fingerprint = adapter.storage_fingerprint()
    except UploadError:
        raise
    except Exception as exc:
        raise UploadStorageError from exc
    intent_id = uuid4()
    stage_key = f"{settings.staging_prefix}{uuid4().hex}/{uuid4().hex}"
    token, token_digest = issue_upload_token()
    upload_url = f"/{settings.http_upload_path}{intent_id}"

    user_model = get_user_model()
    with transaction.atomic(using=database_alias):
        try:
            owner = (
                user_model._default_manager.using(database_alias)
                .select_for_update()
                .get(pk=owner_pk)
            )
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
            adapter_id=adapter.adapter_id,
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
        except UploadError:
            raise
        except Exception as exc:
            raise UploadStorageError from exc
        UploadIntent.objects.using(database_alias).create(
            id=intent_id,
            user=owner,
            token_digest=token_digest,
            manager_name=manager_class.__name__,
            field_name=model_field.name,
            operation=operation.value,
            target_id=target_id,
            adapter_id=adapter.adapter_id,
            adapter_version=str(adapter.adapter_version),
            storage_fingerprint=storage_fingerprint,
            staging_key=stage_key,
            original_filename=filename,
            declared_size=size,
            declared_content_type=content_type,
            declared_checksum_sha256=checksum_sha256,
            expires_at=expires_at,
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


def _effective_database_alias(
    interface: _UploadInterface,
    model: type[models.Model],
) -> str:
    explicit = getattr(interface, "database", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    return _normalized_database_alias(router.db_for_write(model))


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
        user_id=owner_pk,
        state__in=_ACTIVE_QUOTA_STATES,
        expires_at__gt=timezone.now(),
    )
    if active.count() >= settings.max_pending_intents_per_user:
        raise UploadQuotaExceededError
    total = active.aggregate(total=models.Sum("declared_size"))["total"] or 0
    if total + size > settings.max_pending_bytes_per_user:
        raise UploadQuotaExceededError
