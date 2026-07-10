from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
from pathlib import Path
from threading import Barrier
from typing import ClassVar
from uuid import UUID

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, Storage
from django.test import override_settings

from general_manager.uploads.adapters import (
    AmbiguousUploadAdapterError,
    ProxyUploadAdapter,
    ProxyUploadSink,
    PublicUploadUrlUnsupportedError,
    UploadAdapter,
    UploadAdapterRegistry,
    UploadFinalizationAdapter,
)
from general_manager.uploads import adapters as adapters_module
from general_manager.uploads.errors import (
    UploadBackendUnsupportedError,
    UploadChecksumMismatchError,
    UploadStorageError,
    UploadTransferConflictError,
)
from general_manager.uploads.types import ObjectVersion, UploadTransport


class NamedAdapter(ProxyUploadAdapter):
    adapter_id: ClassVar[str] = "tests.named"
    adapter_version: ClassVar[int] = 7

    def __init__(self, storage: Storage, *, label: str = "named") -> None:
        super().__init__(storage)
        self.label = label


class UnknownStorage(Storage):
    pass


class SecretBearingStorage(FileSystemStorage):
    access_key = "AKIA-DO-NOT-LOG"
    secret_key = "super-secret"  # noqa: S105 - verifies redaction
    endpoint_url = "https://username:password@objects.example.test/root?signature=bad"


