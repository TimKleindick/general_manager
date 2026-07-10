"""Django startup checks for the optional file-upload subsystem."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import inspect
import re
from urllib.parse import urlsplit
from dataclasses import dataclass
from typing import Any, cast

from django.core.checks import CheckMessage, Error, Warning, register
from django.core.files.storage import Storage
from django.db import DEFAULT_DB_ALIAS, models

from general_manager.conf import get_setting
from general_manager.uploads.adapters import (
    ProxyUploadAdapter,
    UploadFinalizationAdapter,
)
from general_manager.uploads.config import (
    FileUploadConfigurationError,
    FileUploadPolicy,
    FileUploadSettings,
    get_file_upload_settings,
)
from general_manager.uploads import services


_registered = False
_PREFIX = "general_manager.uploads"
_CONTENT_TYPE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)
_EXTENSION = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$")
_ADAPTER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class _AdapterCapabilities:
    identity_valid: bool
    finalization: bool
    public: bool
    uncertain: bool = False


def _error(message: str, suffix: str) -> Error:
    return Error(message, id=f"{_PREFIX}.{suffix}")


def _warning(message: str, suffix: str) -> Warning:
    return Warning(message, id=f"{_PREFIX}.{suffix}")


def _alias(value: object) -> str:
    return value if isinstance(value, str) and value else DEFAULT_DB_ALIAS


def _uploads_explicitly_disabled() -> bool:
    configured = get_setting("FILE_UPLOADS", {})
    return isinstance(configured, Mapping) and configured.get("ENABLED", False) is False


def _load_registered_managers() -> tuple[type[object], ...] | None:
    """Return the live GraphQL manager registry, or ``None`` when unavailable."""

    try:
        from general_manager.api.graphql import GraphQL

        return tuple(GraphQL.manager_registry.values())
    except Exception:  # noqa: BLE001 - checks never break Django startup
        return None


def run_upload_checks(
    *,
    manager_database: object = ...,
    intent_database: object = ...,
    managers: Iterable[type[object]] | None = None,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Return stable, sanitized configuration issues without storage I/O.

    ``manager_database`` and ``intent_database`` are a narrow diagnostic seam
    used by tests and deployment tooling. Normal Django checks inspect the live
    manager registry instead.
    """

    if manager_database is not ... or intent_database is not ...:
        if _alias(manager_database) != _alias(intent_database):
            return [
                _error(
                    "Upload-enabled managers must use the upload intent database.",
                    "E001",
                )
            ]
        return []

    if _uploads_explicitly_disabled():
        if managers is None:
            managers = _load_registered_managers() or ()
        return _check_disabled_managers(managers)
    try:
        settings = get_file_upload_settings()
    except FileUploadConfigurationError:
        return [_error("File upload settings are invalid.", "E000")]
    except Exception:  # noqa: BLE001 - checks never break Django startup
        return [_error("File upload settings could not be inspected.", "E000")]
    if not settings.enabled:
        return []

    issues: list[CheckMessage] = []
    if settings.terminal_retention_seconds <= settings.download_url_ttl_seconds:
        issues.append(
            _error(
                "Upload intent retention must exceed the download capability TTL.",
                "E002",
            )
        )
    issues.extend(_check_adapter_registrations())
    if managers is None:
        managers = _load_registered_managers()
        if managers is None:
            return [*issues, _error("Upload managers could not be inspected.", "E003")]
    for manager in managers:
        issues.extend(_check_manager(manager, settings))
    return _deduplicate(issues)


def _check_disabled_managers(
    managers: Iterable[type[object]],
) -> list[CheckMessage]:
    for manager in managers:
        interface = getattr(manager, "Interface", None)
        get_attributes = getattr(interface, "get_attribute_types", None)
        if interface is None or not callable(get_attributes):
            continue
        try:
            metadata = get_attributes()
        except Exception:  # noqa: BLE001, S112 - only proven fields are rejected
            continue
        if not isinstance(metadata, Mapping):
            continue
        for name, value in metadata.items():
            if (
                not isinstance(name, str)
                or not isinstance(value, Mapping)
                or value.get("is_editable") is not True
                or value.get("orm_field_kind") not in {"file", "image"}
            ):
                continue
            try:
                _model, model_field = services.resolve_file_field(
                    cast(Any, interface), name
                )
            except Exception:  # noqa: BLE001, S112 - require a proven ORM field
                continue
            if isinstance(model_field, (models.FileField, models.ImageField)):
                return [
                    _error(
                        "Editable GraphQL file fields require file uploads to be enabled.",
                        "E006",
                    )
                ]
    return []


