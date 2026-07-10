"""Storage-agnostic upload adapter contract and proxy implementation."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
from io import BufferedIOBase
import json
import os
from tempfile import SpooledTemporaryFile
from threading import RLock
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
    UploadError,
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


def _filesystem_stat_identity(value: os.stat_result) -> str:
    """Encode the local inode identity observed through an open handle."""
    return (
        f"fs-v1:{value.st_dev}:{value.st_ino}:{value.st_mtime_ns}:{value.st_ctime_ns}"
    )


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


@dataclass(frozen=True, slots=True)
class ClaimedObject:
    """Intent-owned exact object handle used by retry-safe cleanup."""

    key: str
    version: ObjectVersion


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


@runtime_checkable
class UploadFinalizationAdapter(Protocol):
    """Exact-version operations required by the post-commit saga."""

    def inspect_materialized(
        self,
        final_key: str,
        source_version: ObjectVersion,
        *,
        intent_id: UUID,
    ) -> ObjectVersion:
        """Return the exact intent-owned destination version."""
        ...

    def delete_materialized(
        self,
        final_key: str,
        final_version: ObjectVersion,
        *,
        intent_id: UUID,
    ) -> None:
        """Delete only the exact destination version owned by ``intent_id``."""
        ...

    def delete_object(self, key: str, version: ObjectVersion) -> None:
        """Delete one exact non-staging object version or fail closed."""
        ...

    def inspect_replaced_object(self, key: str) -> ObjectVersion:
        """Inspect immutable identity for a potentially replaced object."""
        ...

    def plan_replaced_object_claim(
        self,
        key: str,
        version: ObjectVersion,
        *,
        cleanup_id: UUID,
    ) -> ClaimedObject:
        """Return a deterministic claim handle without accessing storage."""
        ...

    def claim_replaced_object(
        self,
        key: str,
        claimed: ClaimedObject,
        *,
        cleanup_id: UUID,
    ) -> None:
        """Idempotently bind an old object to a persisted cleanup handle."""
        ...

    def delete_claimed_object(
        self,
        claimed: ClaimedObject,
        *,
        cleanup_id: UUID,
    ) -> None:
        """Idempotently delete only an already-claimed object handle."""
        ...


@runtime_checkable
class ProxyUploadSink(UploadAdapter, Protocol):
    """Upload adapter that can accept a proxy stream through Django storage."""

    def save_stage(
        self,
        stage_key: str,
        chunks: Iterable[bytes],
        *,
        content_type: str | None,
        checksum_sha256: str | None = None,
        size: int | None = None,
    ) -> ObjectVersion:
        """Stream and verify one proxy upload into staging."""
        ...


UploadAdapterFactory = Callable[[Storage], UploadAdapter]


class ProxyUploadAdapter:
    """Universal adapter using only Django's bounded ``Storage`` API."""

    adapter_id: ClassVar[str] = "proxy"
    adapter_version: ClassVar[int] = 1
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
            version = ObjectVersion(
                version_id=None,
                etag=None,
                checksum_sha256=actual_checksum,
                size=byte_count,
                content_type=content_type,
            )
            claim_key = _stage_claim_marker(stage_key)
            completed_key = _stage_metadata_marker(stage_key)
            claim_identity = _stage_identity(version, state="claimed")
            completed_identity = _stage_identity(version, state="completed")

            if self._storage_exists(stage_key):
                if self._marker_matches(
                    claim_key,
                    claim_identity,
                ) and self._object_matches(stage_key, version):
                    self._acquire_marker(completed_key, completed_identity)
                    return version
                raise _exception(
                    UploadTransferConflictError,
                    "The reserved staging key is already occupied.",
                )
            if self._storage_exists(completed_key):
                raise _exception(
                    UploadTransferConflictError,
                    "The staged upload completion marker has no matching object.",
                )

            self._acquire_marker(claim_key, claim_identity)
            if self._storage_exists(stage_key):
                if self._object_matches(stage_key, version):
                    self._acquire_marker(completed_key, completed_identity)
                    return version
                raise _exception(
                    UploadTransferConflictError,
                    "The reserved staging key is already occupied.",
                )
            try:
                self._require_conditional_creation(stage_key)
            except UploadTransferConflictError:
                if self._object_matches(stage_key, version):
                    self._acquire_marker(completed_key, completed_identity)
                    return version
                raise
            raw.seek(0)
            saved_key = self._storage_save(
                stage_key,
                File(cast(BufferedIOBase, raw), name=stage_key),
            )
            if saved_key != stage_key:
                self._storage_delete(saved_key)
                if not self._object_matches(stage_key, version):
                    raise _exception(
                        UploadTransferConflictError,
                        "The reserved staging key is already occupied.",
                    )
            self._acquire_marker(completed_key, completed_identity)
            return version

    def inspect_staged(self, stage_key: str) -> ObjectVersion:
        with self._storage_open(stage_key) as staged:
            checksum, size = _checksum_stream(staged)
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
        claim_key = _materialization_marker(final_key)
        completed_key = _materialization_completed_marker(final_key)
        claim_identity = _materialization_identity(
            intent_id=intent_id,
            checksum_sha256=version.checksum_sha256,
            state="claimed",
        )
        completed_identity = _materialization_identity(
            intent_id=intent_id,
            checksum_sha256=version.checksum_sha256,
            state="completed",
        )

        if self._storage_exists(final_key):
            if self._marker_matches(claim_key, claim_identity) and self._object_matches(
                final_key,
                version,
            ):
                self._acquire_marker(completed_key, completed_identity)
                return final_key
            raise _exception(
                UploadTransferConflictError,
                "The requested final storage key is already occupied.",
            )

        self._acquire_marker(claim_key, claim_identity)
        if self._storage_exists(final_key):
            if self._object_matches(final_key, version):
                self._acquire_marker(completed_key, completed_identity)
                return final_key
            raise _exception(
                UploadTransferConflictError,
                "The requested final storage key is already occupied.",
            )

        try:
            self._require_conditional_creation(final_key)
        except UploadTransferConflictError:
            if self._object_matches(final_key, version):
                self._acquire_marker(completed_key, completed_identity)
                return final_key
            raise

        with SpooledTemporaryFile(max_size=self._spool_memory_limit, mode="w+b") as raw:
            with self._storage_open(stage_key) as staged:
                checksum, size = _copy_stream(staged, raw)
            if checksum != version.checksum_sha256 or size != version.size:
                raise UploadStorageChangedError()
            raw.seek(0)
            actual_key = self._storage_save(
                final_key,
                File(cast(BufferedIOBase, raw), name=final_key),
            )

        if actual_key != final_key:
            self._storage_delete(actual_key)
            if not self._object_matches(final_key, version):
                raise _exception(
                    UploadTransferConflictError,
                    "The requested final storage key is already occupied.",
                )
        self._acquire_marker(completed_key, completed_identity)
        return final_key

    def open_stage(self, stage_key: str, version: ObjectVersion) -> IO[bytes]:
        opened = self._storage_open(stage_key)
        try:
            seekable = opened.seekable()
        except (AttributeError, OSError):
            seekable = False
        if seekable:
            try:
                checksum, size = _checksum_stream(opened)
                _ensure_object_version(checksum, size, version)
                opened.seek(0)
            except Exception:
                opened.close()
                raise
            return opened

        spooled = SpooledTemporaryFile(
            max_size=self._spool_memory_limit,
            mode="w+b",
        )
        try:
            checksum, size = _copy_stream(opened, spooled)
        except Exception:
            spooled.close()
            raise
        finally:
            opened.close()
        try:
            _ensure_object_version(checksum, size, version)
        except UploadStorageChangedError:
            spooled.close()
            raise
        spooled.seek(0)
        return cast(IO[bytes], spooled)

    def delete_stage(
        self,
        stage_key: str,
        version: ObjectVersion | None = None,
    ) -> None:
        if version is None:
            self._storage_delete(stage_key)
            self._storage_delete(_stage_metadata_marker(stage_key))
            self._storage_delete(_stage_claim_marker(stage_key))
            return
        self.delete_object(stage_key, version)
        self._storage_delete(_stage_metadata_marker(stage_key))
        self._storage_delete(_stage_claim_marker(stage_key))

    def _delete_filesystem_object_exact(
        self,
        key: str,
        version: ObjectVersion,
    ) -> None:
        storage = self.storage
        if not isinstance(storage, FileSystemStorage):  # pragma: no cover - caller
            raise TypeError
        original_path = storage.path(key)
        quarantine_path = f"{original_path}.gm-delete-{version.checksum_sha256}"
        if os.path.exists(quarantine_path):
            self._delete_quarantine_exact(quarantine_path, version)
        if not self._storage_exists(key):
            return
        if not self._object_matches(key, version):
            raise UploadStorageChangedError()
        try:
            os.replace(original_path, quarantine_path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise _exception(
                UploadStorageError,
                "The filesystem backend could not claim an exact object version.",
            ) from exc
        try:
            self._delete_quarantine_exact(quarantine_path, version)
        except Exception:
            if os.path.exists(quarantine_path) and not os.path.exists(original_path):
                try:
                    os.replace(quarantine_path, original_path)
                except OSError:
                    pass
            raise

    @staticmethod
    def _delete_quarantine_exact(
        quarantine_path: str,
        version: ObjectVersion,
    ) -> None:
        try:
            with open(quarantine_path, "rb") as quarantined:
                checksum, size = _checksum_stream(quarantined)
            if checksum != version.checksum_sha256 or size != version.size:
                raise UploadStorageChangedError()
            os.unlink(quarantine_path)
        except UploadError:
            raise
        except OSError as exc:
            raise _exception(
                UploadStorageError,
                "The filesystem backend could not delete an exact object version.",
            ) from exc

    def delete_object(self, key: str, version: ObjectVersion) -> None:
        storage = self.storage
        if isinstance(storage, FileSystemStorage):
            self._delete_filesystem_object_exact(key, version)
            return
        try:
            supports_conditional_delete = storage.supports_atomic_conditional_delete  # type: ignore[attr-defined]
            conditional_delete = storage.delete_if_version  # type: ignore[attr-defined]
        except AttributeError:
            supports_conditional_delete = False
            conditional_delete = None
        if supports_conditional_delete is not True or not callable(conditional_delete):
            raise _exception(
                UploadBackendUnsupportedError,
                "The storage backend lacks atomic conditional deletion.",
            )
        try:
            deleted = conditional_delete(key, version)
        except OSError as exc:
            raise _exception(
                UploadStorageError,
                "The storage backend could not conditionally delete the object.",
            ) from exc
        if deleted is not True:
            raise UploadStorageChangedError()

    def plan_replaced_object_claim(
        self,
        key: str,
        version: ObjectVersion,
        *,
        cleanup_id: UUID,
    ) -> ClaimedObject:
        """Derive the intent-qualified local claim path without storage I/O."""
        key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return ClaimedObject(
            key=f"gm-upload-old-claims/{cleanup_id.hex}/{key_digest}",
            version=version,
        )

    def inspect_replaced_object(self, key: str) -> ObjectVersion:
        """Persist an opened local inode identity for replacement cleanup."""
        storage = self.storage
        if not isinstance(storage, FileSystemStorage):
            return self.inspect_staged(key)
        path = storage.path(key)
        try:
            with open(path, "rb") as stored:
                before = os.fstat(stored.fileno())
                checksum, size = _checksum_stream(stored)
                after = os.fstat(stored.fileno())
        except OSError as exc:
            raise UploadStorageError from exc
        if _filesystem_stat_identity(before) != _filesystem_stat_identity(after):
            raise UploadStorageChangedError()
        return ObjectVersion(
            version_id=_filesystem_stat_identity(before),
            etag=None,
            checksum_sha256=checksum,
            size=size,
            content_type=self._staged_content_type(key, checksum=checksum, size=size),
        )

    def claim_replaced_object(
        self,
        key: str,
        claimed: ClaimedObject,
        *,
        cleanup_id: UUID,
    ) -> None:
        del key, claimed, cleanup_id
        raise _exception(
            UploadBackendUnsupportedError,
            "Proxy storage cannot atomically claim a replaced object version.",
        )

    def delete_claimed_object(
        self,
        claimed: ClaimedObject,
        *,
        cleanup_id: UUID,
    ) -> None:
        del claimed, cleanup_id
        raise _exception(
            UploadBackendUnsupportedError,
            "Proxy storage cannot atomically delete a replaced object version.",
        )

    def inspect_materialized(
        self,
        final_key: str,
        source_version: ObjectVersion,
        *,
        intent_id: UUID,
    ) -> ObjectVersion:
        claim_identity = _materialization_identity(
            intent_id=intent_id,
            checksum_sha256=source_version.checksum_sha256,
            state="claimed",
        )
        completed_identity = _materialization_identity(
            intent_id=intent_id,
            checksum_sha256=source_version.checksum_sha256,
            state="completed",
        )
        if not self._marker_matches(
            _materialization_marker(final_key), claim_identity
        ) or not self._marker_matches(
            _materialization_completed_marker(final_key), completed_identity
        ):
            raise UploadStorageChangedError()
        inspected = self.inspect_staged(final_key)
        if (
            inspected.checksum_sha256 != source_version.checksum_sha256
            or inspected.size != source_version.size
        ):
            raise UploadStorageChangedError()
        return ObjectVersion(
            version_id=inspected.version_id,
            etag=inspected.etag,
            checksum_sha256=inspected.checksum_sha256,
            size=inspected.size,
            content_type=source_version.content_type,
        )

    def delete_materialized(
        self,
        final_key: str,
        final_version: ObjectVersion,
        *,
        intent_id: UUID,
    ) -> None:
        source_version = ObjectVersion(
            version_id=final_version.version_id,
            etag=final_version.etag,
            checksum_sha256=final_version.checksum_sha256,
            size=final_version.size,
            content_type=final_version.content_type,
        )
        inspected = self.inspect_materialized(
            final_key,
            source_version,
            intent_id=intent_id,
        )
        if inspected != final_version:
            raise UploadStorageChangedError()
        self.delete_object(final_key, final_version)
        self._storage_delete(_materialization_completed_marker(final_key))
        self._storage_delete(_materialization_marker(final_key))

    def private_download_url(self, key: str, *, expires_in: int) -> str:
        del key, expires_in
        raise _exception(
            UploadBackendUnsupportedError,
            "Proxy storage does not provide expiring private download URLs.",
        )

    def public_url(self, key: str) -> str:
        if not self.supports_public_urls:
            raise _exception(
                PublicUploadUrlUnsupportedError,
                "This storage was not explicitly configured as public.",
            )
        return self._storage_url(key)

    def storage_fingerprint(self) -> str:
        return build_storage_fingerprint(self.storage)

    def _storage_exists(self, key: str) -> bool:
        try:
            return self.storage.exists(key)
        except OSError as exc:
            raise _exception(
                UploadStorageError,
                "The storage backend could not inspect an object key.",
            ) from exc

    def _storage_open(self, key: str) -> IO[bytes]:
        try:
            return cast(IO[bytes], self.storage.open(key, "rb"))
        except OSError as exc:
            raise _exception(
                UploadStorageError,
                "The storage backend could not open an object.",
            ) from exc

    def _storage_save(self, key: str, content: object) -> str:
        try:
            return self.storage.save(key, content)  # type: ignore[arg-type]
        except OSError as exc:
            raise _exception(
                UploadStorageError,
                "The storage backend could not save an object.",
            ) from exc

    def _storage_delete(self, key: str) -> None:
        try:
            self.storage.delete(key)
        except OSError as exc:
            raise _exception(
                UploadStorageError,
                "The storage backend could not delete an object.",
            ) from exc

    def _storage_url(self, key: str) -> str:
        try:
            return self.storage.url(key)
        except OSError as exc:
            raise _exception(
                UploadStorageError,
                "The storage backend could not create an object URL.",
            ) from exc

    def _object_checksum(self, key: str) -> tuple[str, int]:
        with self._storage_open(key) as stored:
            return _checksum_stream(stored)

    def _object_matches(self, key: str, version: ObjectVersion) -> bool:
        if not self._storage_exists(key):
            return False
        checksum, size = self._object_checksum(key)
        return checksum == version.checksum_sha256 and size == version.size

    def _acquire_marker(self, key: str, identity: Mapping[str, object]) -> None:
        if self._storage_exists(key):
            if self._marker_matches(key, identity):
                return
            raise _exception(
                UploadTransferConflictError,
                "The upload materialization marker is already occupied.",
            )
        self._require_conditional_creation(key)
        payload = json.dumps(
            dict(identity),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        saved_key = self._storage_save(key, ContentFile(payload))
        if saved_key == key:
            return
        self._storage_delete(saved_key)
        if self._marker_matches(key, identity):
            return
        raise _exception(
            UploadTransferConflictError,
            "The upload materialization marker is already occupied.",
        )

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
        if self._storage_exists(key):
            raise _exception(
                UploadTransferConflictError,
                "The reserved storage key is already occupied.",
            )

    def _marker_matches(
        self,
        marker: str,
        identity: Mapping[str, object],
    ) -> bool:
        if not self._storage_exists(marker):
            return False
        try:
            with self._storage_open(marker) as stored:
                payload = stored.read(4097)
            if len(payload) > 4096:
                return False
            value = json.loads(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return False
        return bool(value == dict(identity))

    def _staged_content_type(
        self,
        stage_key: str,
        *,
        checksum: str,
        size: int,
    ) -> str | None:
        marker = _stage_metadata_marker(stage_key)
        if not self._storage_exists(marker):
            return None
        try:
            with self._storage_open(marker) as stored:
                payload = stored.read(4097)
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
        self._registrations: dict[type[Storage], UploadAdapterFactory] = {}
        self._lock = RLock()

    def register(
        self,
        storage_class: type[Storage],
        factory: UploadAdapterFactory,
    ) -> None:
        with self._lock:
            if storage_class in self._registrations:
                raise _exception(
                    AmbiguousUploadAdapterError,
                    f"An upload adapter is already registered for {storage_class!r}.",
                )
            self._registrations[storage_class] = factory

    def resolve(self, storage: Storage | None = None) -> UploadAdapter:
        resolved_storage = storage if storage is not None else storages["default"]
        with self._lock:
            explicit = self._resolve_explicit_factory(resolved_storage)
            if explicit is not None:
                return self._build_adapter(explicit, resolved_storage)

            from general_manager.uploads.s3 import S3UploadAdapter

            if S3UploadAdapter.supports_direct(resolved_storage):
                return S3UploadAdapter(resolved_storage)
            return ProxyUploadAdapter(resolved_storage)

    def resolve_by_id(
        self,
        adapter_id: str,
        adapter_version: int,
        storage: Storage | None = None,
    ) -> UploadAdapter | None:
        identity = (adapter_id, adapter_version)
        resolved_storage = storage if storage is not None else storages["default"]
        with self._lock:
            explicit = self._resolve_explicit_factory(resolved_storage)
            if explicit is not None:
                adapter = self._build_adapter(explicit, resolved_storage)
                if _adapter_identity(adapter) == identity:
                    return adapter
                return None
            if identity == (
                ProxyUploadAdapter.adapter_id,
                ProxyUploadAdapter.adapter_version,
            ):
                return ProxyUploadAdapter(resolved_storage)

            from general_manager.uploads.s3 import S3UploadAdapter

            if identity == (
                S3UploadAdapter.adapter_id,
                S3UploadAdapter.adapter_version,
            ):
                if S3UploadAdapter.supports_direct(resolved_storage):
                    return S3UploadAdapter(resolved_storage)
            return None

    def _resolve_explicit_factory(
        self,
        storage: Storage,
    ) -> UploadAdapterFactory | None:
        candidates = [
            (storage_class, factory)
            for storage_class, factory in self._registrations.items()
            if isinstance(storage, storage_class)
        ]
        if not candidates:
            return None
        distances = [
            (_inheritance_distance(type(storage), storage_class), factory)
            for storage_class, factory in candidates
        ]
        closest = min(distance for distance, _factory in distances)
        matches = [factory for distance, factory in distances if distance == closest]
        if len(matches) != 1:
            raise _exception(
                AmbiguousUploadAdapterError,
                f"Multiple upload adapters match {type(storage)!r} equally.",
            )
        return matches[0]

    @staticmethod
    def _build_adapter(
        factory: UploadAdapterFactory,
        storage: Storage,
    ) -> UploadAdapter:
        adapter = factory(storage)
        if not isinstance(adapter, UploadAdapter):
            raise _exception(
                TypeError,
                "Upload adapter factory must return an UploadAdapter.",
            )
        return adapter


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


def _ensure_object_version(
    checksum: str,
    size: int,
    version: ObjectVersion,
) -> None:
    if checksum != version.checksum_sha256 or size != version.size:
        raise UploadStorageChangedError()


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


def _materialization_completed_marker(key: str) -> str:
    identity = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"gm-upload-complete/{identity}.json"


def _materialization_identity(
    *,
    intent_id: UUID,
    checksum_sha256: str,
    state: str,
) -> dict[str, str]:
    return {
        "intent_id": str(intent_id),
        "checksum_sha256": checksum_sha256,
        "state": state,
    }


def _stage_metadata_marker(key: str) -> str:
    identity = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"gm-upload-stage-meta/{identity}.json"


def _stage_claim_marker(key: str) -> str:
    identity = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"gm-upload-stage-claim/{identity}.json"


def _stage_identity(
    version: ObjectVersion,
    *,
    state: str,
) -> dict[str, object]:
    return {
        "checksum_sha256": version.checksum_sha256,
        "content_type": version.content_type,
        "size": version.size,
        "state": state,
    }


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