class OpaqueOverwriteStorage(Storage):
    """Opaque fake whose ``_save`` overwrites and is therefore not atomic-safe."""

    def __init__(self, capability: object | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.save_attempts: list[str] = []
        self.delete_attempts: list[str] = []
        if capability is not None:
            self.supports_atomic_conditional_create = capability

    def _open(self, name: str, mode: str = "rb") -> BytesIO:
        del mode
        return BytesIO(self.objects[name])

    def _save(self, name: str, content: object) -> str:
        self.save_attempts.append(name)
        self.objects[name] = b"".join(content.chunks())  # type: ignore[attr-defined]
        return name

    def exists(self, name: str) -> bool:
        return name in self.objects

    def delete(self, name: str) -> None:
        self.delete_attempts.append(name)
        self.objects.pop(name, None)


class MutatingReadStorage(Storage):
    def __init__(self, original: bytes, replacement: bytes) -> None:
        self.payloads = [original, replacement]
        self.opened: list[BytesIO] = []

    def _open(self, name: str, mode: str = "rb") -> BytesIO:
        del name, mode
        payload = self.payloads[min(len(self.opened), len(self.payloads) - 1)]
        opened = BytesIO(payload)
        self.opened.append(opened)
        return opened

    def exists(self, name: str) -> bool:
        del name
        return False


class NonSeekableBytes:
    def __init__(self, payload: bytes) -> None:
        self._source = BytesIO(payload)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._source.read(size)

    def seekable(self) -> bool:
        return False

    def close(self) -> None:
        self.closed = True
        self._source.close()

    def __enter__(self) -> NonSeekableBytes:
        return self

    def __exit__(self, *args: object) -> None:
        del args
        self.close()


class NonSeekableReadStorage(Storage):
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.opened: list[NonSeekableBytes] = []

    def _open(self, name: str, mode: str = "rb") -> NonSeekableBytes:
        del name, mode
        opened = NonSeekableBytes(self.payload)
        self.opened.append(opened)
        return opened

    def exists(self, name: str) -> bool:
        del name
        return False


class FailingOperationStorage(Storage):
    supports_atomic_conditional_create = True

    def __init__(self, operation: str) -> None:
        self.operation = operation

    def _raise_if(self, operation: str) -> None:
        if self.operation == operation:
            raise OSError

    def _open(self, name: str, mode: str = "rb") -> BytesIO:
        del name, mode
        self._raise_if("open")
        return BytesIO(b"payload")

    def _save(self, name: str, content: object) -> str:
        del content
        self._raise_if("save")
        return name

    def exists(self, name: str) -> bool:
        del name
        return False

    def delete(self, name: str) -> None:
        del name
        self._raise_if("delete")

    def url(self, name: str) -> str:
        del name
        self._raise_if("url")
        return "/unused"


class SynchronizedFinalStorage(FileSystemStorage):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.final_key: str | None = None
        self.final_barrier = Barrier(2)

    def save(
        self,
        name: str,
        content: object,
        max_length: int | None = None,
    ) -> str:
        if name == self.final_key:
            self.final_barrier.wait(timeout=5)
        return super().save(name, content, max_length=max_length)  # type: ignore[arg-type]


class CrashBeforeKeyStorage(FileSystemStorage):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.crash_before: set[str] = set()

    def save(
        self,
        name: str,
        content: object,
        max_length: int | None = None,
    ) -> str:
        if name in self.crash_before:
            self.crash_before.remove(name)
            raise OSError
        return super().save(name, content, max_length=max_length)  # type: ignore[arg-type]


_UUID_STAGE_KEY = "gm-staging/9c90741f-72ce-4f34-886c-297bc019db16.bin"
_UUID_FINAL_KEY = "files/1aeff4c6-4895-4114-a984-b3d136083d33.bin"


def _intent_marker(prefix: str, final_key: str) -> str:
    digest = hashlib.sha256(final_key.encode()).hexdigest()
    return f"{prefix}/{digest}.json"


def _stage_marker(prefix: str, stage_key: str) -> str:
    digest = hashlib.sha256(stage_key.encode()).hexdigest()
    return f"{prefix}/{digest}.json"


def _filesystem_adapter(
    location: Path,
    *,
    public: bool = False,
) -> ProxyUploadAdapter:
    return ProxyUploadAdapter(
        FileSystemStorage(location=location, base_url="/media/"),
        public=public,
    )


def _overwrite_filesystem_adapter(location: Path) -> ProxyUploadAdapter:
    return ProxyUploadAdapter(
        FileSystemStorage(
            location=location,
            base_url="/media/",
            allow_overwrite=True,
        )
    )


def _proxy_version(
    payload: bytes, *, content_type: str = "text/plain"
) -> ObjectVersion:
    return ObjectVersion(
        version_id=None,
        etag=None,
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        content_type=content_type,
    )


def test_registry_prefers_most_specific_storage_class() -> None:
    registry = UploadAdapterRegistry()
    registry.register(Storage, lambda storage: NamedAdapter(storage, label="base"))
    registry.register(
        FileSystemStorage,
        lambda storage: NamedAdapter(storage, label="local"),
    )

    resolved = registry.resolve(FileSystemStorage())

    assert isinstance(resolved, NamedAdapter)
    assert resolved.label == "local"


def test_registry_rejects_ambiguous_registration() -> None:
    registry = UploadAdapterRegistry()
    registry.register(FileSystemStorage, lambda storage: NamedAdapter(storage))

    with pytest.raises(AmbiguousUploadAdapterError):
        registry.register(FileSystemStorage, lambda storage: NamedAdapter(storage))


def test_registry_resolves_registered_adapter_by_stable_id_and_version() -> None:
    registry = UploadAdapterRegistry()
    storage = UnknownStorage()
    registry.register(UnknownStorage, lambda value: NamedAdapter(value))

    resolved = registry.resolve_by_id("tests.named", 7, storage)

    assert isinstance(resolved, NamedAdapter)
    assert resolved.storage is storage
    assert registry.resolve_by_id("tests.named", 8, storage) is None


def test_registry_id_resolution_remains_bound_to_registered_storage_class(
    tmp_path: Path,
) -> None:
    registry = UploadAdapterRegistry()
    registry.register(UnknownStorage, lambda storage: NamedAdapter(storage))

    resolved = registry.resolve_by_id(
        "tests.named",
        7,
        FileSystemStorage(location=tmp_path),
    )

    assert resolved is None


def test_registry_factory_builds_distinct_storage_bound_adapters(
    tmp_path: Path,
) -> None:
    first_storage = FileSystemStorage(location=tmp_path / "first")
    second_storage = FileSystemStorage(location=tmp_path / "second")
    registry = UploadAdapterRegistry()
    registry.register(FileSystemStorage, lambda storage: NamedAdapter(storage))

    first = registry.resolve(first_storage)
    second = registry.resolve(second_storage)

    assert isinstance(first, NamedAdapter)
    assert isinstance(second, NamedAdapter)
    assert first is not second
    assert first.storage is first_storage
    assert second.storage is second_storage


def test_registry_factory_tracks_default_storage_overrides(tmp_path: Path) -> None:
    registry = UploadAdapterRegistry()
    registry.register(FileSystemStorage, lambda storage: NamedAdapter(storage))

    with override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": tmp_path / "first"},
            }
        }
    ):
        first = registry.resolve()
    with override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": tmp_path / "second"},
            }
        }
    ):
        second = registry.resolve()

    assert isinstance(first, NamedAdapter)
    assert isinstance(second, NamedAdapter)
    assert first is not second
    assert Path(first.storage.location) == tmp_path / "first"
    assert Path(second.storage.location) == tmp_path / "second"


