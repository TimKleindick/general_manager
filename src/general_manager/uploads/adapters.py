"""Storage-agnostic upload adapter contract and proxy implementation."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
from io import BufferedIOBase
import json
from tempfile import SpooledTemporaryFile
from types import MappingProxyType
from typing import IO, ClassVar, Protocol, TypeVar, cast, runtime_checkable
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from django.core.files import File
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, Storage, storages

from general_manager.uploads.errors import (
    UploadBackendUnsupportedError,
    UploadChecksumMismatchError,
    UploadStorageChangedError,
    UploadStorageError,
    UploadTransferConflictError,
)
from general_manager.uploads.types import ObjectVersion, UploadTransport


class AmbiguousUploadAdapterError(ValueError):
    """Raised when adapter registration or selection is not deterministic."""


class PublicUploadUrlUnsupportedError(ValueError):
    """Raised when a storage was not explicitly configured as public."""


_ExceptionT = TypeVar("_ExceptionT", bound=Exception)


def _exception(
    exception_type: type[_ExceptionT],
    message: str,
) -> _ExceptionT:
    """Build a contextual exception without duplicating local subclasses."""
    return exception_type(message)


@dataclass(frozen=True, slots=True)
class UploadInstructions:
    """Client-safe instructions for transferring one staged object."""

    transport: UploadTransport
    method: str
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )
    fields: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )

    def __post_init__(self) -> None:
        """Defensively copy caller-owned mappings into immutable views."""
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


@runtime_checkable
class UploadAdapter(Protocol):
    """Bounded interface implemented by durable upload storage adapters."""

    adapter_id: ClassVar[str]
    adapter_version: ClassVar[int]

    @property
    def supports_public_urls(self) -> bool:
        """Whether public URLs were explicitly enabled for this storage."""
        ...

    @classmethod
    def supports_direct(cls, storage: Storage) -> bool:
        """Return whether the backend can safely accept direct uploads."""
        ...

    def create_upload_instructions(
        self,
        *,
        stage_key: str,
        upload_url: str | None,
        content_type: str,
        size: int,
        checksum_sha256: str,
        headers: Mapping[str, str] | None = None,
        expires_in: int = 900,
    ) -> UploadInstructions:
        """Create client-safe transfer instructions for a private stage key."""
        ...

    def inspect_staged(self, stage_key: str) -> ObjectVersion:
        """Inspect and return the immutable identity of staged bytes."""
        ...

    def materialize(
        self,
        stage_key: str,
        version: ObjectVersion,
        final_key: str,
        *,
        intent_id: UUID,
    ) -> str:
        """Conditionally materialize an exact version and return its actual key."""
        ...

    def open_stage(self, stage_key: str, version: ObjectVersion) -> IO[bytes]:
        """Open one verified staged version for validation."""
        ...

    def delete_stage(
        self,
        stage_key: str,
        version: ObjectVersion | None = None,
    ) -> None:
        """Delete staged bytes, optionally constrained to an exact version."""
        ...

    def private_download_url(self, key: str, *, expires_in: int) -> str:
        """Return a private download URL supported by the backend."""
        ...

    def public_url(self, key: str) -> str:
        """Return a public URL only when explicitly enabled."""
        ...

    def storage_fingerprint(self) -> str:
        """Return a deterministic, non-secret storage identity."""
        ...


class ProxyUploadAdapter:
    """Universal adapter using only Django's bounded ``Storage`` API."""

    adapter_id = "proxy"
    adapter_version = 1
    _spool_memory_limit = 1024 * 1024

    def __init__(self, storage: Storage | None = None, *, public: bool = False) -> None:
        self._provided_storage = storage
        self._public = public

    @property
    def storage(self) -> Storage:
        """Resolve the current default lazily so setting overrides take effect."""
        if self._provided_storage is not None:
            return self._provided_storage
        return storages["default"]

    @property
    def supports_public_urls(self) -> bool:
        return self._public

    @classmethod
    def supports_direct(cls, storage: Storage) -> bool:
        return False

    def create_upload_instructions(
        self,
        *,
        stage_key: str,
        upload_url: str | None,
        content_type: str,
        size: int,
        checksum_sha256: str,
        headers: Mapping[str, str] | None = None,
        expires_in: int = 900,
    ) -> UploadInstructions:
        """Return opaque proxy instructions without exposing the stage key."""
        del stage_key, content_type, size, checksum_sha256, expires_in
        if upload_url is None:
            raise _exception(ValueError, "upload_url is required for proxy uploads.")
        return UploadInstructions(
            transport=UploadTransport.PROXY,
            method="PUT",
            url=upload_url,
            headers=headers or {},
        )

    def save_stage(
        self,
        stage_key: str,
        chunks: Iterable[bytes],
        *,
        content_type: str | None,
        checksum_sha256: str | None = None,
        size: int | None = None,
    ) -> ObjectVersion:
        """Stream chunks through a bounded spool and save without overwriting."""
        stage_marker = _stage_metadata_marker(stage_key)
        if self.storage.exists(stage_marker):
            raise _exception(
                UploadTransferConflictError,
                "The reserved staging identity marker is already occupied.",
            )
        self._require_conditional_creation(stage_key)
        with SpooledTemporaryFile(max_size=self._spool_memory_limit, mode="w+b") as raw:
            digest = hashlib.sha256()
            byte_count = 0
            for chunk in chunks:
                digest.update(chunk)
                raw.write(chunk)
                byte_count += len(chunk)
            actual_checksum = digest.hexdigest()
            if checksum_sha256 is not None and actual_checksum != checksum_sha256:
                raise UploadChecksumMismatchError()
            if size is not None and byte_count != size:
                raise _exception(
                    UploadStorageError,
                    "The staged upload size did not match.",
                )
            raw.seek(0)
            saved_key = self.storage.save(
                stage_key,
                File(cast(BufferedIOBase, raw), name=stage_key),
            )
        if saved_key != stage_key:
            self.storage.delete(saved_key)
            raise _exception(
                UploadTransferConflictError,
                "The reserved staging key is already occupied.",
            )
        metadata = json.dumps(
            {
                "checksum_sha256": actual_checksum,
                "content_type": content_type,
                "size": byte_count,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._require_conditional_creation(stage_marker)
        saved_marker = self.storage.save(
            stage_marker,
            ContentFile(metadata),
        )
        if saved_marker != stage_marker:
            self.storage.delete(saved_marker)
            self.storage.delete(stage_key)
            raise _exception(
                UploadTransferConflictError,
                "The reserved staging identity marker is already occupied.",
            )
        return ObjectVersion(
            version_id=None,
            etag=None,
            checksum_sha256=actual_checksum,
            size=byte_count,
            content_type=content_type,
        )

    def inspect_staged(self, stage_key: str) -> ObjectVersion:
        with self.storage.open(stage_key, "rb") as staged:
            checksum, size = _checksum_stream(cast(IO[bytes], staged))
        content_type = self._staged_content_type(
            stage_key, checksum=checksum, size=size
        )
        return ObjectVersion(
            version_id=None,
            etag=None,
            checksum_sha256=checksum,
            size=size,
            content_type=content_type,
        )

    def materialize(
        self,
        stage_key: str,
        version: ObjectVersion,
        final_key: str,
        *,
        intent_id: UUID,
    ) -> str:
        marker = _materialization_marker(final_key)
        if self.storage.exists(marker):
            if self.storage.exists(final_key) and self._marker_matches(
                marker,
                intent_id=intent_id,
                checksum_sha256=version.checksum_sha256,
            ):
                checksum, size = self._object_checksum(final_key)
                if checksum == version.checksum_sha256 and size == version.size:
                    return final_key
            raise _exception(
                UploadTransferConflictError,
                "The final upload identity marker is already occupied.",
            )

        self._require_conditional_creation(final_key)

        with SpooledTemporaryFile(max_size=self._spool_memory_limit, mode="w+b") as raw:
            with self.storage.open(stage_key, "rb") as staged:
                checksum, size = _copy_stream(cast(IO[bytes], staged), raw)
            if checksum != version.checksum_sha256 or size != version.size:
                raise UploadStorageChangedError()
            raw.seek(0)
            actual_key = self.storage.save(
                final_key,
                File(cast(BufferedIOBase, raw), name=final_key),
            )

        actual_marker = _materialization_marker(actual_key)
        marker_payload = json.dumps(
            {
                "intent_id": str(intent_id),
                "checksum_sha256": version.checksum_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if self.storage.exists(actual_marker):
            if self._marker_matches(
                actual_marker,
                intent_id=intent_id,
                checksum_sha256=version.checksum_sha256,
            ):
                return actual_key
            self.storage.delete(actual_key)
            raise _exception(
                UploadTransferConflictError,
                "The final upload identity marker is already occupied.",
            )
        self._require_conditional_creation(actual_marker)
        saved_marker = self.storage.save(
            actual_marker,
            ContentFile(marker_payload),
        )
        if saved_marker != actual_marker:
            self.storage.delete(saved_marker)
            if not self._marker_matches(
                actual_marker,
                intent_id=intent_id,
                checksum_sha256=version.checksum_sha256,
            ):
                self.storage.delete(actual_key)
                raise _exception(
                    UploadTransferConflictError,
                    "The final upload identity marker is already occupied.",
                )
        return actual_key

    def open_stage(self, stage_key: str, version: ObjectVersion) -> IO[bytes]:
        inspected = self.inspect_staged(stage_key)
        if (
            inspected.checksum_sha256 != version.checksum_sha256
            or inspected.size != version.size
        ):
            raise UploadStorageChangedError()
        return cast(IO[bytes], self.storage.open(stage_key, "rb"))

    def delete_stage(
        self,
        stage_key: str,
        version: ObjectVersion | None = None,
    ) -> None:
        if version is not None and self.storage.exists(stage_key):
            inspected = self.inspect_staged(stage_key)
            if (
                inspected.checksum_sha256 != version.checksum_sha256
                or inspected.size != version.size
            ):
                raise UploadStorageChangedError()
        self.storage.delete(stage_key)
        self.storage.delete(_stage_metadata_marker(stage_key))

    def private_download_url(self, key: str, *, expires_in: int) -> str:
        del expires_in
        return self.storage.url(key)

    def public_url(self, key: str) -> str:
        if not self.supports_public_urls:
            raise _exception(
                PublicUploadUrlUnsupportedError,
                "This storage was not explicitly configured as public.",
            )
        return self.storage.url(key)

    def storage_fingerprint(self) -> str:
        return build_storage_fingerprint(self.storage)

    def _object_checksum(self, key: str) -> tuple[str, int]:
        with self.storage.open(key, "rb") as stored:
            return _checksum_stream(cast(IO[bytes], stored))

    def _require_conditional_creation(
        self,
        key: str,
    ) -> None:
        """Fail closed when ``Storage.save`` could overwrite an existing key.

        Django's default filesystem backend uses atomic exclusive creation when
        ``allow_overwrite`` is false. Opaque backends may opt in only through an
        explicit atomic-create capability; ``exists()`` followed by ``save()``
        is not accepted because concurrent retries can race between those calls.
        """
        storage = self.storage
        if isinstance(storage, FileSystemStorage):
            if storage._allow_overwrite:
                raise _exception(
                    UploadBackendUnsupportedError,
                    "The storage backend permits overwriting existing object keys.",
                )
            return
        try:
            supports_atomic_create = storage.supports_atomic_conditional_create  # type: ignore[attr-defined]
        except AttributeError:
            supports_atomic_create = False
        if supports_atomic_create is not True:
            raise _exception(
                UploadBackendUnsupportedError,
                "The storage backend lacks atomic conditional creation.",
            )
        if storage.exists(key):
            raise _exception(
                UploadTransferConflictError,
                "The reserved storage key is already occupied.",
            )

    def _marker_matches(
        self,
        marker: str,
        *,
        intent_id: UUID,
        checksum_sha256: str,
    ) -> bool:
        if not self.storage.exists(marker):
            return False
        try:
            with self.storage.open(marker, "rb") as stored:
                payload = cast(IO[bytes], stored).read(4097)
            if len(payload) > 4096:
                return False
            value = json.loads(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return False
        return bool(
            value
            == {
                "intent_id": str(intent_id),
                "checksum_sha256": checksum_sha256,
            }
        )

    def _staged_content_type(
        self,
        stage_key: str,
        *,
        checksum: str,
        size: int,
    ) -> str | None:
        marker = _stage_metadata_marker(stage_key)
        if not self.storage.exists(marker):
            return None
        try:
            with self.storage.open(marker, "rb") as stored:
                payload = cast(IO[bytes], stored).read(4097)
            if len(payload) > 4096:
                return None
            value = json.loads(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(value, dict):
            return None
        if value.get("checksum_sha256") != checksum or value.get("size") != size:
            return None
        content_type = value.get("content_type")
        return content_type if isinstance(content_type, str) else None

    def __repr__(self) -> str:
        return (
            f"ProxyUploadAdapter(adapter_id={self.adapter_id!r}, "
            f"adapter_version={self.adapter_version!r}, "
            f"storage_fingerprint={self.storage_fingerprint()!r})"
        )


class UploadAdapterRegistry:
    """Resolve explicit adapters deterministically before safe built-ins."""

    def __init__(self) -> None:
        self._registrations: dict[type[Storage], object] = {}
        self._identities: dict[tuple[str, int], object] = {}
        self._identity_storage_classes: dict[tuple[str, int], set[type[Storage]]] = {}

    def register(self, storage_class: type[Storage], adapter: object) -> None:
        if storage_class in self._registrations:
            raise _exception(
                AmbiguousUploadAdapterError,
                f"An upload adapter is already registered for {storage_class!r}.",
            )
        identity = _adapter_identity(adapter)
        if identity is not None:
            existing = self._identities.get(identity)
            if existing is not None and existing is not adapter:
                raise _exception(
                    AmbiguousUploadAdapterError,
                    f"Upload adapter identity {identity!r} is already registered.",
                )
            self._identities[identity] = adapter
            self._identity_storage_classes.setdefault(identity, set()).add(
                storage_class
            )
        self._registrations[storage_class] = adapter

    def resolve(self, storage: Storage | None = None) -> object:
        resolved_storage = storage or storages["default"]
        explicit = self._resolve_explicit(resolved_storage)
        if explicit is not None:
            return explicit

        from general_manager.uploads.s3 import S3UploadAdapter

        if S3UploadAdapter.supports_direct(resolved_storage):
            return S3UploadAdapter(resolved_storage)
        return ProxyUploadAdapter(resolved_storage)

    def resolve_by_id(
        self,
        adapter_id: str,
        adapter_version: int,
        storage: Storage | None = None,
    ) -> object | None:
        identity = (adapter_id, adapter_version)
        explicit = self._identities.get(identity)
        resolved_storage = storage or storages["default"]
        if explicit is not None:
            registered_classes = self._identity_storage_classes[identity]
            if any(
                isinstance(resolved_storage, storage_class)
                for storage_class in registered_classes
            ):
                return explicit
            return None
        if identity == (
            ProxyUploadAdapter.adapter_id,
            ProxyUploadAdapter.adapter_version,
        ):
            return ProxyUploadAdapter(resolved_storage)

        from general_manager.uploads.s3 import S3UploadAdapter

        if identity == (S3UploadAdapter.adapter_id, S3UploadAdapter.adapter_version):
            if S3UploadAdapter.supports_direct(resolved_storage):
                return S3UploadAdapter(resolved_storage)
        return None

    def _resolve_explicit(self, storage: Storage) -> object | None:
        candidates = [
            (storage_class, adapter)
            for storage_class, adapter in self._registrations.items()
            if isinstance(storage, storage_class)
        ]
        if not candidates:
            return None
        distances = [
            (_inheritance_distance(type(storage), storage_class), adapter)
            for storage_class, adapter in candidates
        ]
        closest = min(distance for distance, _adapter in distances)
        matches = [adapter for distance, adapter in distances if distance == closest]
        if len(matches) != 1:
            raise _exception(
                AmbiguousUploadAdapterError,
                f"Multiple upload adapters match {type(storage)!r} equally.",
            )
        return matches[0]


def build_storage_fingerprint(
    storage: Storage,
    *,
    identity: Mapping[str, str] | None = None,
) -> str:
    """Hash only backend class and allowlisted, credential-free identity fields."""
    backend = type(storage)
    values: dict[str, str] = {
        "backend": f"{backend.__module__}.{backend.__qualname__}",
    }
    location = getattr(storage, "location", None)
    if location is not None:
        values["location"] = str(location)
    bucket = getattr(storage, "bucket_name", None)
    if bucket is not None:
        values["bucket"] = str(bucket)
    endpoint = getattr(storage, "endpoint_url", None)
    if endpoint:
        values["endpoint"] = _safe_endpoint(str(endpoint))
    if identity is not None:
        values.update(identity)
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def _adapter_identity(adapter: object) -> tuple[str, int] | None:
    adapter_id = getattr(adapter, "adapter_id", None)
    adapter_version = getattr(adapter, "adapter_version", None)
    if isinstance(adapter_id, str) and isinstance(adapter_version, int):
        return adapter_id, adapter_version
    return None


def _inheritance_distance(child: type[object], parent: type[object]) -> int:
    pending: deque[tuple[type[object], int]] = deque([(child, 0)])
    visited: set[type[object]] = set()
    while pending:
        current, distance = pending.popleft()
        if current in visited:
            continue
        visited.add(current)
        if current is parent:
            return distance
        pending.extend((base, distance + 1) for base in current.__bases__)
    return 1_000_000


def _checksum_stream(stream: IO[bytes]) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(64 * 1024):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _copy_stream(source: IO[bytes], destination: IO[bytes]) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(64 * 1024):
        digest.update(chunk)
        destination.write(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _materialization_marker(key: str) -> str:
    identity = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"gm-upload-meta/{identity}.json"


def _stage_metadata_marker(key: str) -> str:
    identity = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"gm-upload-stage-meta/{identity}.json"


def _safe_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "invalid"
    hostname = parsed.hostname or ""
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        port = ""
    return urlunsplit((parsed.scheme, f"{hostname}{port}", parsed.path, "", ""))
