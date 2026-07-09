from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
import tomllib
from uuid import UUID

import pytest

from general_manager.uploads.adapters import (
    ProxyUploadAdapter,
    PublicUploadUrlUnsupportedError,
    UploadAdapterRegistry,
)
from general_manager.uploads.errors import (
    UploadBackendUnsupportedError,
    UploadStorageError,
    UploadTransferConflictError,
)
from general_manager.uploads.s3 import S3UploadAdapter
from general_manager.uploads.types import ObjectVersion, UploadTransport


class MissingObjectError(Exception):
    response: ClassVar[dict[str, dict[str, str]]] = {"Error": {"Code": "NoSuchKey"}}


class FakeSDKError(Exception):
    response: ClassVar[dict[str, dict[str, str]]] = {"Error": {"Code": "InternalError"}}


class FakeS3Client:
    def __init__(
        self,
        *,
        versioning: bool = True,
        conditional_copy: bool = True,
    ) -> None:
        self.versioning = versioning
        self.objects: dict[tuple[str, str | None], dict[str, Any]] = {}
        self.copy_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.head_calls: list[dict[str, Any]] = []
        self.presigned_post_calls: list[dict[str, Any]] = []
        self.fail_operations: set[str] = set()
        self.operation_errors: dict[str, Exception] = {}
        members = {"IfNoneMatch": object()} if conditional_copy else {}
        operation = SimpleNamespace(input_shape=SimpleNamespace(members=members))
        service_model = SimpleNamespace(operation_model=lambda _name: operation)
        self.meta = SimpleNamespace(service_model=service_model)

    def _fail_if(self, operation: str) -> None:
        if operation in self.operation_errors:
            raise self.operation_errors[operation]
        if operation in self.fail_operations:
            raise FakeSDKError

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, str]:
        assert kwargs["Bucket"] == "uploads"
        return {"Status": "Enabled" if self.versioning else "Suspended"}

    def generate_presigned_post(self, **kwargs: Any) -> dict[str, Any]:
        self._fail_if("presign_post")
        self.presigned_post_calls.append(dict(kwargs))
        return {
            "url": "https://signed.example.test/upload?credential=secret",
            "fields": dict(kwargs["Fields"]),
        }

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self._fail_if("presign_get")
        assert operation == "get_object"
        assert kwargs["Params"]["Bucket"] == "uploads"
        return "https://signed.example.test/get?signature=secret"

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self._fail_if("head")
        self.head_calls.append(dict(kwargs))
        assert kwargs.get("ChecksumMode") == "ENABLED"
        return self._lookup_object(**kwargs)

    def _lookup_object(self, **kwargs: Any) -> dict[str, Any]:
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
        self._fail_if("get")
        value = self._lookup_object(**kwargs)
        return {"Body": BytesIO(value["Body"])}

    def copy_object(self, **kwargs: Any) -> dict[str, Any]:
        self._fail_if("copy")
        self.copy_calls.append(dict(kwargs))
        source = kwargs["CopySource"]
        assert source["VersionId"]
        assert kwargs["IfNoneMatch"] == "*"
        value = self._lookup_object(Key=source["Key"], VersionId=source["VersionId"])
        assert kwargs["CopySourceIfMatch"] == value["ETag"]
        source_checksum = base64.b64decode(value["ChecksumSHA256"]).hex()
        assert kwargs["Metadata"]["gm-checksum-sha256"] == source_checksum
        assert not any(key == kwargs["Key"] for key, _version in self.objects)
        destination = (kwargs["Key"], "final-version")
        self.objects[destination] = {
            **value,
            "VersionId": "final-version",
            "Metadata": dict(kwargs["Metadata"]),
        }
        return {"VersionId": "final-version", "CopyObjectResult": {}}

    def delete_object(self, **kwargs: Any) -> None:
        self._fail_if("delete")
        self.delete_calls.append(dict(kwargs))
        self.objects.pop((kwargs["Key"], kwargs.get("VersionId")), None)


class FakeS3Storage:
    _gm_s3_storage = True
    bucket_name = "uploads"
    default_acl = None
    upload_staging_prefix_private: object

    def __init__(
        self,
        client: FakeS3Client,
        *,
        public: bool = False,
        endpoint_url: str = "https://s3.us-east-1.amazonaws.com",
        conditional_copy: object | None = None,
        default_acl: str | None = None,
        object_parameters: dict[str, object] | None = None,
    ) -> None:
        self.s3_client = client
        self.public = public
        self.endpoint_url = endpoint_url
        self.default_acl = default_acl
        self.object_parameters = object_parameters or {}
        if conditional_copy is not None:
            self.supports_conditional_copy = conditional_copy

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