def test_registry_rejects_factory_result_outside_adapter_protocol() -> None:
    registry = UploadAdapterRegistry()
    registry.register(FileSystemStorage, lambda _storage: object())  # type: ignore[arg-type,return-value]

    with pytest.raises(TypeError, match="UploadAdapter"):
        registry.resolve(FileSystemStorage())


def test_proxy_adapter_satisfies_upload_and_streaming_sink_protocols(
    tmp_path: Path,
) -> None:
    adapter = _filesystem_adapter(tmp_path)

    assert isinstance(adapter, UploadAdapter)
    assert isinstance(adapter, ProxyUploadSink)
    assert isinstance(adapter, UploadFinalizationAdapter)


def test_registry_uses_proxy_for_unknown_storage() -> None:
    adapter = UploadAdapterRegistry().resolve(UnknownStorage())

    assert isinstance(adapter, ProxyUploadAdapter)
    assert adapter.adapter_id == "proxy"
    assert adapter.adapter_version == 1


def test_proxy_lazily_tracks_default_storage_setting_overrides(tmp_path: Path) -> None:
    adapter = ProxyUploadAdapter()
    first = tmp_path / "first"
    second = tmp_path / "second"

    with override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": first},
            }
        }
    ):
        assert Path(adapter.storage.location) == first

    with override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": second},
            }
        }
    ):
        assert Path(adapter.storage.location) == second


def test_proxy_instruction_does_not_expose_stage_key_or_secret_url() -> None:
    adapter = ProxyUploadAdapter(UnknownStorage())
    secret_url = "/gm/uploads/opaque?authorization=secret"  # noqa: S105

    instructions = adapter.create_upload_instructions(
        stage_key="gm-staging/raw-private-key",
        upload_url=secret_url,
        content_type="text/plain",
        size=3,
        checksum_sha256="a" * 64,
        headers={"Authorization": "Bearer secret"},
    )

    assert instructions.transport is UploadTransport.PROXY
    assert instructions.url == secret_url
    assert "gm-staging/raw-private-key" not in repr(instructions)
    assert "authorization=secret" not in repr(instructions)
    assert "Bearer secret" not in repr(instructions)


def test_proxy_streams_chunks_and_records_checksum(tmp_path: Path) -> None:
    adapter = _filesystem_adapter(tmp_path)
    yielded: list[bytes] = []

    def chunks():
        for chunk in (b"hello ", b"chunked ", b"world"):
            yielded.append(chunk)
            yield chunk

    version = adapter.save_stage(
        "gm-staging/intent.bin",
        chunks(),
        content_type="application/octet-stream",
    )

    payload = b"hello chunked world"
    assert yielded == [b"hello ", b"chunked ", b"world"]
    assert version == ObjectVersion(
        version_id=None,
        etag=None,
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        content_type="application/octet-stream",
    )
    assert adapter.inspect_staged("gm-staging/intent.bin") == version