def _check_manager(
    manager: type[object], settings: FileUploadSettings
) -> list[CheckMessage]:
    interface = getattr(manager, "Interface", None)
    get_attributes = getattr(interface, "get_attribute_types", None)
    if interface is None or not callable(get_attributes):
        return []
    try:
        metadata = get_attributes()
    except Exception:  # noqa: BLE001 - manager checks remain isolated
        return [_error("An upload manager exposes invalid field metadata.", "E003")]
    if not isinstance(metadata, Mapping):
        return [_error("An upload manager exposes invalid field metadata.", "E003")]
    upload_fields = {
        name: value
        for name, value in metadata.items()
        if isinstance(name, str)
        and isinstance(value, Mapping)
        and value.get("orm_field_kind") in {"file", "image"}
        and value.get("is_editable") is True
    }
    declaration = inspect.getattr_static(manager, "FileUploads", None)
    policies = (
        inspect.getattr_static(declaration, "fields", {})
        if declaration is not None
        else {}
    )
    if policies is None:
        policies = {}
    if not isinstance(policies, Mapping):
        return [_error("An upload manager has invalid field policies.", "E003")]
    issues: list[CheckMessage]
    if any(not isinstance(name, str) for name in policies):
        issues = [_error("An upload field policy is invalid.", "E003")]
    else:
        issues = []
    names = set(upload_fields) | {name for name in policies if isinstance(name, str)}
    if not names:
        return issues
    if _alias(getattr(interface, "database", None)) != _alias(settings.intent_database):
        issues.append(
            _error(
                "Upload-enabled managers must use the upload intent database.",
                "E001",
            )
        )
    for name in sorted(names):
        value = policies.get(name, FileUploadPolicy())
        if name not in upload_fields or not isinstance(value, FileUploadPolicy):
            issues.append(_error("An upload field policy is invalid.", "E003"))
            continue
        try:
            _model, model_field = services.resolve_file_field(
                cast(Any, interface), name
            )
            if not isinstance(model_field, (models.FileField, models.ImageField)):
                issues.append(_error("An upload field policy is invalid.", "E003"))
                continue
            if not _policy_is_safe(
                value, image=isinstance(model_field, models.ImageField)
            ):
                issues.append(_error("An upload field policy is invalid.", "E003"))
            capabilities = _static_adapter_capabilities(model_field.storage)
            if capabilities.uncertain:
                issues.append(
                    _warning(
                        "An upload adapter requires runtime capability validation.",
                        "W001",
                    )
                )
            if not capabilities.identity_valid or not capabilities.finalization:
                issues.append(
                    _error(
                        "An upload adapter lacks safe finalization capabilities.",
                        "E004",
                    )
                )
            if value.public is True and not capabilities.public:
                issues.append(
                    _error(
                        "A public upload field uses an adapter without public URLs.",
                        "E005",
                    )
                )
        except Exception:  # noqa: BLE001 - custom adapters are untrusted
            issues.append(_error("An upload adapter configuration is invalid.", "E004"))
    return issues


def _static_adapter_capabilities(storage: object) -> _AdapterCapabilities:
    """Inspect configured adapter shape without invoking storage/network methods."""

    registry = services.upload_adapter_registry
    factory = registry.explicit_factory_for(storage)  # type: ignore[arg-type]
    if factory is not None:
        declared = _declared_factory_capabilities(factory)
        if declared is not None:
            return declared
        adapter_type = factory if isinstance(factory, type) else None
        if adapter_type is None:
            return _AdapterCapabilities(True, False, False, uncertain=True)
        adapter_id = inspect.getattr_static(adapter_type, "adapter_id", None)
        adapter_version = inspect.getattr_static(adapter_type, "adapter_version", None)
        identity_valid = bool(
            isinstance(adapter_id, str)
            and _ADAPTER_ID.fullmatch(adapter_id)
            and isinstance(adapter_version, int)
            and not isinstance(adapter_version, bool)
            and adapter_version > 0
        )
        finalization = bool(
            all(
                callable(inspect.getattr_static(adapter_type, name, None))
                for name in (
                    "inspect_materialized",
                    "delete_materialized",
                    "delete_object",
                    "inspect_replaced_object",
                    "plan_replaced_object_claim",
                    "claim_replaced_object",
                    "delete_claimed_object",
                )
            )
        )
        public_value = inspect.getattr_static(
            adapter_type, "supports_public_urls", False
        )
        public = public_value is True
        uncertain = isinstance(public_value, property)
        return _AdapterCapabilities(identity_valid, finalization, public, uncertain)

    if _looks_like_s3_static(storage):
        configured = _static_value(storage, "object_parameters", {})
        options = configured if isinstance(configured, Mapping) else {}
        explicit_public = bool(
            _static_value(storage, "public", False) is True
            or _static_value(storage, "querystring_auth", True) is False
            or options.get("ACL") in {"public-read", "public-read-write"}
            or _static_value(storage, "default_acl", None)
            in {"public-read", "public-read-write"}
        )
        custom_domain = _static_value(storage, "custom_domain", None)
        exact_public = custom_domain in {None, ""}
        versioning = _static_value(storage, "versioning_enabled", None) is True
        endpoint = _static_value(storage, "endpoint_url", None)
        aws_endpoint = endpoint in {None, ""}
        if aws_endpoint:
            endpoint_safe = True
        elif isinstance(endpoint, str):
            parsed = urlsplit(endpoint)
            endpoint_safe = parsed.scheme == "https" and bool(parsed.hostname)
        else:
            endpoint_safe = False
        custom_copy = bool(
            aws_endpoint
            or _static_value(storage, "supports_conditional_copy", False) is True
        )
        client = _static_value(storage, "s3_client", None)
        client_meta = _static_value(client, "meta", None)
        client_config = _static_value(client_meta, "config", None)
        signature_safe = (
            _static_value(client_config, "signature_version", None) == "s3v4"
        )
        staging_private = bool(
            not explicit_public
            or _static_value(storage, "upload_staging_prefix_private", False) is True
        )
        direct_safe = (
            versioning
            and custom_copy
            and signature_safe
            and staging_private
            and endpoint_safe
        )
        return _AdapterCapabilities(
            True,
            True,
            explicit_public and direct_safe and exact_public,
            uncertain=not direct_safe,
        )

    adapter = ProxyUploadAdapter(storage)  # type: ignore[arg-type]
    return _AdapterCapabilities(
        identity_valid=True,
        finalization=isinstance(adapter, UploadFinalizationAdapter),
        public=adapter.supports_public_urls,
    )