def test_s3_direct_construction_rejects_suspended_versioning() -> None:
    storage = FakeS3Storage(FakeS3Client(versioning=False))

    with pytest.raises(UploadBackendUnsupportedError):
        S3UploadAdapter(storage)


def test_s3_direct_mode_requires_conditional_destination_copy() -> None:
    client = FakeS3Client(conditional_copy=False)
    storage = FakeS3Storage(client)

    assert S3UploadAdapter.supports_direct(storage) is False
    assert isinstance(UploadAdapterRegistry().resolve(storage), ProxyUploadAdapter)


def test_s3_direct_construction_rejects_missing_if_none_match_model() -> None:
    storage = FakeS3Storage(FakeS3Client(conditional_copy=False))

    with pytest.raises(UploadBackendUnsupportedError):
        S3UploadAdapter(storage)


@pytest.mark.parametrize("capability", [None, False, 1, "yes"])
def test_s3_custom_endpoint_requires_explicit_true_conditional_copy_capability(
    capability: object | None,
) -> None:
    client = FakeS3Client()
    storage = FakeS3Storage(
        client,
        endpoint_url="https://objects.example.test",
        conditional_copy=capability,
    )

    assert S3UploadAdapter.supports_direct(storage) is False
    assert isinstance(UploadAdapterRegistry().resolve(storage), ProxyUploadAdapter)


def test_s3_custom_endpoint_allows_explicit_conditional_copy_capability() -> None:
    storage = FakeS3Storage(
        FakeS3Client(),
        endpoint_url="https://objects.example.test",
        conditional_copy=True,
    )

    assert S3UploadAdapter.supports_direct(storage) is True
    assert isinstance(UploadAdapterRegistry().resolve(storage), S3UploadAdapter)


def test_s3_direct_construction_rejects_unapproved_custom_endpoint() -> None:
    storage = FakeS3Storage(
        FakeS3Client(),
        endpoint_url="https://objects.example.test",
    )

    with pytest.raises(UploadBackendUnsupportedError):
        S3UploadAdapter(storage)


def test_s3_registered_factory_cannot_bypass_constructor_capabilities() -> None:
    storage = FakeS3Storage(FakeS3Client(versioning=False))
    registry = UploadAdapterRegistry()
    registry.register(FakeS3Storage, lambda value: S3UploadAdapter(value))

    with pytest.raises(UploadBackendUnsupportedError):
        registry.resolve(storage)


def test_s3_extra_requires_conditional_copy_capable_boto3() -> None:
    configuration = tomllib.loads(
        (Path(__file__).parents[2] / "pyproject.toml").read_text()
    )

    assert configuration["project"]["optional-dependencies"]["file-upload-s3"] == [
        "boto3>=1.42.0",
        "django-storages[s3]>=1.14",
    ]


def test_registry_prefers_explicit_adapter_over_builtin_s3() -> None:
    client = FakeS3Client()
    storage = FakeS3Storage(client)
    registry = UploadAdapterRegistry()
    registry.register(FakeS3Storage, lambda value: ProxyUploadAdapter(value))

    resolved = registry.resolve(storage)

    assert isinstance(resolved, ProxyUploadAdapter)
    assert resolved.storage is storage


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


def test_s3_bucket_owner_enforced_staging_omits_acl() -> None:
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

    assert "acl" not in instructions.fields
    assert not any(
        isinstance(condition, dict) and "acl" in condition
        for condition in client.presigned_post_calls[0]["Conditions"]
    )


def test_s3_staging_propagates_safe_bucket_owner_acl() -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(
        FakeS3Storage(
            client,
            object_parameters={"ACL": "bucket-owner-full-control"},
        )
    )
    checksum = hashlib.sha256(b"payload").hexdigest()
    version = _stage(client)

    instructions = adapter.create_upload_instructions(
        stage_key="gm-staging/intent.bin",
        upload_url=None,
        content_type="text/plain",
        size=7,
        checksum_sha256=checksum,
    )

    assert instructions.fields["acl"] == "bucket-owner-full-control"
    assert {"acl": "bucket-owner-full-control"} in (
        client.presigned_post_calls[0]["Conditions"]
    )
    adapter.materialize(
        "gm-staging/intent.bin",
        version,
        "files/report.txt",
        intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
    )
    assert client.copy_calls[0]["ACL"] == "bucket-owner-full-control"