def test_filesystem_proxy_deletes_exact_intent_owned_materialized_version(
    tmp_path: Path,
) -> None:
    adapter = _filesystem_adapter(tmp_path)
    intent_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")
    stage_key = _UUID_STAGE_KEY
    final_key = _UUID_FINAL_KEY
    source_version = adapter.save_stage(
        stage_key,
        [b"owned payload"],
        content_type="application/octet-stream",
    )
    adapter.materialize(
        stage_key,
        source_version,
        final_key,
        intent_id=intent_id,
    )
    final_version = adapter.inspect_materialized(
        final_key,
        source_version,
        intent_id=intent_id,
    )

    adapter.delete_materialized(
        final_key,
        final_version,
        intent_id=intent_id,
    )

    assert not adapter.storage.exists(final_key)


def test_filesystem_exact_delete_restores_key_when_quarantine_unlink_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _filesystem_adapter(tmp_path)
    key = _UUID_STAGE_KEY
    version = adapter.save_stage(
        key,
        [b"retryable delete"],
        content_type="application/octet-stream",
    )
    real_unlink = adapters_module.os.unlink
    attempts = 0

    def fail_once(path: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError
        real_unlink(path)

    monkeypatch.setattr(adapters_module.os, "unlink", fail_once)

    with pytest.raises(UploadStorageError):
        adapter.delete_stage(key, version)
    assert adapter.storage.exists(key)

    adapter.delete_stage(key, version)
    assert not adapter.storage.exists(key)


def test_filesystem_replaced_object_cleanup_fails_closed_without_data_movement(
    tmp_path: Path,
) -> None:
    adapter = _filesystem_adapter(tmp_path)
    cleanup_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")
    old_key = "existing/retained-old.bin"
    adapter.storage.save(old_key, ContentFile(b"old payload"))
    version = adapter.inspect_replaced_object(old_key)
    claimed = adapter.plan_replaced_object_claim(
        old_key,
        version,
        cleanup_id=cleanup_id,
    )
    with pytest.raises(UploadBackendUnsupportedError):
        adapter.claim_replaced_object(old_key, claimed, cleanup_id=cleanup_id)

    assert adapter.storage.exists(old_key)
    assert not adapter.storage.exists(claimed.key)


def test_filesystem_cleanup_rejects_same_bytes_recreated_before_first_claim(
    tmp_path: Path,
) -> None:
    adapter = _filesystem_adapter(tmp_path)
    cleanup_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")
    old_key = "existing/recreated-before-claim.bin"
    adapter.storage.save(old_key, ContentFile(b"same bytes"))
    version = adapter.inspect_replaced_object(old_key)
    claimed = adapter.plan_replaced_object_claim(
        old_key,
        version,
        cleanup_id=cleanup_id,
    )
    adapter.storage.delete(old_key)
    adapter.storage.save(old_key, ContentFile(b"same bytes"))

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.claim_replaced_object(old_key, claimed, cleanup_id=cleanup_id)

    assert adapter.storage.exists(old_key)
    assert not adapter.storage.exists(claimed.key)


def test_filesystem_cleanup_claim_path_is_bounded_for_max_component_old_key(
    tmp_path: Path,
) -> None:
    adapter = _filesystem_adapter(tmp_path)
    cleanup_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")
    old_key = f"existing/{'x' * 240}.bin"
    adapter.storage.save(old_key, ContentFile(b"long-name payload"))
    version = adapter.inspect_replaced_object(old_key)

    claimed = adapter.plan_replaced_object_claim(
        old_key,
        version,
        cleanup_id=cleanup_id,
    )
    with pytest.raises(UploadBackendUnsupportedError):
        adapter.claim_replaced_object(old_key, claimed, cleanup_id=cleanup_id)

    assert claimed.key.startswith(f"gm-upload-old-claims/{cleanup_id.hex}/")
    assert max(map(len, claimed.key.split("/"))) <= 255
    assert len(claimed.key) < 1024
    assert adapter.storage.exists(old_key)


def test_proxy_concurrent_same_stage_identity_converges_on_requested_key(
    tmp_path: Path,
) -> None:
    storage = SynchronizedFinalStorage(location=tmp_path, base_url="/media/")
    adapter = ProxyUploadAdapter(storage)
    stage_key = "gm-staging/intent.bin"
    storage.final_key = stage_key

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = list(
            executor.map(
                lambda _index: adapter.save_stage(
                    stage_key,
                    [b"concurrent payload"],
                    content_type="text/plain",
                ),
                range(2),
            )
        )

    assert versions == [versions[0], versions[0]]
    with storage.open(stage_key, "rb") as staged:
        assert staged.read() == b"concurrent payload"
    assert [path.name for path in (tmp_path / "gm-staging").iterdir()] == ["intent.bin"]


def test_proxy_stage_retry_recovers_after_completion_marker_crash(
    tmp_path: Path,
) -> None:
    storage = CrashBeforeKeyStorage(location=tmp_path, base_url="/media/")
    adapter = ProxyUploadAdapter(storage)
    stage_key = "gm-staging/intent.bin"
    claim_key = _stage_marker("gm-upload-stage-claim", stage_key)
    completed_key = _stage_marker("gm-upload-stage-meta", stage_key)
    storage.crash_before.add(completed_key)

    with pytest.raises(UploadStorageError) as captured:
        adapter.save_stage(
            stage_key,
            [b"recoverable payload"],
            content_type="text/plain",
        )

    assert isinstance(captured.value.__cause__, OSError)
    assert storage.exists(claim_key)
    assert storage.exists(stage_key)
    assert not storage.exists(completed_key)

    recovered = adapter.save_stage(
        stage_key,
        [b"recoverable payload"],
        content_type="text/plain",
    )

    assert (
        recovered.checksum_sha256 == hashlib.sha256(b"recoverable payload").hexdigest()
    )
    assert storage.exists(completed_key)


def test_proxy_stage_retry_rejects_conflicting_bytes_after_claim_crash(
    tmp_path: Path,
) -> None:
    storage = CrashBeforeKeyStorage(location=tmp_path, base_url="/media/")
    adapter = ProxyUploadAdapter(storage)
    stage_key = "gm-staging/intent.bin"
    claim_key = _stage_marker("gm-upload-stage-claim", stage_key)
    storage.crash_before.add(stage_key)

    with pytest.raises(UploadStorageError):
        adapter.save_stage(
            stage_key,
            [b"expected payload"],
            content_type="text/plain",
        )

    assert storage.exists(claim_key)
    storage.save(stage_key, ContentFile(b"conflicting payload"))

    with pytest.raises(UploadTransferConflictError):
        adapter.save_stage(
            stage_key,
            [b"expected payload"],
            content_type="text/plain",
        )


def test_proxy_rejects_checksum_mismatch_before_saving(tmp_path: Path) -> None:
    adapter = _filesystem_adapter(tmp_path)

    with pytest.raises(UploadChecksumMismatchError):
        adapter.save_stage(
            "gm-staging/intent.bin",
            [b"payload"],
            content_type="text/plain",
            checksum_sha256="0" * 64,
        )

    assert not adapter.storage.exists("gm-staging/intent.bin")


def test_proxy_stage_save_does_not_overwrite_collision(tmp_path: Path) -> None:
    adapter = _filesystem_adapter(tmp_path)
    adapter.storage.save("gm-staging/intent.bin", ContentFile(b"original"))

    with pytest.raises(UploadTransferConflictError):
        adapter.save_stage(
            "gm-staging/intent.bin",
            [b"replacement"],
            content_type="text/plain",
        )

    with adapter.storage.open("gm-staging/intent.bin", "rb") as stored:
        assert stored.read() == b"original"
    assert list((tmp_path / "gm-staging").iterdir()) == [
        tmp_path / "gm-staging" / "intent.bin"
    ]


def test_proxy_rejects_opaque_storage_for_uuid_stage_before_write() -> None:
    storage = OpaqueOverwriteStorage()
    adapter = ProxyUploadAdapter(storage)

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.save_stage(
            _UUID_STAGE_KEY,
            [b"payload"],
            content_type="text/plain",
        )

    assert storage.save_attempts == []


def test_proxy_rejects_opaque_storage_for_uuid_final_before_write() -> None:
    storage = OpaqueOverwriteStorage()
    payload = b"staged payload"
    storage.objects[_UUID_STAGE_KEY] = payload
    adapter = ProxyUploadAdapter(storage)

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.materialize(
            _UUID_STAGE_KEY,
            _proxy_version(payload),
            _UUID_FINAL_KEY,
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )

    assert storage.save_attempts == []
    assert _UUID_FINAL_KEY not in storage.objects


def test_proxy_rejects_opaque_storage_for_framework_marker_before_write() -> None:
    storage = OpaqueOverwriteStorage()
    adapter = ProxyUploadAdapter(storage)
    marker_digest = hashlib.sha256(_UUID_FINAL_KEY.encode()).hexdigest()
    marker_key = f"gm-upload-meta/{marker_digest}.json"

    with pytest.raises(UploadBackendUnsupportedError):
        adapter._require_conditional_creation(marker_key)

    assert storage.save_attempts == []


@pytest.mark.parametrize("capability", [False, 1, "yes"])
def test_proxy_requires_atomic_create_capability_to_be_exactly_true(
    capability: object,
) -> None:
    storage = OpaqueOverwriteStorage(capability)
    adapter = ProxyUploadAdapter(storage)

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.save_stage(
            _UUID_STAGE_KEY,
            [b"payload"],
            content_type="text/plain",
        )

    assert storage.save_attempts == []


def test_proxy_rejects_overwrite_enabled_storage_before_occupied_stage_write(
    tmp_path: Path,
) -> None:
    adapter = _overwrite_filesystem_adapter(tmp_path)
    adapter.storage.save("gm-staging/intent.bin", ContentFile(b"original"))

    with pytest.raises(UploadTransferConflictError):
        adapter.save_stage(
            "gm-staging/intent.bin",
            [b"replacement"],
            content_type="text/plain",
        )

    with adapter.storage.open("gm-staging/intent.bin", "rb") as stored:
        assert stored.read() == b"original"


def test_proxy_rejects_overwrite_enabled_storage_before_occupied_final_write(
    tmp_path: Path,
) -> None:
    adapter = _overwrite_filesystem_adapter(tmp_path)
    payload = b"staged payload"
    adapter.storage.save("gm-staging/intent.bin", ContentFile(payload))
    adapter.storage.save("files/report.txt", ContentFile(b"unrelated"))

    with pytest.raises(UploadTransferConflictError):
        adapter.materialize(
            "gm-staging/intent.bin",
            _proxy_version(payload),
            "files/report.txt",
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )

    with adapter.storage.open("files/report.txt", "rb") as stored:
        assert stored.read() == b"unrelated"


def test_proxy_rejects_overwrite_enabled_storage_before_conflicting_marker_write(
    tmp_path: Path,
) -> None:
    adapter = _overwrite_filesystem_adapter(tmp_path)
    payload = b"staged payload"
    final_key = "files/report.txt"
    marker_digest = hashlib.sha256(final_key.encode()).hexdigest()
    marker_key = f"gm-upload-meta/{marker_digest}.json"
    adapter.storage.save("gm-staging/intent.bin", ContentFile(payload))
    adapter.storage.save(marker_key, ContentFile(b'{"intent_id":"another"}'))

    with pytest.raises(UploadTransferConflictError):
        adapter.materialize(
            "gm-staging/intent.bin",
            _proxy_version(payload),
            final_key,
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )

    assert not adapter.storage.exists(final_key)
    with adapter.storage.open(marker_key, "rb") as stored:
        assert stored.read() == b'{"intent_id":"another"}'


def test_proxy_materialization_rejects_unrelated_requested_key_without_alternate(
    tmp_path: Path,
) -> None:
    adapter = _filesystem_adapter(tmp_path)
    version = adapter.save_stage(
        "gm-staging/intent.bin",
        [b"new payload"],
        content_type="text/plain",
    )
    adapter.storage.save("files/report.txt", ContentFile(b"unrelated"))
    intent_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")

    with pytest.raises(UploadTransferConflictError):
        adapter.materialize(
            "gm-staging/intent.bin",
            version,
            "files/report.txt",
            intent_id=intent_id,
        )

    with adapter.storage.open("files/report.txt", "rb") as unrelated:
        assert unrelated.read() == b"unrelated"
    assert [path.name for path in (tmp_path / "files").iterdir()] == ["report.txt"]


def test_proxy_concurrent_same_intent_materializes_one_requested_key(
    tmp_path: Path,
) -> None:
    storage = SynchronizedFinalStorage(location=tmp_path, base_url="/media/")
    adapter = ProxyUploadAdapter(storage)
    version = adapter.save_stage(
        "gm-staging/intent.bin",
        [b"new payload"],
        content_type="text/plain",
    )
    final_key = "files/report.txt"
    storage.final_key = final_key
    intent_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: adapter.materialize(
                    "gm-staging/intent.bin",
                    version,
                    final_key,
                    intent_id=intent_id,
                ),
                range(2),
            )
        )

    assert results == [final_key, final_key]
    assert [path.name for path in (tmp_path / "files").iterdir()] == ["report.txt"]


