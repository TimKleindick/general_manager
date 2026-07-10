"""Stable GraphQL schema types for uploaded and stored files."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import hmac
import mimetypes
import posixpath
import re
from typing import Any, TYPE_CHECKING, cast
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit
from uuid import UUID

from django.conf import settings as django_settings
from django.core import signing
from django.db import DEFAULT_DB_ALIAS, models
from django.utils import timezone
from graphql import Undefined
from graphql.language import StringValueNode

from general_manager.api.graphql_errors import BigIntScalar
from general_manager.uploads.adapters import (
    ExactPublicDownloadAdapter,
    PublicUploadUrlUnsupportedError,
    UploadAdapter,
)
from general_manager.uploads.config import get_file_upload_settings
from general_manager.uploads.errors import (
    UploadBackendUnsupportedError,
    UploadError,
)
from general_manager.uploads.types import StoredFileStatus
from general_manager.uploads.types import ObjectVersion, UploadIntentState

if TYPE_CHECKING:

    class _GrapheneMountedType:
        def __init__(self, *args: object, **kwargs: object) -> None: ...

    class Scalar(_GrapheneMountedType):
        """Typed stand-in for Graphene's untyped scalar base."""

    class ObjectType:
        """Typed stand-in for Graphene's untyped object base."""

    class Enum:
        """Typed stand-in for Graphene's untyped enum base."""

        @classmethod
        def from_enum(cls, enum: type[StoredFileStatus]) -> type[Enum]: ...

    class Field(_GrapheneMountedType): ...

    class String(_GrapheneMountedType): ...

    class Int(_GrapheneMountedType): ...

    class DateTime(_GrapheneMountedType): ...

else:
    from graphene import (  # type: ignore[import-untyped]
        DateTime,
        Enum,
        Field,
        Int,
        ObjectType,
        Scalar,
        String,
    )


class _UploadTokenTypeError(TypeError):
    """Raised when an upload token is not a string."""

    def __init__(self) -> None:
        super().__init__("UploadToken must be a string.")


class _EmptyUploadTokenError(ValueError):
    """Raised when an upload token is empty."""

    def __init__(self) -> None:
        super().__init__("UploadToken must not be empty.")


class UploadToken(Scalar):
    """Opaque, non-empty upload token accepted by generated mutations."""

    @staticmethod
    def serialize(value: object) -> str:
        return UploadToken._validate(value)

    @staticmethod
    def parse_value(value: object) -> str:
        return UploadToken._validate(value)

    @staticmethod
    def parse_literal(node: object, _variables: object = None) -> str | object:
        if not isinstance(node, StringValueNode):
            return Undefined
        return UploadToken._validate(node.value)

    @staticmethod
    def _validate(value: object) -> str:
        if not isinstance(value, str):
            raise _UploadTokenTypeError
        if value == "":
            raise _EmptyUploadTokenError
        return value


StoredFileStatusEnum = Enum.from_enum(StoredFileStatus)

_DOWNLOAD_CAPABILITY_SALT = "general_manager.uploads.private-download.v1"
_DOWNLOAD_CACHE_ATTRIBUTE = "_general_manager_stored_file_cache"
_SAFE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)


class _InvalidDownloadValue(ValueError):
    """Raised internally for malformed capability or adapter output values."""


def _safe_basename(value: str) -> str:
    return posixpath.basename(value.replace("\\", "/")) or "download"