@pytest.mark.parametrize(
    ("default_acl", "object_parameters"),
    [
        ("public-read", None),
        (None, {"ACL": "public-read"}),
    ],
)
def test_s3_public_final_acl_requires_private_staging_prefix(
    default_acl: str | None,
    object_parameters: dict[str, object] | None,
) -> None:
    storage = FakeS3Storage(
        FakeS3Client(),
        default_acl=default_acl,
        object_parameters=object_parameters,
    )

    assert S3UploadAdapter.supports_direct(storage) is False
    assert isinstance(UploadAdapterRegistry().resolve(storage), ProxyUploadAdapter)
    with pytest.raises(UploadBackendUnsupportedError):
        S3UploadAdapter(storage)


def test_s3_public_bucket_policy_requires_private_staging_prefix() -> None:
    storage = FakeS3Storage(FakeS3Client(), public=True)

    assert S3UploadAdapter.supports_direct(storage) is False
    assert isinstance(UploadAdapterRegistry().resolve(storage), ProxyUploadAdapter)
    with pytest.raises(UploadBackendUnsupportedError):
        S3UploadAdapter(storage)


@pytest.mark.parametrize("capability", [False, 1, "yes"])
def test_s3_public_bucket_policy_requires_exact_private_prefix_capability(
    capability: object,
) -> None:
    storage = FakeS3Storage(FakeS3Client(), public=True)
    storage.upload_staging_prefix_private = capability

    assert S3UploadAdapter.supports_direct(storage) is False
    with pytest.raises(UploadBackendUnsupportedError):
        S3UploadAdapter(storage)


def test_s3_public_bucket_policy_allows_explicit_private_staging_prefix() -> None:
    storage = FakeS3Storage(FakeS3Client(), public=True)
    storage.upload_staging_prefix_private = True

    assert S3UploadAdapter.supports_direct(storage) is True
    adapter = S3UploadAdapter(storage)
    checksum = hashlib.sha256(b"payload").hexdigest()

    instructions = adapter.create_upload_instructions(
        stage_key="gm-staging/intent.bin",
        upload_url=None,
        content_type="text/plain",
        size=7,
        checksum_sha256=checksum,
    )

    assert "acl" not in instructions.fields


def test_s3_stages_private_with_kms_and_applies_public_acl_on_final_copy() -> None:
    client = FakeS3Client()
    storage = FakeS3Storage(
        client,
        default_acl="public-read",
        object_parameters={
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": "alias/uploads",
            "BucketKeyEnabled": True,
            "StorageClass": "STANDARD_IA",
        },
    )
    storage.upload_staging_prefix_private = True
    adapter = S3UploadAdapter(storage)
    version = _stage(client)

    instructions = adapter.create_upload_instructions(
        stage_key="gm-staging/intent.bin",
        upload_url=None,
        content_type="text/plain",
        size=version.size,
        checksum_sha256=version.checksum_sha256,
    )
    adapter.materialize(
        "gm-staging/intent.bin",
        version,
        "files/report.txt",
        intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
    )

    expected_fields = {
        "x-amz-server-side-encryption": "aws:kms",
        "x-amz-server-side-encryption-aws-kms-key-id": "alias/uploads",
        "x-amz-server-side-encryption-bucket-key-enabled": "true",
    }
    assert expected_fields.items() <= instructions.fields.items()
    assert "acl" not in instructions.fields
    assert "x-amz-storage-class" not in instructions.fields
    conditions = client.presigned_post_calls[0]["Conditions"]
    assert all({key: value} in conditions for key, value in expected_fields.items())
    copy = client.copy_calls[0]
    assert copy["ACL"] == "public-read"
    assert copy["ServerSideEncryption"] == "aws:kms"
    assert copy["SSEKMSKeyId"] == "alias/uploads"
    assert copy["BucketKeyEnabled"] is True
    assert copy["StorageClass"] == "STANDARD_IA"


@pytest.mark.parametrize("storage_class", ["GLACIER", "DEEP_ARCHIVE"])
def test_s3_archive_storage_class_is_final_only(storage_class: str) -> None:
    client = FakeS3Client()
    storage = FakeS3Storage(
        client,
        object_parameters={"StorageClass": storage_class},
    )
    adapter = S3UploadAdapter(storage)
    version = _stage(client)

    instructions = adapter.create_upload_instructions(
        stage_key="gm-staging/intent.bin",
        upload_url=None,
        content_type="text/plain",
        size=version.size,
        checksum_sha256=version.checksum_sha256,
    )
    adapter.materialize(
        "gm-staging/intent.bin",
        version,
        "files/report.txt",
        intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
    )

    assert "acl" not in instructions.fields
    assert "x-amz-storage-class" not in instructions.fields
    assert client.copy_calls[0]["StorageClass"] == storage_class


