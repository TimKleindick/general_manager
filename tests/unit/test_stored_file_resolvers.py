"""Tests for lazy, structured GraphQL file output values."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from django.core.files.storage import Storage
from django.db import models
from django.db.models import NOT_PROVIDED
from django.test import override_settings
from django.utils import timezone

from general_manager.uploads.graphql_types import (
    StoredFile,
    StoredImage,
    create_stored_file_value,
)
from general_manager.uploads.adapters import ProxyUploadAdapter, UploadAdapterRegistry
from general_manager.uploads.config import FileUploadPolicy
from general_manager.uploads import services
from general_manager.uploads.types import StoredFileStatus, UploadIntentState
from general_manager.uploads.types import ObjectVersion
from general_manager.uploads.errors import UploadStorageChangedError
from general_manager.api.graphql import GraphQL
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input


class ResolverStorage(Storage):
    def __init__(self) -> None:
        self.exists_calls = 0
        self.size_calls = 0

    def exists(self, name: str) -> bool:
        del name
        self.exists_calls += 1
        return True

    def size(self, name: str) -> int:
        del name
        self.size_calls += 1
        return 41


_STORAGE = ResolverStorage()


class ResolverRecord(models.Model):
    document = models.FileField(storage=_STORAGE, upload_to="documents/", blank=True)
    image = models.ImageField(storage=_STORAGE, upload_to="images/", blank=True)

    class Meta:
        app_label = "general_manager"
        managed = False


class ResolverInterface:
    _model = ResolverRecord
    database = None


class ResolverManager:
    Interface = ResolverInterface

    def __init__(self, row: ResolverRecord) -> None:
        self.identification = {"id": row.pk}
        self._interface = SimpleNamespace(_instance=row)


class ResolverGraphInterface(OrmInterfaceBase[ResolverRecord]):
    _model = ResolverRecord
    database: ClassVar[str | None] = None
    input_fields: ClassVar[dict[str, Input[type[object]]]] = {
        "id": Input(int)  # type: ignore[dict-item]
    }

    @classmethod
    def get_attribute_types(cls) -> dict[str, dict[str, object]]:
        common = {
            "type": str,
            "is_required": False,
            "is_editable": True,
            "is_derived": False,
            "default": NOT_PROVIDED,
        }
        return {
            "document": {**common, "orm_field_kind": "file"},
            "image": {**common, "orm_field_kind": "image"},
        }


class ResolverGraphManager(GeneralManager):
    _attributes: ClassVar[dict[str, object]] = {}


ResolverGraphManager.Interface = ResolverGraphInterface  # type: ignore[assignment]
ResolverGraphInterface._parent_class = ResolverGraphManager


class DirectDownloadAdapter(ProxyUploadAdapter):
    adapter_id = "tests.direct-download"
    adapter_version = 1

    def __init__(
        self, storage: Storage, *, url: str = "https://signed.test/file"
    ) -> None:
        super().__init__(storage)
        self.url = url
        self.private_calls: list[dict[str, object]] = []
        self.fail_exact_inspection = False

    def private_download_url(self, key: str, **kwargs: object) -> str:
        self.private_calls.append({"key": key, **kwargs})
        return self.url

    def inspect_download(
        self,
        key: str,
        version: ObjectVersion,
    ) -> ObjectVersion:
        del key
        if self.fail_exact_inspection:
            raise UploadStorageChangedError
        return version


class PublicDownloadAdapter(DirectDownloadAdapter):
    @property
    def supports_public_urls(self) -> bool:
        return True

    def public_url(self, key: str) -> str:
        return f"https://cdn.test/{key}"


class CredentialQueryPublicAdapter(PublicDownloadAdapter):
    def public_url(self, key: str) -> str:
        del key
        return "https://cdn.test/file?X-Amz-Signature=secret"


class VersionedPublicAdapter(PublicDownloadAdapter):
    @classmethod
    def supports_direct(cls, storage: Storage) -> bool:
        del storage
        return True


class ExactVersionPublicAdapter(VersionedPublicAdapter):
    def public_download_url(
        self,
        key: str,
        *,
        version: ObjectVersion,
    ) -> str:
        return f"https://cdn.test/{key}?versionId={version.version_id}"


class ConfigurableExactVersionPublicAdapter(ExactVersionPublicAdapter):
    exact_url = ""

    def public_download_url(
        self,
        key: str,
        *,
        version: ObjectVersion,
    ) -> str:
        del key, version
        return self.exact_url


def _info() -> SimpleNamespace:
    return SimpleNamespace(context=SimpleNamespace())


def _intent(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "state": UploadIntentState.CONSUMED.value,
        "finalization_error_code": "",
        "original_filename": "portrait original.png",
        "verified_size": 123,
        "verified_content_type": "image/png",
        "verified_checksum_sha256": "a" * 64,
        "verified_width": 640,
        "verified_height": 480,
        "adapter_id": "proxy",
        "adapter_version": "1",
        "storage_fingerprint": "sha256:" + "b" * 64,
        "final_object_version": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_empty_file_resolves_to_null() -> None:
    row = ResolverRecord(id=7, document="")

    value = create_stored_file_value(
        ResolverManager(row),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
    )

    assert value is None


@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": False}})
def test_disabled_uploads_do_not_issue_dead_local_download_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ProxyUploadAdapter(_STORAGE)
    registry = UploadAdapterRegistry()
    registry.register(ResolverStorage, lambda _storage: adapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)
    value = create_stored_file_value(
        ResolverManager(
            ResolverRecord(id=7, document="documents/read-only-existing.pdf")
        ),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: None,
    )

    assert value is not None
    assert value.status is StoredFileStatus.AVAILABLE
    assert value.download_url is None
    assert value.expires_at is None


def test_finalizing_file_has_processing_status_and_no_url() -> None:
    row = ResolverRecord(id=7, document="documents/current.txt")
    lookup = Mock(return_value=_intent(state=UploadIntentState.FINALIZING.value))
    value = create_stored_file_value(
        ResolverManager(row),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lookup,
    )

    assert value is not None
    assert value.status is StoredFileStatus.PROCESSING
    assert value.download_url is None
    assert lookup.call_count == 1


@pytest.mark.parametrize(
    ("state", "error_code"),
    [
        (UploadIntentState.FINALIZING.value, "UPLOAD_STORAGE_ERROR"),
        (UploadIntentState.REJECTED.value, "INVALID_IMAGE"),
        (UploadIntentState.SUPERSEDED.value, ""),
    ],
)
def test_failed_retained_intent_has_no_url(state: str, error_code: str) -> None:
    row = ResolverRecord(id=7, document="documents/current.txt")
    value = create_stored_file_value(
        ResolverManager(row),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: _intent(
            state=state,
            finalization_error_code=error_code,
        ),
    )

    assert value is not None
    assert value.status is StoredFileStatus.FAILED
    assert value.download_url is None


def test_consumed_image_exposes_verified_metadata_and_dimensions() -> None:
    row = ResolverRecord(id=7, image="images/server-key.png")
    value = create_stored_file_value(
        ResolverManager(row),
        _info(),
        field_name="image",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: _intent(),
    )

    assert value is not None
    assert value.name == "portrait original.png"
    assert value.original_name == "portrait original.png"
    assert value.size == 123
    assert value.content_type == "image/png"
    assert value.checksum == "a" * 64
    assert value.width == 640
    assert value.height == 480


def test_preexisting_file_metadata_is_lazy_and_cached_per_request() -> None:
    _STORAGE.exists_calls = 0
    _STORAGE.size_calls = 0
    info = _info()
    row = ResolverRecord(id=7, document="documents/pre-existing.pdf")
    lookup = Mock(return_value=None)
    first = create_stored_file_value(
        ResolverManager(row),
        info,
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lookup,
    )
    second = create_stored_file_value(
        ResolverManager(row),
        info,
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lookup,
    )

    assert first is second
    assert first is not None
    assert _STORAGE.exists_calls == 0
    assert _STORAGE.size_calls == 0
    assert first.status is StoredFileStatus.AVAILABLE
    assert first.size == 41
    assert second.size == 41
    assert _STORAGE.exists_calls == 1
    assert _STORAGE.size_calls == 1
    assert lookup.call_count == 1


def test_stored_value_repr_redacts_key_and_intent_metadata() -> None:
    key = "secret/files/internal-storage-key.txt"
    value = create_stored_file_value(
        ResolverManager(ResolverRecord(id=7, document=key)),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: _intent(
            final_object_version={"version_id": "secret-version"}
        ),
    )

    rendered = repr(value)
    assert key not in rendered
    assert "secret-version" not in rendered
    assert "checksum" not in rendered.lower()


def test_graphql_types_publish_structured_metadata_fields() -> None:
    assert {
        "name",
        "original_name",
        "size",
        "content_type",
        "checksum",
        "download_url",
        "expires_at",
        "status",
    } <= set(StoredFile._meta.fields)
    assert {"width", "height"} <= set(StoredImage._meta.fields)


def test_generated_file_resolver_preserves_field_read_permission() -> None:
    class DenyPermission:
        def __init__(self, instance: object, user: object) -> None:
            del instance, user

        def check_permission(self, action: str, field_name: str) -> bool:
            assert (action, field_name) == ("read", "document")
            return False

    ResolverGraphManager.Permission = DenyPermission  # type: ignore[assignment]
    old_types = dict(GraphQL.graphql_type_registry)
    old_managers = dict(GraphQL.manager_registry)
    try:
        with (
            patch.object(GraphQL, "_add_queries_to_schema"),
            patch.object(GraphQL, "_add_subscription_field"),
        ):
            GraphQL.create_graphql_interface(ResolverGraphManager)
        generated = GraphQL.graphql_type_registry[ResolverGraphManager.__name__]
        resolver = generated.resolve_document
        manager = ResolverGraphManager._from_trusted_orm_instance(
            ResolverRecord(id=7, document="documents/denied.txt")
        )
        info = SimpleNamespace(context=SimpleNamespace(user=object()))

        assert resolver(manager, info) is None
    finally:
        GraphQL.graphql_type_registry = old_types
        GraphQL.manager_registry = old_managers
        del ResolverGraphManager.Permission


@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DOWNLOAD_URL_TTL_SECONDS": 60}}
)
def test_download_expiry_is_finite_and_not_cached_across_requests() -> None:
    now = timezone.now()
    row = ResolverRecord(id=7, document="documents/current.txt")
    first = create_stored_file_value(
        ResolverManager(row),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: _intent(),
        now=lambda: now,
    )
    second = create_stored_file_value(
        ResolverManager(row),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: _intent(),
        now=lambda: now + timedelta(seconds=2),
    )

    assert first is not None and second is not None
    assert first is not second


@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DOWNLOAD_URL_TTL_SECONDS": 60}}
)
def test_direct_private_url_rechecks_adapter_and_binds_response_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectDownloadAdapter(_STORAGE)
    registry = UploadAdapterRegistry()
    registry.register(ResolverStorage, lambda _storage: adapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)
    issued_at = timezone.now()
    value = create_stored_file_value(
        ResolverManager(
            ResolverRecord(id=7, document="documents/pre-existing report.pdf")
        ),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: None,
        now=lambda: issued_at,
    )

    assert value is not None
    assert value.download_url == "https://signed.test/file"
    assert value.download_url_expires_at == issued_at + timedelta(seconds=60)
    assert adapter.private_calls == [
        {
            "key": "documents/pre-existing report.pdf",
            "expires_in": 60,
            "version": None,
            "response_content_type": "application/pdf",
            "response_content_disposition": (
                'inline; filename="pre-existing report.pdf"; '
                "filename*=utf-8''pre-existing%20report.pdf"
            ),
        }
    ]


@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DOWNLOAD_URL_TTL_SECONDS": 60}}
)
def test_retained_consumed_intent_requires_valid_exact_final_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectDownloadAdapter(_STORAGE)
    registry = UploadAdapterRegistry()
    registry.register(ResolverStorage, lambda _storage: adapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)
    intent = _intent(
        adapter_id=adapter.adapter_id,
        adapter_version=str(adapter.adapter_version),
        storage_fingerprint=adapter.storage_fingerprint(),
        final_object_version={},
    )
    value = create_stored_file_value(
        ResolverManager(ResolverRecord(id=7, document="documents/report.pdf")),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: intent,
    )

    assert value is not None
    assert value.download_url is None
    assert adapter.private_calls == []


@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DOWNLOAD_URL_TTL_SECONDS": 60}}
)
def test_deleted_exact_retained_version_is_failed_even_if_key_still_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectDownloadAdapter(_STORAGE)
    adapter.fail_exact_inspection = True
    registry = UploadAdapterRegistry()
    registry.register(ResolverStorage, lambda _storage: adapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)
    intent = _intent(
        adapter_id=adapter.adapter_id,
        adapter_version=str(adapter.adapter_version),
        storage_fingerprint=adapter.storage_fingerprint(),
        final_object_version={
            "version_id": "retained-version-1",
            "etag": "retained-etag",
            "checksum_sha256": "a" * 64,
            "size": 123,
            "content_type": "image/png",
        },
    )
    value = create_stored_file_value(
        ResolverManager(ResolverRecord(id=7, document="documents/report.pdf")),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: intent,
    )

    assert value is not None
    assert value.status is StoredFileStatus.FAILED
    assert value.download_url is None


@override_settings(
    DEBUG=False,
    GENERAL_MANAGER={
        "FILE_UPLOADS": {
            "ENABLED": True,
            "DOWNLOAD_URL_TTL_SECONDS": 60,
            "ALLOW_INSECURE_HTTP": True,
        }
    },
)
def test_private_adapter_http_url_is_rejected_outside_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectDownloadAdapter(_STORAGE, url="http://signed.test/file")
    registry = UploadAdapterRegistry()
    registry.register(ResolverStorage, lambda _storage: adapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)
    value = create_stored_file_value(
        ResolverManager(ResolverRecord(id=7, document="documents/report.pdf")),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: None,
    )

    assert value is not None
    assert value.download_url is None
    assert value.download_url_expires_at is None


@override_settings(
    GENERAL_MANAGER={
        "FILE_UPLOADS": {
            "ENABLED": True,
            "DOWNLOAD_URL_TTL_SECONDS": 604_801,
        }
    }
)
def test_oversized_private_ttl_fails_closed_without_local_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = VersionedPublicAdapter(_STORAGE)
    registry = UploadAdapterRegistry()
    registry.register(ResolverStorage, lambda _storage: adapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)
    value = create_stored_file_value(
        ResolverManager(ResolverRecord(id=7, document="documents/report.pdf")),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: None,
    )

    assert value is not None
    assert value.download_url is None
    assert value.download_url_expires_at is None
    assert adapter.private_calls == []


@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DOWNLOAD_URL_TTL_SECONDS": 60}}
)
def test_public_url_requires_both_field_policy_and_adapter_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PublicDownloadAdapter(_STORAGE)
    registry = UploadAdapterRegistry()
    registry.register(ResolverStorage, lambda _storage: adapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)

    class FileUploads:
        fields: ClassVar[dict[str, FileUploadPolicy]] = {
            "document": FileUploadPolicy(public=True)
        }

    monkeypatch.setattr(ResolverManager, "FileUploads", FileUploads, raising=False)
    value = create_stored_file_value(
        ResolverManager(ResolverRecord(id=7, document="documents/report.pdf")),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: None,
    )

    assert value is not None
    assert value.download_url == "https://cdn.test/documents/report.pdf"
    assert value.download_url_expires_at is None
    assert urlsplit(value.download_url).username is None


@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DOWNLOAD_URL_TTL_SECONDS": 60}}
)
def test_public_url_rejects_credential_bearing_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = CredentialQueryPublicAdapter(_STORAGE)
    registry = UploadAdapterRegistry()
    registry.register(ResolverStorage, lambda _storage: adapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)

    class FileUploads:
        fields: ClassVar[dict[str, FileUploadPolicy]] = {
            "document": FileUploadPolicy(public=True)
        }

    monkeypatch.setattr(ResolverManager, "FileUploads", FileUploads, raising=False)
    value = create_stored_file_value(
        ResolverManager(ResolverRecord(id=7, document="documents/report.pdf")),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: None,
    )

    assert value is not None
    assert value.download_url is None


@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DOWNLOAD_URL_TTL_SECONDS": 60}}
)
@pytest.mark.parametrize(
    "adapter_class", [VersionedPublicAdapter, PublicDownloadAdapter]
)
def test_retained_public_file_never_falls_back_to_latest_key(
    monkeypatch: pytest.MonkeyPatch,
    adapter_class: type[PublicDownloadAdapter],
) -> None:
    adapter = adapter_class(_STORAGE)
    registry = UploadAdapterRegistry()
    registry.register(ResolverStorage, lambda _storage: adapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)

    class FileUploads:
        fields: ClassVar[dict[str, FileUploadPolicy]] = {
            "document": FileUploadPolicy(public=True)
        }

    monkeypatch.setattr(ResolverManager, "FileUploads", FileUploads, raising=False)
    intent = _intent(
        adapter_id=adapter.adapter_id,
        adapter_version=str(adapter.adapter_version),
        storage_fingerprint=adapter.storage_fingerprint(),
        final_object_version={
            "version_id": "retained-version-1",
            "etag": "retained-etag",
            "checksum_sha256": "a" * 64,
            "size": 123,
            "content_type": "image/png",
        },
    )
    value = create_stored_file_value(
        ResolverManager(ResolverRecord(id=7, document="documents/report.pdf")),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: intent,
    )

    assert value is not None
    assert value.status is StoredFileStatus.AVAILABLE
    assert value.download_url is None


@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DOWNLOAD_URL_TTL_SECONDS": 60}}
)
def test_retained_public_file_uses_only_an_exact_version_public_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ExactVersionPublicAdapter(_STORAGE)
    registry = UploadAdapterRegistry()
    registry.register(ResolverStorage, lambda _storage: adapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)

    class FileUploads:
        fields: ClassVar[dict[str, FileUploadPolicy]] = {
            "document": FileUploadPolicy(public=True)
        }

    monkeypatch.setattr(ResolverManager, "FileUploads", FileUploads, raising=False)
    intent = _intent(
        adapter_id=adapter.adapter_id,
        adapter_version=str(adapter.adapter_version),
        storage_fingerprint=adapter.storage_fingerprint(),
        final_object_version={
            "version_id": "retained-version-1",
            "etag": "retained-etag",
            "checksum_sha256": "a" * 64,
            "size": 123,
            "content_type": "image/png",
        },
    )
    value = create_stored_file_value(
        ResolverManager(ResolverRecord(id=7, document="documents/report.pdf")),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: intent,
    )

    assert value is not None
    assert value.status is StoredFileStatus.AVAILABLE
    assert value.download_url == (
        "https://cdn.test/documents/report.pdf?versionId=retained-version-1"
    )
    assert value.download_url_expires_at is None


@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DOWNLOAD_URL_TTL_SECONDS": 60}}
)
@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.test/file?versionId=wrong",
        "https://cdn.test/file?versionId=retained-version-1&download=1",
        "https://cdn.test/file?X-Amz-Signature=secret",
        "https://user:password@cdn.test/file?versionId=retained-version-1",
        "https://cdn.test/file?versionId=retained-version-1#fragment",
    ],
)
def test_retained_public_file_rejects_inexact_or_credential_bearing_urls(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    adapter = ConfigurableExactVersionPublicAdapter(_STORAGE)
    adapter.exact_url = url
    registry = UploadAdapterRegistry()
    registry.register(ResolverStorage, lambda _storage: adapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)

    class FileUploads:
        fields: ClassVar[dict[str, FileUploadPolicy]] = {
            "document": FileUploadPolicy(public=True)
        }

    monkeypatch.setattr(ResolverManager, "FileUploads", FileUploads, raising=False)
    intent = _intent(
        adapter_id=adapter.adapter_id,
        adapter_version=str(adapter.adapter_version),
        storage_fingerprint=adapter.storage_fingerprint(),
        final_object_version={
            "version_id": "retained-version-1",
            "etag": "retained-etag",
            "checksum_sha256": "a" * 64,
            "size": 123,
            "content_type": "image/png",
        },
    )
    value = create_stored_file_value(
        ResolverManager(ResolverRecord(id=7, document="documents/report.pdf")),
        _info(),
        field_name="document",
        manager_name="ResolverManager",
        intent_lookup=lambda **_kwargs: intent,
    )

    assert value is not None
    assert value.download_url is None
