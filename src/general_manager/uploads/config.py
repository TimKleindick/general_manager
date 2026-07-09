"""Immutable file upload settings and per-field policies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from unicodedata import category
from urllib.parse import unquote, urlsplit

from django.db import DEFAULT_DB_ALIAS

from general_manager.conf import get_setting


class FileUploadConfigurationError(ValueError):
    """Raised when file upload configuration is invalid."""

    @classmethod
    def invalid_mapping(cls) -> FileUploadConfigurationError:
        """Build the error for a non-mapping settings value."""
        return cls("GENERAL_MANAGER['FILE_UPLOADS'] must be a mapping.")

    @classmethod
    def unknown_settings(cls, names: str) -> FileUploadConfigurationError:
        """Build the error for unsupported configuration keys."""
        return cls(f"Unknown FILE_UPLOADS settings: {names}.")

    @classmethod
    def positive_integer(cls, name: str) -> FileUploadConfigurationError:
        """Build the error for a non-positive or non-integer limit."""
        return cls(f"{name} must be a positive integer.")

    @classmethod
    def boolean(cls, name: str) -> FileUploadConfigurationError:
        """Build the error for a non-boolean flag."""
        return cls(f"{name} must be a boolean.")

    @classmethod
    def relative_path(cls, name: str) -> FileUploadConfigurationError:
        """Build the error for a malformed path setting."""
        return cls(f"{name} must be a non-empty relative path ending in '/'.")

    @classmethod
    def unsafe_path(cls, name: str) -> FileUploadConfigurationError:
        """Build the error for a path that could escape its namespace."""
        return cls(f"{name} must be a safe relative path.")

    @classmethod
    def invalid_database_alias(cls) -> FileUploadConfigurationError:
        """Build the error for an unsupported database alias value."""
        return cls("INTENT_DATABASE must be a string or null.")

    @classmethod
    def non_empty_strings(cls, name: str) -> FileUploadConfigurationError:
        """Build the error for a malformed policy allowlist."""
        return cls(f"{name} must be a non-empty sequence of non-empty strings.")


@dataclass(frozen=True, slots=True)
class FileUploadSettings:
    """Normalized global file upload configuration."""

    enabled: bool = False
    http_upload_path: str = "gm/uploads/"
    staging_prefix: str = "gm-staging/"
    intent_database: str = DEFAULT_DB_ALIAS
    max_bytes: int = 25_000_000
    max_pending_intents_per_user: int = 20
    max_pending_bytes_per_user: int = 100_000_000
    max_image_pixels: int = 40_000_000
    token_ttl_seconds: int = 900
    download_url_ttl_seconds: int = 300
    delete_replaced_files: bool = False


@dataclass(frozen=True, slots=True)
class FileUploadPolicy:
    """Optional per-file-field policy values layered over global policy."""

    max_bytes: int | None = None
    allowed_content_types: Sequence[str] | None = None
    allowed_extensions: Sequence[str] | None = None
    public: bool | None = None

    def __post_init__(self) -> None:
        """Reject invalid values before a policy can be merged or exposed."""
        if self.max_bytes is not None:
            _positive_integer("max_bytes", self.max_bytes)
        if self.public is not None and not isinstance(self.public, bool):
            raise FileUploadConfigurationError.boolean("public")
        object.__setattr__(
            self,
            "allowed_content_types",
            _normalize_policy_strings(
                "allowed_content_types",
                self.allowed_content_types,
            ),
        )
        object.__setattr__(
            self,
            "allowed_extensions",
            _normalize_policy_strings(
                "allowed_extensions",
                self.allowed_extensions,
            ),
        )


_SETTING_NAMES = {
    "ENABLED",
    "HTTP_UPLOAD_PATH",
    "STAGING_PREFIX",
    "INTENT_DATABASE",
    "MAX_BYTES",
    "MAX_PENDING_INTENTS_PER_USER",
    "MAX_PENDING_BYTES_PER_USER",
    "MAX_IMAGE_PIXELS",
    "TOKEN_TTL_SECONDS",
    "DOWNLOAD_URL_TTL_SECONDS",
    "DELETE_REPLACED_FILES",
}
_MALFORMED_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def get_file_upload_settings() -> FileUploadSettings:
    """Return strict, normalized settings from ``GENERAL_MANAGER['FILE_UPLOADS']``."""
    configured = get_setting("FILE_UPLOADS", {})
    if not isinstance(configured, Mapping):
        raise FileUploadConfigurationError.invalid_mapping()

    unknown = set(configured) - _SETTING_NAMES
    if unknown:
        names = ", ".join(sorted(repr(name) for name in unknown))
        raise FileUploadConfigurationError.unknown_settings(names)

    defaults = FileUploadSettings()
    enabled = _boolean("ENABLED", configured.get("ENABLED", defaults.enabled))
    delete_replaced_files = _boolean(
        "DELETE_REPLACED_FILES",
        configured.get("DELETE_REPLACED_FILES", defaults.delete_replaced_files),
    )
    http_upload_path = _safe_prefix(
        "HTTP_UPLOAD_PATH",
        configured.get("HTTP_UPLOAD_PATH", defaults.http_upload_path),
    )
    staging_prefix = _safe_prefix(
        "STAGING_PREFIX",
        configured.get("STAGING_PREFIX", defaults.staging_prefix),
    )
    intent_database = _database_alias(
        configured.get("INTENT_DATABASE", defaults.intent_database)
    )

    return FileUploadSettings(
        enabled=enabled,
        http_upload_path=http_upload_path,
        staging_prefix=staging_prefix,
        intent_database=intent_database,
        max_bytes=_positive_integer(
            "MAX_BYTES", configured.get("MAX_BYTES", defaults.max_bytes)
        ),
        max_pending_intents_per_user=_positive_integer(
            "MAX_PENDING_INTENTS_PER_USER",
            configured.get(
                "MAX_PENDING_INTENTS_PER_USER",
                defaults.max_pending_intents_per_user,
            ),
        ),
        max_pending_bytes_per_user=_positive_integer(
            "MAX_PENDING_BYTES_PER_USER",
            configured.get(
                "MAX_PENDING_BYTES_PER_USER", defaults.max_pending_bytes_per_user
            ),
        ),
        max_image_pixels=_positive_integer(
            "MAX_IMAGE_PIXELS",
            configured.get("MAX_IMAGE_PIXELS", defaults.max_image_pixels),
        ),
        token_ttl_seconds=_positive_integer(
            "TOKEN_TTL_SECONDS",
            configured.get("TOKEN_TTL_SECONDS", defaults.token_ttl_seconds),
        ),
        download_url_ttl_seconds=_positive_integer(
            "DOWNLOAD_URL_TTL_SECONDS",
            configured.get(
                "DOWNLOAD_URL_TTL_SECONDS", defaults.download_url_ttl_seconds
            ),
        ),
        delete_replaced_files=delete_replaced_files,
    )


def merge_file_upload_policy(
    base: FileUploadPolicy,
    override: FileUploadPolicy,
) -> FileUploadPolicy:
    """Return a new policy using non-``None`` override values."""
    return FileUploadPolicy(
        max_bytes=override.max_bytes
        if override.max_bytes is not None
        else base.max_bytes,
        allowed_content_types=(
            override.allowed_content_types
            if override.allowed_content_types is not None
            else base.allowed_content_types
        ),
        allowed_extensions=(
            override.allowed_extensions
            if override.allowed_extensions is not None
            else base.allowed_extensions
        ),
        public=override.public if override.public is not None else base.public,
    )


def _positive_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FileUploadConfigurationError.positive_integer(name)
    return value


def _normalize_policy_strings(
    name: str,
    value: object,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise FileUploadConfigurationError.non_empty_strings(name)
    normalized = tuple(value)
    if not normalized or any(
        not isinstance(item, str) or not item.strip() for item in normalized
    ):
        raise FileUploadConfigurationError.non_empty_strings(name)
    return normalized


def _boolean(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise FileUploadConfigurationError.boolean(name)
    return value


def _safe_prefix(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or not value.endswith("/"):
        raise FileUploadConfigurationError.relative_path(name)
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise FileUploadConfigurationError.unsafe_path(name) from exc
    segments = value[:-1].split("/")
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or value.startswith("/")
        or "\\" in value
        or any(not _is_safe_path_segment(segment) for segment in segments)
    ):
        raise FileUploadConfigurationError.unsafe_path(name)
    return value


def _is_safe_path_segment(segment: str) -> bool:
    """Reject malformed escapes and unsafe Unicode content after URL decoding."""
    if _MALFORMED_PERCENT_ESCAPE.search(segment):
        return False
    try:
        decoded = unquote(segment, errors="strict")
    except UnicodeError:
        return False
    return bool(
        decoded
        and decoded not in {".", ".."}
        and "/" not in decoded
        and "\\" not in decoded
        and all(not category(character).startswith("C") for character in decoded)
    )


def _database_alias(value: object) -> str:
    if value is None or value == "":
        return DEFAULT_DB_ALIAS
    if not isinstance(value, str):
        raise FileUploadConfigurationError.invalid_database_alias()
    return value