def test_proxy_retry_resumes_after_claim_before_data_crash(tmp_path: Path) -> None:
    storage = CrashBeforeKeyStorage(location=tmp_path, base_url="/media/")
    adapter = ProxyUploadAdapter(storage)
    version = adapter.save_stage(
        "gm-staging/intent.bin",
        [b"new payload"],
        content_type="text/plain",
    )
    final_key = "files/report.txt"
    intent_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")
    claim_key = _intent_marker("gm-upload-meta", final_key)
    completed_key = _intent_marker("gm-upload-complete", final_key)
    storage.crash_before.add(final_key)

    with pytest.raises(UploadStorageError) as captured:
        adapter.materialize(
            "gm-staging/intent.bin",
            version,
            final_key,
            intent_id=intent_id,
        )
    assert isinstance(captured.value.__cause__, OSError)

    assert storage.exists(claim_key)
    assert not storage.exists(final_key)
    assert not storage.exists(completed_key)
    with storage.open(claim_key, "rb") as claim:
        assert json.load(claim)["state"] == "claimed"

    assert (
        adapter.materialize(
            "gm-staging/intent.bin",
            version,
            final_key,
            intent_id=intent_id,
        )
        == final_key
    )
    assert storage.exists(completed_key)


def test_proxy_retry_completes_after_data_before_marker_crash(tmp_path: Path) -> None:
    storage = CrashBeforeKeyStorage(location=tmp_path, base_url="/media/")
    adapter = ProxyUploadAdapter(storage)
    version = adapter.save_stage(
        "gm-staging/intent.bin",
        [b"new payload"],
        content_type="text/plain",
    )
    final_key = "files/report.txt"
    intent_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")
    completed_key = _intent_marker("gm-upload-complete", final_key)
    storage.crash_before.add(completed_key)

    with pytest.raises(UploadStorageError) as captured:
        adapter.materialize(
            "gm-staging/intent.bin",
            version,
            final_key,
            intent_id=intent_id,
        )
    assert isinstance(captured.value.__cause__, OSError)

    assert storage.exists(final_key)
    assert not storage.exists(completed_key)

    assert (
        adapter.materialize(
            "gm-staging/intent.bin",
            version,
            final_key,
            intent_id=intent_id,
        )
        == final_key
    )
    with storage.open(completed_key, "rb") as completed:
        assert json.load(completed)["state"] == "completed"


