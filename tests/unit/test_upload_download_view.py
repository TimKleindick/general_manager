"""Security tests for private file download capabilities."""

from __future__ import annotations

from datetime import timedelta
from dataclasses import asdict
import hashlib
from pathlib import Path
import tempfile
from typing import ClassVar

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import connection, models
from django.db.models import NOT_PROVIDED
from django.test import Client, override_settings
from django.utils import timezone

from general_manager.api.graphql import GraphQL
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.uploads.graphql_types import (
    decode_local_download_capability,
    issue_local_download_capability,
)
from general_manager.uploads.adapters import ProxyUploadAdapter
from general_manager.uploads.models import UploadIntent
from general_manager.uploads.types import UploadIntentState, UploadOperation
from general_manager.uploads.urls import add_file_upload_urls, clear_file_upload_urls


class DownloadStorage(FileSystemStorage):
    pass


_ROOT = tempfile.mkdtemp(prefix="gm-download-tests-")
_STORAGE = DownloadStorage(location=_ROOT)


class DownloadRecord(models.Model):
    file = models.FileField(storage=_STORAGE, upload_to="private/", blank=True)
    label = models.CharField(max_length=32, blank=True)

    class Meta:
        app_label = "general_manager"
        db_table = "gm_test_upload_download_record"


class DownloadInterface(OrmInterfaceBase[DownloadRecord]):
    _model = DownloadRecord
    database: ClassVar[str | None] = None
    input_fields: ClassVar[dict[str, Input[type[object]]]] = {
        "id": Input(int)  # type: ignore[dict-item]
    }

    @classmethod
    def get_attribute_types(cls) -> dict[str, dict[str, object]]:
        return {
            "file": {
                "type": str,
                "orm_field_kind": "file",
                "is_required": False,
                "is_editable": True,
                "is_derived": False,
                "default": NOT_PROVIDED,
            }
        }


class DownloadManager(GeneralManager):
    _attributes: ClassVar[dict[str, object]] = {}


DownloadManager.Interface = DownloadInterface  # type: ignore[assignment]
DownloadInterface._parent_class = DownloadManager


_SETTINGS = {
    "FILE_UPLOADS": {
        "ENABLED": True,
        "HTTP_UPLOAD_PATH": "gm/uploads/",
        "DOWNLOAD_URL_TTL_SECONDS": 60,
    }
}


@pytest.fixture(scope="module", autouse=True)
def download_table(django_db_setup: object, django_db_blocker: object):
    del django_db_setup
    with django_db_blocker.unblock():  # type: ignore[attr-defined]
        with connection.schema_editor() as editor:
            editor.create_model(DownloadRecord)
    yield
    with django_db_blocker.unblock():  # type: ignore[attr-defined]
        with connection.schema_editor() as editor:
            editor.delete_model(DownloadRecord)


@pytest.fixture(autouse=True)
def download_runtime():
    GraphQL.manager_registry[DownloadManager.__name__] = DownloadManager
    clear_file_upload_urls()
    yield
    clear_file_upload_urls()
    GraphQL.manager_registry.pop(DownloadManager.__name__, None)


@pytest.fixture
def stored_file(db: object) -> DownloadRecord:
    del db
    row = DownloadRecord.objects.create(file="private/display image.svg", label="x")
    _STORAGE.save(row.file.name, ContentFile(b"<svg></svg>"))
    yield row
    DownloadRecord.objects.filter(pk=row.pk).delete()
    _STORAGE.delete("private/display image.svg")


