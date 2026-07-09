from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from typing import Any, ClassVar
from uuid import UUID

import pytest

from general_manager.uploads.adapters import (
    ProxyUploadAdapter,
    PublicUploadUrlUnsupportedError,
    UploadAdapterRegistry,
)
from general_manager.uploads.errors import UploadTransferConflictError
from general_manager.uploads.s3 import S3UploadAdapter
from general_manager.uploads.types import ObjectVersion, UploadTransport


class MissingObjectError(Exception):
    response: ClassVar[dict[str, dict[str, str]]] = {"Error": {"Code": "NoSuchKey"}}


class FakeS3Client:
    supports_conditional_copy = True

    def __init__(self, *, versioning: bool = True) -> None:
        self.versioning = versioning
        self.objects: dict[tuple[str, str | None], dict[str, Any]] = {}
        self.copy_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, str]:
        assert kwargs["Bucket"] == "uploads"
        return {"Status": "Enabled" if self.versioning else "Suspended"}

    def generate_presigned_post(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "url": "https://signed.example.test/upload?credential=secret",
            "fields": dict(kwargs["Fields"]),
        }

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        assert operation == "get_object"
        assert kwargs["Params"]["Bucket"] == "uploads"
        return "https://signed.example.test/get?signature=secret"

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        key = (kwargs["Key"], kwargs.get("VersionId"))
        if key not in self.objects and kwargs.get("VersionId") is None:
            versions = [item for item in self.objects if item[0] == kwargs["Key"]]
            if versions:
                key = versions[-1]
        try:
            return dict(self.objects[key])
        except KeyError as exc:
            raise MissingObjectError from exc

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        value = self.head_object(**kwargs)
        return {"Body": BytesIO(value["Body"])}

    def copy_object(self, **kwargs: Any) -> dict[str, Any]:
        self.copy_calls.append(dict(kwargs))
        source = kwargs["CopySource"]
        value = self.head_object(Key=source["Key"], VersionId=source["VersionId"])
        destination = (kwargs["Key"], "final-version")
        self.objects[destination] = {
            **value,
            "VersionId": "final-version",
            "Metadata": dict(kwargs["Metadata"]),
        }
        return {"VersionId": "final-version", "CopyObjectResult": {}}

    def delete_object(self, **kwargs: Any) -> None:
        self.delete_calls.append(dict(kwargs))
        self.objects.pop((kwargs["Key"], kwargs.get("VersionId")), None)


class FakeS3Storage:
    _gm_s3_storage = True
    bucket_name = "uploads"
    endpoint_url = "https://user:password@objects.example.test/root?secret=value"
    default_acl = None

    def __init__(self, client: FakeS3Client, *, public: bool = False) -> None:
        self.s3_client = client
        self.public = public

    def url(self, key: str) -> str:
        return f"https://cdn.example.test/{key}"


class OfficialLookingS3Storage:
    bucket_name = "uploads"


OfficialLookingS3Storage.__module__ = "storages.backends.s3"


def _stage(client: FakeS3Client) -> ObjectVersion:
    payload = b"immutable staged payload"
    checksum = hashlib.sha256(payload).digest()
    client.objects[("gm-staging/intent.bin", "stage-version-1")] = {
        "VersionId": "stage-version-1",
        "ETag": '"etag-1"',
        "ChecksumSHA256": base64.b64encode(checksum).decode("ascii"),
        "ContentLength": len(payload),
        "ContentType": "text/plain",
        "Metadata": {},
        "Body": payload,
    }
    return ObjectVersion(
        version_id="stage-version-1",
        etag='"etag-1"',
        checksum_sha256=checksum.hex(),
        size=len(payload),
        content_type="text/plain",
    )


def test_s3_direct_mode_requires_versioning() -> None:
    client = FakeS3Client(versioning=False)
    storage = FakeS3Storage(client)

    assert S3UploadAdapter.supports_direct(storage) is False
    assert isinstance(UploadAdapterRegistry().resolve(storage), ProxyUploadAdapter)


def test_s3_direct_mode_requires_conditional_destination_copy() -> None:
    client = FakeS3Client()
    client.supports_conditional_copy = False
    storage = FakeS3Storage(client)

    assert S3UploadAdapter.supports_direct(storage) is False
    assert isinstance(UploadAdapterRegistry().resolve(storage), ProxyUploadAdapter)