def test_proxy_rejects_existing_marker_for_another_intent_without_writing(
    tmp_path: Path,
) -> None:
    adapter = _filesystem_adapter(tmp_path)
    version = adapter.save_stage(
        "gm-staging/intent.bin",
        [b"new payload"],
        content_type="text/plain",
    )
    first_key = adapter.materialize(
        "gm-staging/intent.bin",
        version,
        "files/report.txt",
        intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
    )

    with pytest.raises(UploadTransferConflictError):
        adapter.materialize(
            "gm-staging/intent.bin",
            version,
            first_key,
            intent_id=UUID("1aeff4c6-4895-4114-a984-b3d136083d33"),
        )

    assert [path.name for path in (tmp_path / "files").iterdir()] == ["report.txt"]


def test_proxy_opens_deletes_and_exposes_urls_only_when_explicit(
    tmp_path: Path,
) -> None:
    private = _filesystem_adapter(tmp_path / "private")
    version = private.save_stage(
        "gm-staging/intent.bin",
        [b"payload"],
        content_type="text/plain",
    )

    with private.open_stage("gm-staging/intent.bin", version) as staged:
        assert staged.read() == b"payload"
    with pytest.raises(UploadBackendUnsupportedError):
        private.private_download_url("gm-staging/intent.bin", expires_in=60)
    assert private.supports_public_urls is False
    with pytest.raises(PublicUploadUrlUnsupportedError):
        private.public_url("gm-staging/intent.bin")
    completion_marker = _stage_marker("gm-upload-stage-meta", "gm-staging/intent.bin")
    claim_marker = _stage_marker("gm-upload-stage-claim", "gm-staging/intent.bin")
    assert private.storage.exists(completion_marker)
    assert private.storage.exists(claim_marker)
    private.delete_stage("gm-staging/intent.bin", version)
    assert not private.storage.exists("gm-staging/intent.bin")
    assert not private.storage.exists(completion_marker)
    assert not private.storage.exists(claim_marker)

    public = _filesystem_adapter(tmp_path / "public", public=True)
    assert public.supports_public_urls is True
    assert public.public_url("files/image.png") == "/media/files/image.png"