def _key_digest(value: str) -> str:
    return hmac.new(
        str(django_settings.SECRET_KEY).encode("utf-8"),
        b"general_manager.uploads.download-key.v1\x00" + value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class LocalDownloadCapability:
    """A repr-redacted local capability URL and its finite expiry."""

    url: str = field(repr=False)
    expires_at: datetime = field(repr=False)

    def __iter__(self):  # type: ignore[no-untyped-def]
        yield self.url
        yield self.expires_at

    def __getitem__(self, index: int) -> str | datetime:
        return (self.url, self.expires_at)[index]


def issue_local_download_capability(
    *,
    manager_name: str,
    object_id: str,
    field_name: str,
    current_key: str,
    expires_in: int,
    intent_id: UUID | None = None,
    now: Callable[[], datetime] = timezone.now,
) -> LocalDownloadCapability:
    """Issue a signed current-binding capability without embedding a raw key."""

    if (
        not manager_name
        or not object_id
        or not field_name
        or not current_key
        or isinstance(expires_in, bool)
        or not isinstance(expires_in, int)
        or expires_in <= 0
    ):
        raise _InvalidDownloadValue
    expires_at = now() + timedelta(seconds=expires_in)
    payload = {
        "v": 1,
        "m": manager_name,
        "o": object_id,
        "f": field_name,
        "k": _key_digest(current_key),
        "i": str(intent_id) if intent_id is not None else None,
        "e": int(expires_at.timestamp()),
    }
    capability = signing.dumps(
        payload,
        key=django_settings.SECRET_KEY,
        salt=_DOWNLOAD_CAPABILITY_SALT,
        compress=True,
    )
    configured_path = get_file_upload_settings().http_upload_path
    return LocalDownloadCapability(
        url=f"/{configured_path}download/{capability}",
        expires_at=expires_at,
    )


def decode_local_download_capability(
    capability: str,
    *,
    max_age: int,
    now: Callable[[], datetime] = timezone.now,
) -> dict[str, object] | None:
    """Return one strictly validated capability payload or ``None``."""

    if not capability or len(capability) > 4096:
        return None
    try:
        payload = signing.loads(
            capability,
            key=django_settings.SECRET_KEY,
            salt=_DOWNLOAD_CAPABILITY_SALT,
            max_age=max_age,
        )
    except (signing.BadSignature, signing.SignatureExpired, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "v",
        "m",
        "o",
        "f",
        "k",
        "i",
        "e",
    }:
        return None
    manager = payload.get("m")
    object_id = payload.get("o")
    field_name = payload.get("f")
    digest = payload.get("k")
    intent_id = payload.get("i")
    expires_at = payload.get("e")
    if (
        payload.get("v") != 1
        or not isinstance(manager, str)
        or not manager
        or len(manager) > 255
        or not isinstance(object_id, str)
        or not object_id
        or len(object_id) > 512
        or not isinstance(field_name, str)
        or not field_name
        or len(field_name) > 255
        or not isinstance(digest, str)
        or _SAFE_SHA256.fullmatch(digest) is None
        or not _valid_capability_intent_id(intent_id)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at <= int(now().timestamp())
    ):
        return None
    return payload


def _valid_capability_intent_id(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


IntentLookup = Callable[..., object | None]


@dataclass(slots=True, repr=False)
class StoredFileValue:
    """Lazy request-bound file output that never represents its storage key."""

    manager_class: type[object]
    manager_name: str
    object_id: str
    field_name: str
    key: str = field(repr=False)
    model_field: models.FileField = field(repr=False)
    intent_lookup: IntentLookup = field(repr=False)
    now: Callable[[], datetime] = field(repr=False)
    _cache: dict[str, object] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        return (
            f"<StoredFileValue manager={self.manager_name!r} field={self.field_name!r}>"
        )

    @property
    def _intent(self) -> object | None:
        if "intent" not in self._cache:
            self._cache["intent"] = self.intent_lookup(
                manager_name=self.manager_name,
                object_id=self.object_id,
                field_name=self.field_name,
                current_key=self.key,
            )
        return self._cache["intent"]

    @property
    def name(self) -> str:
        intent = self._intent
        original = getattr(intent, "original_filename", None)
        return _safe_basename(original if isinstance(original, str) else self.key)

    @property
    def original_name(self) -> str:
        return self.name

    @property
    def _exists(self) -> bool:
        if "exists" not in self._cache:
            try:
                intent = self._intent
                if (
                    intent is not None
                    and getattr(intent, "state", None)
                    == UploadIntentState.CONSUMED.value
                ):
                    version = self._exact_version
                    adapter = self._adapter
                    if version is None or adapter is None:
                        self._cache["exists"] = False
                    elif (
                        type(adapter).supports_direct(self.model_field.storage) is True
                        and not version.version_id
                    ):
                        self._cache["exists"] = False
                    else:
                        inspected = adapter.inspect_download(self.key, version)
                        self._cache["exists"] = (
                            inspected.checksum_sha256 == version.checksum_sha256
                            and inspected.size == version.size
                            and (
                                version.version_id is None
                                or inspected.version_id == version.version_id
                            )
                        )
                else:
                    self._cache["exists"] = (
                        self.model_field.storage.exists(self.key) is True
                    )
            except Exception:  # noqa: BLE001 - storage metadata fails closed
                self._cache["exists"] = False
        return cast(bool, self._cache["exists"])

    @property
    def _exact_version(self) -> ObjectVersion | None:
        if "exact_version" not in self._cache:
            self._cache["exact_version"] = _final_object_version(self._intent)
        return cast(ObjectVersion | None, self._cache["exact_version"])

    @property
    def _adapter(self) -> UploadAdapter | None:
        if "adapter" not in self._cache:
            try:
                from general_manager.uploads import services

                intent = self._intent
                if intent is not None and hasattr(intent, "adapter_id"):
                    adapter = services._resolve_intent_adapter(
                        cast(Any, intent), self.model_field
                    )
                else:
                    adapter = services.upload_adapter_registry.resolve(
                        self.model_field.storage
                    )
                    identity = services._validate_adapter_identity(adapter)
                    fingerprint = adapter.storage_fingerprint()
                    services._validate_storage_fingerprint(fingerprint)
                    if identity != (adapter.adapter_id, adapter.adapter_version):
                        adapter = None
                self._cache["adapter"] = adapter
            except Exception:  # noqa: BLE001 - adapter resolution fails closed
                self._cache["adapter"] = None
        return cast(UploadAdapter | None, self._cache["adapter"])

    @property
    def status(self) -> StoredFileStatus:
        intent = self._intent
        state = getattr(intent, "state", None)
        error = getattr(intent, "finalization_error_code", "")
        if state == UploadIntentState.FINALIZING.value:
            return StoredFileStatus.FAILED if error else StoredFileStatus.PROCESSING
        if state in {
            UploadIntentState.REJECTED.value,
            UploadIntentState.SUPERSEDED.value,
            UploadIntentState.EXPIRED.value,
        }:
            return StoredFileStatus.FAILED
        return StoredFileStatus.AVAILABLE if self._exists else StoredFileStatus.FAILED

    @property
    def size(self) -> int | None:
        intent = self._intent
        verified = getattr(intent, "verified_size", None)
        if (
            isinstance(verified, int)
            and not isinstance(verified, bool)
            and verified >= 0
        ):
            return verified
        if "size" not in self._cache:
            try:
                self._cache["size"] = int(self.model_field.storage.size(self.key))
            except Exception:  # noqa: BLE001 - unavailable metadata stays nullable
                self._cache["size"] = None
        return cast(int | None, self._cache["size"])

    @property
    def content_type(self) -> str | None:
        verified = getattr(self._intent, "verified_content_type", None)
        if isinstance(verified, str) and _SAFE_MEDIA_TYPE.fullmatch(verified.lower()):
            return verified.lower()
        guessed, _encoding = mimetypes.guess_type(self.name)
        if isinstance(guessed, str) and _SAFE_MEDIA_TYPE.fullmatch(guessed.lower()):
            return guessed.lower()
        return None

    @property
    def checksum(self) -> str | None:
        verified = getattr(self._intent, "verified_checksum_sha256", None)
        if isinstance(verified, str) and _SAFE_SHA256.fullmatch(verified):
            return verified
        return None

    @property
    def width(self) -> int | None:
        value = getattr(self._intent, "verified_width", None)
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
            else None
        )

    @property
    def height(self) -> int | None:
        value = getattr(self._intent, "verified_height", None)
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
            else None
        )

    def _download(self) -> tuple[str | None, datetime | None]:
        if "download" in self._cache:
            return cast(tuple[str | None, datetime | None], self._cache["download"])
        result = self._issue_download()
        self._cache["download"] = result
        return result

    def _issue_download(self) -> tuple[str | None, datetime | None]:
        if self.status is not StoredFileStatus.AVAILABLE:
            return None, None
        try:
            from general_manager.uploads import services

            configured = get_file_upload_settings()
            if not configured.enabled:
                return None, None
            intent = self._intent
            adapter = self._adapter
            if adapter is None:
                return None, None
            policy = services._resolve_policy(
                cast(Any, self.manager_class),
                self.field_name,
                configured,
            )
            version = self._exact_version
            if (
                intent is not None
                and getattr(intent, "state", None) == UploadIntentState.CONSUMED.value
                and version is None
            ):
                return None, None
            if policy.public is True:
                if adapter.supports_public_urls is not True:
                    return None, None
                if (
                    intent is not None
                    and getattr(intent, "state", None)
                    == UploadIntentState.CONSUMED.value
                ):
                    if version is None or not isinstance(
                        adapter, ExactPublicDownloadAdapter
                    ):
                        return None, None
                    url = adapter.public_download_url(self.key, version=version)
                    return (_safe_exact_public_url(url, version=version), None)
                return (
                    _safe_download_url(
                        adapter.public_url(self.key), allow_relative=True
                    ),
                    None,
                )

            expires_in = configured.download_url_ttl_seconds
            response_type = self.content_type or "application/octet-stream"
            response_disposition = _inline_disposition(self.name)
            try:
                url = adapter.private_download_url(
                    self.key,
                    expires_in=expires_in,
                    version=version,
                    response_content_type=response_type,
                    response_content_disposition=response_disposition,
                )
            except UploadBackendUnsupportedError:
                if type(adapter).supports_direct(self.model_field.storage) is True:
                    return None, None
                capability = issue_local_download_capability(
                    manager_name=self.manager_name,
                    object_id=self.object_id,
                    field_name=self.field_name,
                    current_key=self.key,
                    expires_in=expires_in,
                    intent_id=(
                        cast(UUID, cast(Any, intent).id)
                        if intent is not None
                        and getattr(intent, "state", None)
                        == UploadIntentState.CONSUMED.value
                        and isinstance(getattr(intent, "id", None), UUID)
                        else None
                    ),
                    now=self.now,
                )
                return capability.url, capability.expires_at
            safe_url = _safe_download_url(url, allow_relative=False)
            return safe_url, self.now() + timedelta(seconds=expires_in)
        except (UploadError, PublicUploadUrlUnsupportedError, ValueError, TypeError):
            return None, None
        except Exception:  # noqa: BLE001 - GraphQL output must fail closed
            return None, None

    @property
    def download_url(self) -> str | None:
        return self._download()[0]

    @property
    def download_url_expires_at(self) -> datetime | None:
        return self._download()[1]

    @property
    def expires_at(self) -> datetime | None:
        return self.download_url_expires_at