def test_registry_prefers_explicit_adapter_over_builtin_s3() -> None:
    client = FakeS3Client()
    storage = FakeS3Storage(client)
    configured = object()
    registry = UploadAdapterRegistry()
    registry.register(FakeS3Storage, configured)

    assert registry.resolve(storage) is configured


def test_s3_missing_optional_dependencies_fall_back_without_import_error() -> None:
    storage = OfficialLookingS3Storage()

    assert S3UploadAdapter.supports_direct(storage) is False
    assert isinstance(UploadAdapterRegistry().resolve(storage), ProxyUploadAdapter)


def test_s3_presigned_upload_binds_stage_metadata() -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(FakeS3Storage(client))
    checksum = hashlib.sha256(b"payload").hexdigest()

    instructions = adapter.create_upload_instructions(
        stage_key="gm-staging/intent.bin",
        upload_url=None,
        content_type="text/plain",
        size=7,
        checksum_sha256=checksum,
    )

    assert instructions.transport is UploadTransport.DIRECT
    assert instructions.method == "POST"
    assert instructions.fields["key"] == "gm-staging/intent.bin"
    assert instructions.fields["Content-Type"] == "text/plain"
    assert "gm-staging/intent.bin" not in repr(instructions)
    assert "credential=secret" not in repr(instructions)


def test_s3_inspects_and_opens_exact_immutable_version() -> None:
    client = FakeS3Client()
    expected = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))

    inspected = adapter.inspect_staged("gm-staging/intent.bin")
    opened = adapter.open_stage("gm-staging/intent.bin", expected)

    assert inspected == expected
    assert opened.read() == b"immutable staged payload"


def test_s3_materializes_exact_version_conditionally_and_retries() -> None:
    client = FakeS3Client()
    version = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))
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
        "files/report.txt",
        intent_id=intent_id,
    )

    assert actual_key == "files/report.txt"
    assert retried_key == "files/report.txt"
    assert len(client.copy_calls) == 1
    call = client.copy_calls[0]
    assert call["CopySource"] == {
        "Bucket": "uploads",
        "Key": "gm-staging/intent.bin",
        "VersionId": "stage-version-1",
    }
    assert call["CopySourceIfMatch"] == '"etag-1"'
    assert call["IfNoneMatch"] == "*"
    assert call["Metadata"] == {
        "gm-intent-id": str(intent_id),
        "gm-checksum-sha256": version.checksum_sha256,
    }


def test_s3_refuses_to_accept_unrelated_existing_destination() -> None:
    client = FakeS3Client()
    version = _stage(client)
    client.objects[("files/report.txt", "old-version")] = {
        "VersionId": "old-version",
        "ETag": '"old"',
        "ChecksumSHA256": base64.b64encode(b"x" * 32).decode("ascii"),
        "ContentLength": 4,
        "ContentType": "text/plain",
        "Metadata": {"gm-intent-id": "another-intent"},
        "Body": b"old!",
    }
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with pytest.raises(UploadTransferConflictError):
        adapter.materialize(
            "gm-staging/intent.bin",
            version,
            "files/report.txt",
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )

    assert client.copy_calls == []


def test_s3_deletes_exact_version_and_handles_private_and_public_urls() -> None:
    client = FakeS3Client()
    version = _stage(client)
    private = S3UploadAdapter(FakeS3Storage(client))

    assert private.private_download_url("files/report.txt", expires_in=45) == (
        "https://signed.example.test/get?signature=secret"
    )
    assert private.supports_public_urls is False
    with pytest.raises(PublicUploadUrlUnsupportedError):
        private.public_url("files/report.txt")
    private.delete_stage("gm-staging/intent.bin", version)
    assert client.delete_calls == [
        {
            "Bucket": "uploads",
            "Key": "gm-staging/intent.bin",
            "VersionId": "stage-version-1",
        }
    ]

    public = S3UploadAdapter(FakeS3Storage(client, public=True))
    assert public.supports_public_urls is True
    assert public.public_url("files/report.txt") == (
        "https://cdn.example.test/files/report.txt"
    )


def test_s3_fingerprint_and_repr_exclude_endpoint_credentials() -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(FakeS3Storage(client))

    loggable = f"{adapter!r} {adapter.storage_fingerprint()}"

    assert adapter.storage_fingerprint().startswith("sha256:")
    assert "user" not in loggable
    assert "password" not in loggable
    assert "secret" not in loggable