def test_proxy_open_stage_verifies_and_rewinds_same_handle() -> None:
    original = b"verified payload"
    storage = MutatingReadStorage(original, b"mutated payload")
    adapter = ProxyUploadAdapter(storage)

    opened = adapter.open_stage("gm-staging/intent.bin", _proxy_version(original))

    assert opened is storage.opened[0]
    assert len(storage.opened) == 1
    assert opened.read() == original


def test_proxy_open_stage_spools_non_seekable_source_once() -> None:
    payload = b"non-seekable payload"
    storage = NonSeekableReadStorage(payload)
    adapter = ProxyUploadAdapter(storage)

    opened = adapter.open_stage("gm-staging/intent.bin", _proxy_version(payload))

    assert len(storage.opened) == 1
    assert storage.opened[0].closed is True
    assert opened.seekable() is True
    assert opened.read() == payload


def test_proxy_exact_delete_fails_before_mutable_backend_delete() -> None:
    payload = b"verified payload"
    storage = OpaqueOverwriteStorage()
    storage.objects[_UUID_STAGE_KEY] = payload
    adapter = ProxyUploadAdapter(storage)

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.delete_stage(_UUID_STAGE_KEY, _proxy_version(payload))

    assert storage.objects[_UUID_STAGE_KEY] == payload
    assert storage.delete_attempts == []