def _inline_disposition(filename: str) -> str:
    fallback = (
        "".join(
            character if 32 <= ord(character) < 127 else "_" for character in filename
        )
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )
    encoded = quote(filename, safe="!#$&+-.^_`|~")
    return f"inline; filename=\"{fallback}\"; filename*=utf-8''{encoded}"


def _safe_download_url(value: object, *, allow_relative: bool) -> str:
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise _InvalidDownloadValue
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise _InvalidDownloadValue
    if allow_relative and parsed.query:
        # A genuinely public URL needs no embedded signature or credential.
        raise _InvalidDownloadValue
    if (
        allow_relative
        and not parsed.scheme
        and not parsed.netloc
        and parsed.path.startswith("/")
    ):
        return value
    allow_http = bool(
        getattr(django_settings, "DEBUG", False)
        and get_file_upload_settings().allow_insecure_http
    )
    if parsed.scheme != "https" and not (allow_http and parsed.scheme == "http"):
        raise _InvalidDownloadValue
    if not parsed.netloc:
        raise _InvalidDownloadValue
    return value


def _safe_exact_public_url(value: object, *, version: ObjectVersion) -> str:
    """Accept a public immutable path or one exact non-credential S3 version query."""

    if not isinstance(value, str) or len(value) > 8192:
        raise _InvalidDownloadValue
    parsed = urlsplit(value)
    if not parsed.query:
        return _safe_download_url(value, allow_relative=True)
    if not version.version_id or len(parsed.query) > 2048:
        raise _InvalidDownloadValue
    try:
        parameters = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError:
        raise _InvalidDownloadValue from None
    if parameters != [("versionId", version.version_id)]:
        raise _InvalidDownloadValue
    queryless = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment)
    )
    _safe_download_url(queryless, allow_relative=True)
    return value