@pytest.mark.parametrize(
    "object_parameters",
    [
        {"SSEKMSKeyId": "alias/uploads"},
        {"BucketKeyEnabled": True},
        {
            "ServerSideEncryption": "AES256",
            "SSEKMSKeyId": "alias/uploads",
        },
    ],
)
def test_s3_direct_construction_rejects_invalid_kms_staging_configuration(
    object_parameters: dict[str, object],
) -> None:
    storage = FakeS3Storage(
        FakeS3Client(),
        object_parameters=object_parameters,
    )

    assert S3UploadAdapter.supports_direct(storage) is False
    with pytest.raises(UploadBackendUnsupportedError):
        S3UploadAdapter(storage)


def test_s3_rejects_unsupported_object_parameters() -> None:
    storage = FakeS3Storage(
        FakeS3Client(),
        object_parameters={"CacheControl": "private"},
    )

    assert S3UploadAdapter.supports_direct(storage) is False
    with pytest.raises(UploadBackendUnsupportedError):
        S3UploadAdapter(storage)


def test_s3_inspects_and_opens_exact_immutable_version() -> None:
    client = FakeS3Client()
    expected = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))

    inspected = adapter.inspect_staged("gm-staging/intent.bin")
    opened = adapter.open_stage("gm-staging/intent.bin", expected)

    assert inspected == expected
    assert opened.read() == b"immutable staged payload"


def test_s3_inspection_requests_checksum_metadata() -> None:
    client = FakeS3Client()
    _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))

    adapter.inspect_staged("gm-staging/intent.bin")

    assert client.head_calls == [
        {
            "Bucket": "uploads",
            "Key": "gm-staging/intent.bin",
            "ChecksumMode": "ENABLED",
        }
    ]


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


def test_s3_post_copy_inspection_requests_checksum_metadata() -> None:
    client = FakeS3Client()
    version = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))

    adapter.materialize(
        "gm-staging/intent.bin",
        version,
        "files/report.txt",
        intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
    )

    assert client.head_calls[-1] == {
        "Bucket": "uploads",
        "Key": "files/report.txt",
        "ChecksumMode": "ENABLED",
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

    public_storage = FakeS3Storage(client, public=True)
    public_storage.upload_staging_prefix_private = True
    public = S3UploadAdapter(public_storage)
    assert public.supports_public_urls is True
    assert public.public_url("files/report.txt") == (
        "https://cdn.example.test/files/report.txt"
    )


def test_s3_delete_without_version_id_fails_closed() -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(FakeS3Storage(client))
    checksum_only = ObjectVersion(
        version_id=None,
        etag=None,
        checksum_sha256="a" * 64,
        size=1,
    )

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.delete_stage("gm-staging/intent.bin", checksum_only)

    assert client.delete_calls == []


@pytest.mark.parametrize(
    "operation",
    ["presign_post", "head", "get", "delete", "presign_get"],
)
def test_s3_normalizes_sdk_failures(operation: str) -> None:
    client = FakeS3Client()
    client.fail_operations.add(operation)
    adapter = S3UploadAdapter(FakeS3Storage(client))
    version = ObjectVersion(
        version_id="stage-version-1",
        etag='"etag-1"',
        checksum_sha256="a" * 64,
        size=1,
        content_type="text/plain",
    )

    with pytest.raises(UploadStorageError) as captured:
        if operation == "presign_post":
            adapter.create_upload_instructions(
                stage_key="gm-staging/intent.bin",
                upload_url=None,
                content_type="text/plain",
                size=1,
                checksum_sha256="a" * 64,
            )
        elif operation == "head":
            adapter.inspect_staged("gm-staging/intent.bin")
        elif operation == "get":
            adapter.open_stage("gm-staging/intent.bin", version)
        elif operation == "delete":
            adapter.delete_stage("gm-staging/intent.bin", version)
        else:
            adapter.private_download_url("files/report.txt", expires_in=60)

    assert isinstance(captured.value.__cause__, FakeSDKError)


def test_s3_preserves_explicit_upload_errors_from_copy_boundary() -> None:
    client = FakeS3Client()
    version = _stage(client)
    explicit = UploadBackendUnsupportedError()
    client.operation_errors["copy"] = explicit
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with pytest.raises(UploadBackendUnsupportedError) as captured:
        adapter.materialize(
            "gm-staging/intent.bin",
            version,
            "files/report.txt",
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )

    assert captured.value is explicit


def test_s3_fingerprint_and_repr_exclude_endpoint_credentials() -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(
        FakeS3Storage(
            client,
            endpoint_url=(
                "https://user:password@objects.example.test/root?secret=value"
            ),
            conditional_copy=True,
        )
    )

    loggable = f"{adapter!r} {adapter.storage_fingerprint()}"

    assert adapter.storage_fingerprint().startswith("sha256:")
    assert "user" not in loggable
    assert "password" not in loggable
    assert "secret" not in loggable
