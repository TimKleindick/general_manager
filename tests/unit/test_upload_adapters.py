from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, Storage
from django.test import override_settings

from general_manager.uploads.adapters import (
    AmbiguousUploadAdapterError,
    ProxyUploadAdapter,
    PublicUploadUrlUnsupportedError,
    UploadAdapterRegistry,
)
from general_manager.uploads.errors import (
    UploadChecksumMismatchError,
    UploadTransferConflictError,
)
from general_manager.uploads.types import ObjectVersion, UploadTransport


@dataclass(frozen=True)
class NamedAdapter:
    adapter_id: ClassVar[str] = "tests.named"
    adapter_version: ClassVar[int] = 7


class UnknownStorage(Storage):
    pass


class SecretBearingStorage(FileSystemStorage):
    access_key = "AKIA-DO-NOT-LOG"
    secret_key = "super-secret"  # noqa: S105 - verifies redaction
    endpoint_url = "https://username:password@objects.example.test/root?signature=bad"


def _filesystem_adapter(
    location: Path,
    *,
    public: bool = False,
) -> ProxyUploadAdapter:
    return ProxyUploadAdapter(
        FileSystemStorage(location=location, base_url="/media/"),
        public=public,
    )


def test_registry_prefers_most_specific_storage_class() -> None:
    base_adapter = object()
    local_adapter = object()
    registry = UploadAdapterRegistry()
    registry.register(Storage, base_adapter)
    registry.register(FileSystemStorage, local_adapter)

    assert registry.resolve(FileSystemStorage()) is local_adapter


def test_registry_rejects_ambiguous_registration() -> None:
    registry = UploadAdapterRegistry()
    registry.register(FileSystemStorage, object())

    with pytest.raises(AmbiguousUploadAdapterError):
        registry.register(FileSystemStorage, object())


def test_registry_resolves_registered_adapter_by_stable_id_and_version() -> None:
    adapter = NamedAdapter()
    registry = UploadAdapterRegistry()
    registry.register(UnknownStorage, adapter)

    assert registry.resolve_by_id("tests.named", 7, UnknownStorage()) is adapter
    assert registry.resolve_by_id("tests.named", 8, UnknownStorage()) is None


def test_registry_id_resolution_remains_bound_to_registered_storage_class(
    tmp_path: Path,
) -> None:
    registry = UploadAdapterRegistry()
    registry.register(UnknownStorage, NamedAdapter())

    resolved = registry.resolve_by_id(
        "tests.named",
        7,
        FileSystemStorage(location=tmp_path),
    )

    assert resolved is None


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


def test_proxy_materialization_returns_collision_safe_actual_key_and_retries(
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

    actual_key = adapter.materialize(
        "gm-staging/intent.bin",
        version,
        "files/report.txt",
        intent_id=intent_id,
    )
    retried_key = adapter.materialize(
        "gm-staging/intent.bin",
        version,
        actual_key,
        intent_id=intent_id,
    )

    assert actual_key != "files/report.txt"
    assert retried_key == actual_key
    with adapter.storage.open("files/report.txt", "rb") as unrelated:
        assert unrelated.read() == b"unrelated"
    with adapter.storage.open(actual_key, "rb") as materialized:
        assert materialized.read() == b"new payload"


def test_proxy_does_not_accept_existing_object_for_another_intent(
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

    second_key = adapter.materialize(
        "gm-staging/intent.bin",
        version,
        first_key,
        intent_id=UUID("1aeff4c6-4895-4114-a984-b3d136083d33"),
    )

    assert second_key != first_key


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
    assert private.private_download_url("gm-staging/intent.bin", expires_in=60) == (
        "/media/gm-staging/intent.bin"
    )
    assert private.supports_public_urls is False
    with pytest.raises(PublicUploadUrlUnsupportedError):
        private.public_url("gm-staging/intent.bin")
    private.delete_stage("gm-staging/intent.bin", version)
    assert not private.storage.exists("gm-staging/intent.bin")

    public = _filesystem_adapter(tmp_path / "public", public=True)
    assert public.supports_public_urls is True
    assert public.public_url("files/image.png") == "/media/files/image.png"


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