@pytest.mark.parametrize("operation", ["save", "open", "delete", "url"])
def test_proxy_normalizes_backend_os_errors(operation: str) -> None:
    adapter = ProxyUploadAdapter(
        FailingOperationStorage(operation),
        public=operation == "url",
    )

    with pytest.raises(UploadStorageError) as captured:
        if operation == "save":
            adapter.save_stage(
                _UUID_STAGE_KEY,
                [b"payload"],
                content_type="text/plain",
            )
        elif operation == "open":
            adapter.open_stage(_UUID_STAGE_KEY, _proxy_version(b"payload"))
        elif operation == "delete":
            adapter.delete_stage(_UUID_STAGE_KEY)
        else:
            adapter.public_url(_UUID_FINAL_KEY)

    assert isinstance(captured.value.__cause__, OSError)


def test_proxy_fingerprint_and_repr_exclude_storage_secrets(tmp_path: Path) -> None:
    storage = SecretBearingStorage(location=tmp_path, base_url="/media/")
    first = ProxyUploadAdapter(storage)
    second = ProxyUploadAdapter(storage)

    fingerprint = first.storage_fingerprint()
    loggable = f"{first!r} {fingerprint}"

    assert fingerprint == second.storage_fingerprint()
    assert fingerprint.startswith("sha256:")
    assert "AKIA-DO-NOT-LOG" not in loggable
    assert "super-secret" not in loggable
    assert "username" not in loggable
    assert "password" not in loggable
    assert "signature" not in loggable