def _url(row: DownloadRecord) -> str:
    return issue_local_download_capability(
        manager_name=DownloadManager.__name__,
        object_id=str(row.pk),
        field_name="file",
        current_key=row.file.name,
        expires_in=60,
    )[0]


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_SETTINGS)
@pytest.mark.django_db
def test_private_local_download_streams_with_security_headers(
    client: Client,
    stored_file: DownloadRecord,
) -> None:
    add_file_upload_urls()
    url = _url(stored_file)

    response = client.get(url)

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"<svg></svg>"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "no-referrer"
    assert response["Cache-Control"] == "private, no-store"
    assert response["Content-Security-Policy"] == "sandbox"
    assert response["Content-Disposition"].startswith("inline;")
    assert "filename*=utf-8''display%20image.svg" in response["Content-Disposition"]


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_SETTINGS)
@pytest.mark.django_db
def test_private_download_capability_contains_no_storage_key(
    stored_file: DownloadRecord,
) -> None:
    add_file_upload_urls()
    url = _url(stored_file)

    assert stored_file.file.name not in url
    assert str(Path(_ROOT)) not in url


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_SETTINGS)
@pytest.mark.django_db
def test_private_download_head_is_intentional_and_bodyless(
    client: Client,
    stored_file: DownloadRecord,
) -> None:
    add_file_upload_urls()

    response = client.head(_url(stored_file))

    assert response.status_code == 200
    assert response.content == b""
    assert response["Content-Disposition"].startswith("inline;")
    assert response["X-Content-Type-Options"] == "nosniff"


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_SETTINGS)
@pytest.mark.django_db
def test_private_download_rejects_unsupported_methods(
    client: Client,
    stored_file: DownloadRecord,
) -> None:
    add_file_upload_urls()

    response = client.post(_url(stored_file))

    assert response.status_code == 405
    assert response["Allow"] == "GET, HEAD"
    assert response.json() == {
        "error": {
            "code": "METHOD_NOT_ALLOWED",
            "message": "Only GET and HEAD are supported for file downloads.",
        }
    }


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_SETTINGS)
@pytest.mark.django_db
@pytest.mark.parametrize("mutation", ["tamper", "replace", "delete-row", "delete-file"])
def test_invalid_replaced_or_missing_download_fails_redacted(
    client: Client,
    stored_file: DownloadRecord,
    mutation: str,
) -> None:
    add_file_upload_urls()
    url = _url(stored_file)
    if mutation == "tamper":
        url = f"{url}x"
    elif mutation == "replace":
        DownloadRecord.objects.filter(pk=stored_file.pk).update(
            file="private/replacement.txt"
        )
    elif mutation == "delete-row":
        DownloadRecord.objects.filter(pk=stored_file.pk).delete()
    else:
        _STORAGE.delete(stored_file.file.name)

    response = client.get(url)

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "FILE_NOT_FOUND",
            "message": "The requested file is not available.",
        }
    }
    rendered = response.content.decode()
    assert stored_file.file.name not in rendered
    assert _ROOT not in rendered


def test_download_value_repr_does_not_reveal_capability_inputs() -> None:
    result = issue_local_download_capability(
        manager_name="Manager",
        object_id="42",
        field_name="avatar",
        current_key="secret/current-key.png",
        expires_in=60,
    )

    assert "secret/current-key.png" not in repr(result)


def test_capability_is_expired_at_its_exact_expiry_boundary() -> None:
    issued_at = timezone.now()
    result = issue_local_download_capability(
        manager_name="Manager",
        object_id="42",
        field_name="avatar",
        current_key="secret/current-key.png",
        expires_in=60,
        now=lambda: issued_at,
    )
    capability = result.url.rsplit("/", 1)[-1]

    assert (
        decode_local_download_capability(
            capability,
            max_age=60,
            now=lambda: issued_at + timedelta(seconds=60),
        )
        is None
    )


def test_capability_uses_keyed_binding_not_plain_key_digest() -> None:
    key = "guessable/avatar.png"
    result = issue_local_download_capability(
        manager_name="Manager",
        object_id="42",
        field_name="avatar",
        current_key=key,
        expires_in=60,
    )
    payload = decode_local_download_capability(
        result.url.rsplit("/", 1)[-1],
        max_age=60,
    )

    assert payload is not None
    assert payload["k"] != hashlib.sha256(key.encode()).hexdigest()


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_SETTINGS)
@pytest.mark.django_db
def test_retained_capability_rejects_same_key_byte_replacement(
    client: Client,
    stored_file: DownloadRecord,
) -> None:
    add_file_upload_urls()
    adapter = ProxyUploadAdapter(_STORAGE)
    version = adapter.inspect_staged(stored_file.file.name)
    intent = UploadIntent.objects.create(
        user=None,
        token_digest="1" * 64,
        manager_name=DownloadManager.__name__,
        field_name="file",
        operation=UploadOperation.CREATE.value,
        target_id=None,
        final_target_pk=str(stored_file.pk),
        adapter_id=adapter.adapter_id,
        adapter_version=str(adapter.adapter_version),
        storage_fingerprint=adapter.storage_fingerprint(),
        staging_key="gm-staging/tests/retained",
        final_key=stored_file.file.name,
        original_filename="display image.svg",
        declared_size=version.size,
        declared_content_type=version.content_type or "image/svg+xml",
        declared_checksum_sha256=version.checksum_sha256,
        verified_size=version.size,
        verified_content_type=version.content_type or "image/svg+xml",
        verified_checksum_sha256=version.checksum_sha256,
        object_version=asdict(version),
        final_object_version=asdict(version),
        state=UploadIntentState.CONSUMED.value,
        expires_at=timezone.now() + timedelta(minutes=5),
        consumed_at=timezone.now(),
    )
    capability = issue_local_download_capability(
        manager_name=DownloadManager.__name__,
        object_id=str(stored_file.pk),
        field_name="file",
        current_key=stored_file.file.name,
        expires_in=60,
        intent_id=intent.id,
    )
    original = client.get(capability.url)
    assert original.status_code == 200
    assert b"".join(original.streaming_content) == b"<svg></svg>"
    with open(_STORAGE.path(stored_file.file.name), "wb") as replaced:
        replaced.write(b"replacement")

    response = client.get(capability.url)
    head_response = client.head(capability.url)

    assert response.status_code == 404
    assert head_response.status_code == 404
    assert b"replacement" not in response.content