_MISSING = object()


def _static_value(value: object, name: str, default: object = None) -> object:
    if value is None:
        return default
    resolved = inspect.getattr_static(value, name, _MISSING)
    if resolved is _MISSING or isinstance(resolved, property):
        return default
    return resolved


def _looks_like_s3_static(storage: object) -> bool:
    storage_type = type(storage)
    return bool(
        _static_value(storage, "_gm_s3_storage", False) is True
        or storage_type.__module__.startswith("storages.backends.s3")
        or storage_type.__name__ in {"S3Storage", "S3Boto3Storage"}
    )


def _check_adapter_registrations() -> list[CheckMessage]:
    registrations = services.upload_adapter_registry.registrations_snapshot()
    if not isinstance(registrations, Mapping):
        return [_error("Upload adapter registrations are invalid.", "E004")]
    for storage_class, factory in registrations.items():
        if (
            not isinstance(storage_class, type)
            or not issubclass(storage_class, Storage)
            or not callable(factory)
        ):
            return [_error("Upload adapter registrations are invalid.", "E004")]
        declared = inspect.getattr_static(
            factory, "upload_adapter_capabilities", _MISSING
        )
        if declared is not _MISSING:
            capabilities = _declared_factory_capabilities(factory)
            if capabilities is None or not capabilities.identity_valid:
                return [_error("Upload adapter registrations are invalid.", "E004")]
    return []


def _declared_factory_capabilities(
    factory: object,
) -> _AdapterCapabilities | None:
    declared = inspect.getattr_static(factory, "upload_adapter_capabilities", _MISSING)
    if declared is _MISSING:
        return None
    if not isinstance(declared, Mapping) or set(declared) != {
        "adapter_id",
        "adapter_version",
        "finalization",
        "public",
    }:
        return None
    adapter_id = declared.get("adapter_id")
    adapter_version = declared.get("adapter_version")
    finalization = declared.get("finalization")
    public = declared.get("public")
    valid = bool(
        isinstance(adapter_id, str)
        and _ADAPTER_ID.fullmatch(adapter_id)
        and isinstance(adapter_version, int)
        and not isinstance(adapter_version, bool)
        and adapter_version > 0
        and isinstance(finalization, bool)
        and isinstance(public, bool)
    )
    if not valid:
        return _AdapterCapabilities(False, False, False)
    return _AdapterCapabilities(True, cast(bool, finalization), cast(bool, public))


def _policy_is_safe(policy: FileUploadPolicy, *, image: bool) -> bool:
    content_types = policy.allowed_content_types
    if content_types is not None:
        if any(_CONTENT_TYPE.fullmatch(value) is None for value in content_types):
            return False
        if image and any(
            not value.lower().startswith("image/") for value in content_types
        ):
            return False
    extensions = policy.allowed_extensions
    if extensions is not None:
        normalized = tuple(
            value if value.startswith(".") else f".{value}" for value in extensions
        )
        if any(_EXTENSION.fullmatch(value) is None for value in normalized):
            return False
    return True


def _deduplicate(messages: Iterable[CheckMessage]) -> list[CheckMessage]:
    unique: dict[tuple[str, str], CheckMessage] = {}
    for message in messages:
        unique.setdefault((message.id or "", str(message.msg)), message)
    return list(unique.values())


def register_upload_checks() -> None:
    """Register the upload check exactly once per process."""

    global _registered
    if _registered:
        return
    register("general_manager")(run_upload_checks)
    _registered = True