def _final_object_version(intent: object | None) -> ObjectVersion | None:
    metadata = getattr(intent, "final_object_version", None)
    if not isinstance(metadata, Mapping) or set(metadata) != {
        "version_id",
        "etag",
        "checksum_sha256",
        "size",
        "content_type",
    }:
        return None
    try:
        version = ObjectVersion(
            version_id=cast(str | None, metadata.get("version_id")),
            etag=cast(str | None, metadata.get("etag")),
            checksum_sha256=cast(str, metadata["checksum_sha256"]),
            size=cast(int, metadata["size"]),
            content_type=cast(str | None, metadata.get("content_type")),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if _SAFE_SHA256.fullmatch(version.checksum_sha256) is None:
        return None
    for identity in (version.version_id, version.etag):
        if identity is not None and (
            not isinstance(identity, str)
            or not identity
            or len(identity) > 1024
            or any(
                ord(character) < 32 or ord(character) == 127 for character in identity
            )
        ):
            return None
    if version.content_type is not None and (
        not isinstance(version.content_type, str)
        or _SAFE_MEDIA_TYPE.fullmatch(version.content_type.lower()) is None
    ):
        return None
    return version


def _default_intent_lookup(
    *,
    manager_name: str,
    object_id: str,
    field_name: str,
    current_key: str,
) -> object | None:
    from general_manager.uploads.models import UploadIntent

    configured = get_file_upload_settings()
    return (
        UploadIntent.objects.using(configured.intent_database)
        .filter(
            manager_name=manager_name,
            final_target_pk=object_id,
            field_name=field_name,
            final_key=current_key,
        )
        .order_by("-updated_at")
        .first()
    )


def _request_cache(context: object) -> dict[tuple[object, ...], StoredFileValue]:
    existing = getattr(context, _DOWNLOAD_CACHE_ATTRIBUTE, None)
    if isinstance(existing, dict):
        return cast(dict[tuple[object, ...], StoredFileValue], existing)
    created: dict[tuple[object, ...], StoredFileValue] = {}
    try:
        setattr(context, _DOWNLOAD_CACHE_ATTRIBUTE, created)
    except (AttributeError, TypeError):
        pass
    return created


def create_stored_file_value(
    manager: object,
    info: object,
    *,
    field_name: str,
    manager_name: str,
    intent_lookup: IntentLookup = _default_intent_lookup,
    now: Callable[[], datetime] = timezone.now,
) -> StoredFileValue | None:
    """Return a lazy structured file output for one current ORM binding."""

    manager_class = type(manager)
    interface = getattr(manager_class, "Interface", None)
    model = getattr(interface, "_model", None)
    if not isinstance(model, type) or not issubclass(model, models.Model):
        return None
    try:
        model_field = model._meta.get_field(field_name)
    except (LookupError, AttributeError):
        return None
    if not isinstance(model_field, models.FileField):
        return None
    typed_interface = cast(Any, interface)
    metadata = (
        typed_interface.get_attribute_types().get(field_name)
        if hasattr(typed_interface, "get_attribute_types")
        else None
    )
    expected_kind = "image" if isinstance(model_field, models.ImageField) else "file"
    if (
        isinstance(metadata, Mapping)
        and metadata.get("orm_field_kind") != expected_kind
    ):
        return None
    identification = getattr(manager, "identification", None)
    object_id = (
        identification.get("id") if isinstance(identification, Mapping) else None
    )
    row = getattr(getattr(manager, "_interface", None), "_instance", None)
    if not isinstance(row, model):
        alias = getattr(interface, "database", None) or DEFAULT_DB_ALIAS
        try:
            row = cast(Any, model)._base_manager.using(alias).get(pk=object_id)
        except Exception:  # noqa: BLE001 - output lookup fails closed
            return None
    raw_value = getattr(row, field_name, None)
    key = getattr(raw_value, "name", raw_value)
    if not isinstance(key, str) or not key:
        return None
    context = getattr(info, "context", None)
    cache = _request_cache(context) if context is not None else {}
    cache_key = (manager_class, str(row.pk), field_name, key)
    existing = cache.get(cache_key)
    if existing is not None:
        return existing
    value = StoredFileValue(
        manager_class=manager_class,
        manager_name=manager_name,
        object_id=str(row.pk),
        field_name=field_name,
        key=key,
        model_field=model_field,
        intent_lookup=intent_lookup,
        now=now,
    )
    cache[cache_key] = value
    return value


class StoredFile(ObjectType):
    """Client-visible metadata for a stored file."""

    name = String(required=True)
    original_name = String(required=True)
    size = BigIntScalar()
    content_type = String()
    checksum = String()
    download_url = String()
    expires_at = DateTime()
    status = Field(StoredFileStatusEnum, required=True)


class StoredImage(StoredFile):
    """Stored-file metadata with optional image dimensions."""

    width = Int()
    height = Int()
