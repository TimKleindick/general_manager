from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, ClassVar
import tomllib
from uuid import UUID

import pytest
from django.test import override_settings

from general_manager.uploads.adapters import (
    ClaimedObject,
    ProxyUploadAdapter,
    PublicUploadUrlUnsupportedError,
    UploadAdapterRegistry,
)
from general_manager.uploads import finalization
from general_manager.uploads.errors import (
    UploadBackendUnsupportedError,
    UploadChecksumMismatchError,
    UploadStorageError,
    UploadObjectMissingError,
    UploadStorageChangedError,
    UploadTransferConflictError,
)
from general_manager.uploads.s3 import S3UploadAdapter
from general_manager.uploads.s3 import S3ProxyUploadAdapter
from general_manager.uploads.types import ObjectVersion, UploadTransport


class MissingObjectError(Exception):
    response: ClassVar[dict[str, dict[str, str]]] = {"Error": {"Code": "NoSuchKey"}}


class MissingVersionError(Exception):
    response: ClassVar[dict[str, dict[str, str]]] = {"Error": {"Code": "NoSuchVersion"}}


class FakeSDKError(Exception):
    response: ClassVar[dict[str, dict[str, str]]] = {"Error": {"Code": "InternalError"}}


class PreconditionError(Exception):
    response: ClassVar[dict[str, dict[str, str]]] = {
        "Error": {"Code": "PreconditionFailed"}
    }


class FakeS3Client:
    def __init__(
        self,
        *,
        versioning: bool = True,
        conditional_copy: bool = True,
        conditional_put: bool = True,
        signature_version: object = "s3v4",
    ) -> None:
        self.versioning = versioning
        self.objects: dict[tuple[str, str | None], dict[str, Any]] = {}
        self.copy_calls: list[dict[str, Any]] = []
        self.put_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.head_calls: list[dict[str, Any]] = []
        self.presigned_url_calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_operations: set[str] = set()
        self.operation_errors: dict[str, Exception] = {}
        self.body_factory: Callable[[bytes], object] = BytesIO
        self.put_version_counter = 0

        def operation_model(name: str) -> SimpleNamespace:
            members: dict[str, object] = {}
            if conditional_copy and name == "CopyObject":
                members["IfNoneMatch"] = object()
            if conditional_put and name == "PutObject":
                members["IfNoneMatch"] = object()
            if name in {"GetObject", "DeleteObject"}:
                members["IfMatch"] = object()
            return SimpleNamespace(input_shape=SimpleNamespace(members=members))

        service_model = SimpleNamespace(operation_model=operation_model)
        self.meta = SimpleNamespace(
            service_model=service_model,
            config=SimpleNamespace(signature_version=signature_version),
        )

    def _fail_if(self, operation: str) -> None:
        if operation in self.operation_errors:
            raise self.operation_errors[operation]
        if operation in self.fail_operations:
            raise FakeSDKError

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, str]:
        assert kwargs["Bucket"] == "uploads"
        return {"Status": "Enabled" if self.versioning else "Suspended"}

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self._fail_if(f"presign_{'put' if operation == 'put_object' else 'get'}")
        self.presigned_url_calls.append((operation, dict(kwargs)))
        assert kwargs["Params"]["Bucket"] == "uploads"
        if operation == "put_object":
            return "https://signed.example.test/upload?signature=secret"
        assert operation == "get_object"
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
        if kwargs.get("IfMatch") is not None and kwargs["IfMatch"] != value["ETag"]:
            raise PreconditionError
        return {"Body": self.body_factory(value["Body"])}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self._fail_if("put")
        self.put_calls.append(dict(kwargs))
        assert kwargs["IfNoneMatch"] == "*"
        key = kwargs["Key"]
        if any(object_key == key for object_key, _version in self.objects):
            raise PreconditionError
        body = kwargs["Body"]
        payload = body.read() if hasattr(body, "read") else bytes(body)
        checksum = base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii")
        etag = f'"{hashlib.md5(payload, usedforsecurity=False).hexdigest()}"'
        self.put_version_counter += 1
        version_id = (
            f"proxy-put-version-{self.put_version_counter}" if self.versioning else None
        )
        self.objects[(key, version_id)] = {
            "VersionId": version_id,
            "ETag": etag,
            "ChecksumSHA256": checksum,
            "ContentLength": len(payload),
            "ContentType": kwargs.get("ContentType"),
            "Metadata": dict(kwargs.get("Metadata", {})),
            "Body": payload,
        }
        return {
            "VersionId": version_id,
            "ETag": etag,
            "ChecksumSHA256": checksum,
        }

    def copy_object(self, **kwargs: Any) -> dict[str, Any]:
        self._fail_if("copy")
        self.copy_calls.append(dict(kwargs))
        source = kwargs["CopySource"]
        if "VersionId" in source:
            assert source["VersionId"]
        assert kwargs["IfNoneMatch"] == "*"
        value = self._lookup_object(
            Key=source["Key"],
            VersionId=source.get("VersionId"),
        )
        if kwargs["CopySourceIfMatch"] != value["ETag"]:
            raise PreconditionError
        source_checksum = base64.b64decode(value["ChecksumSHA256"]).hex()
        assert kwargs["Metadata"]["gm-checksum-sha256"] == source_checksum
        assert not any(key == kwargs["Key"] for key, _version in self.objects)
        destination_version = "final-version" if self.versioning else None
        destination = (kwargs["Key"], destination_version)
        self.objects[destination] = {
            **value,
            "VersionId": destination_version,
            "Metadata": dict(kwargs["Metadata"]),
        }
        return {"VersionId": destination_version, "CopyObjectResult": {}}

    def delete_object(self, **kwargs: Any) -> None:
        self._fail_if("delete")
        self.delete_calls.append(dict(kwargs))
        if kwargs.get("IfMatch") is None:
            self.objects.pop((kwargs["Key"], kwargs.get("VersionId")), None)
            return
        value = self._lookup_object(
            Key=kwargs["Key"],
            VersionId=kwargs.get("VersionId"),
        )
        if kwargs.get("IfMatch") is not None and kwargs["IfMatch"] != value["ETag"]:
            raise PreconditionError
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
        ignore_url_parameters: bool = False,
        custom_domain: str | None = None,
        public_url_host: str = "uploads.s3.us-east-1.amazonaws.com",
    ) -> None:
        self.s3_client = client
        self.public = public
        self.endpoint_url = endpoint_url
        self.default_acl = default_acl
        self.object_parameters = object_parameters or {}
        self.ignore_url_parameters = ignore_url_parameters
        self.custom_domain = custom_domain
        self.public_url_host = public_url_host
        if conditional_copy is not None:
            self.supports_conditional_copy = conditional_copy

    def url(
        self,
        key: str,
        parameters: dict[str, str] | None = None,
    ) -> str:
        url = f"https://{self.public_url_host}/{key}"
        if parameters is not None and not self.ignore_url_parameters:
            assert set(parameters) == {"VersionId"}
            url = f"{url}?versionId={parameters['VersionId']}"
        return url


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


def test_s3_inspection_distinguishes_missing_object_from_storage_outage() -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with pytest.raises(UploadObjectMissingError):
        adapter.inspect_staged("gm-staging/missing.bin")


def test_s3_cleanup_accepts_real_missing_pending_attempt_and_superseded_final() -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(FakeS3Storage(client))
    source = ObjectVersion(
        version_id="stage-version-1",
        etag='"etag-1"',
        checksum_sha256="a" * 64,
        size=1,
        content_type="application/octet-stream",
    )
    intent = SimpleNamespace(
        id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        staging_key="gm-staging/missing.bin",
        transfer_attempt_count=1,
        final_key="files/missing.bin",
        final_object_version={},
    )

    finalization._delete_staging_objects(adapter, intent, source)
    finalization._delete_superseded_final(adapter, intent, source)

    assert client.delete_calls == [
        {
            "Bucket": "uploads",
            "Key": "gm-staging/missing.bin",
            "VersionId": "stage-version-1",
        }
    ]

    client.operation_errors["head"] = FakeSDKError()
    with pytest.raises(UploadStorageError):
        adapter.inspect_staged("gm-staging/missing.bin")


def test_s3_direct_mode_requires_versioning() -> None:
    client = FakeS3Client(versioning=False)
    storage = FakeS3Storage(client)

    assert S3UploadAdapter.supports_direct(storage) is False
    resolved = UploadAdapterRegistry().resolve(storage)
    assert isinstance(resolved, S3ProxyUploadAdapter)
    assert resolved.adapter_id == "s3-proxy"
    assert UploadAdapterRegistry().resolve_by_id("s3-proxy", 1, storage).__class__ is (
        S3ProxyUploadAdapter
    )


def test_s3_proxy_stages_and_materializes_without_overwriting() -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    stage_key = "gm-staging/proxy.bin"
    payload = b"proxied payload"
    version = adapter.save_stage(
        stage_key,
        [payload[:4], payload[4:]],
        content_type="text/plain",
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )

    assert version.version_id is None
    assert version.etag is not None
    assert adapter.inspect_staged(stage_key) == version
    assert (
        adapter.materialize(
            stage_key,
            version,
            "files/proxy.bin",
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )
        == "files/proxy.bin"
    )

    with pytest.raises(UploadTransferConflictError):
        adapter.save_stage(
            stage_key,
            [b"different"],
            content_type="text/plain",
        )
    assert client.objects[(stage_key, None)]["Body"] == payload


def test_s3_proxy_fallback_does_not_require_conditional_copy() -> None:
    client = FakeS3Client(versioning=False, conditional_copy=False)
    storage = FakeS3Storage(client)

    adapter = UploadAdapterRegistry().resolve(storage)

    assert isinstance(adapter, S3ProxyUploadAdapter)
    version = adapter.save_stage(
        "gm-staging/no-copy.bin",
        [b"streamed through server"],
        content_type="application/octet-stream",
    )
    assert (
        adapter.materialize(
            "gm-staging/no-copy.bin",
            version,
            "files/no-copy.bin",
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )
        == "files/no-copy.bin"
    )
    assert client.copy_calls == []


def test_s3_proxy_materialization_rejects_source_and_destination_races() -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    stage_key = "gm-staging/race.bin"
    version = adapter.save_stage(
        stage_key,
        [b"original"],
        content_type="application/octet-stream",
    )
    replacement = b"replacement"
    client.objects[(stage_key, None)] = {
        **client.objects[(stage_key, None)],
        "Body": replacement,
        "ETag": '"replacement-etag"',
        "ChecksumSHA256": base64.b64encode(hashlib.sha256(replacement).digest()).decode(
            "ascii"
        ),
        "ContentLength": len(replacement),
    }

    with pytest.raises(UploadStorageChangedError):
        adapter.materialize(
            stage_key,
            version,
            "files/race.bin",
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )

    client.objects[("files/occupied.bin", None)] = dict(
        client.objects[(stage_key, None)]
    )
    with pytest.raises(UploadTransferConflictError):
        adapter.materialize(
            stage_key,
            adapter.inspect_staged(stage_key),
            "files/occupied.bin",
            intent_id=UUID("1aeff4c6-4895-4114-a984-b3d136083d33"),
        )


def test_s3_proxy_exact_cleanup_preserves_recreated_object() -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    key = "gm-staging/delete-race.bin"
    version = adapter.save_stage(
        key,
        [b"original"],
        content_type="application/octet-stream",
    )
    replacement = b"replacement"
    client.objects[(key, None)] = {
        **client.objects[(key, None)],
        "Body": replacement,
        "ETag": '"replacement-etag"',
        "ChecksumSHA256": base64.b64encode(hashlib.sha256(replacement).digest()).decode(
            "ascii"
        ),
        "ContentLength": len(replacement),
    }

    with pytest.raises(UploadStorageChangedError):
        adapter.delete_stage(key, version)

    assert client.objects[(key, None)]["Body"] == replacement


def test_s3_proxy_versioned_fallback_deletes_exact_version_not_delete_marker() -> None:
    client = FakeS3Client(versioning=True, conditional_copy=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    key = "gm-staging/versioned-proxy.bin"
    payload = b"versioned proxy payload"
    version_id = "proxy-version-1"
    client.objects[(key, version_id)] = {
        "VersionId": version_id,
        "ETag": '"proxy-etag-1"',
        "ChecksumSHA256": base64.b64encode(hashlib.sha256(payload).digest()).decode(
            "ascii"
        ),
        "ContentLength": len(payload),
        "ContentType": "application/octet-stream",
        "Metadata": {},
        "Body": payload,
    }

    version = adapter.inspect_staged(key)
    assert version.version_id == version_id

    adapter.delete_stage(key, version)

    assert (key, version_id) not in client.objects
    assert client.delete_calls[-1]["VersionId"] == version_id


def test_s3_proxy_maps_missing_exact_version_to_public_absence_signal() -> None:
    client = FakeS3Client(versioning=True, conditional_copy=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    version = ObjectVersion(
        version_id="already-deleted-version",
        etag='"deleted-etag"',
        checksum_sha256="a" * 64,
        size=1,
        content_type="application/octet-stream",
    )
    client.operation_errors["head"] = MissingVersionError()

    with pytest.raises(UploadObjectMissingError):
        adapter.delete_stage("gm-staging/deleted-version.bin", version)


def test_s3_proxy_versioned_no_copy_flow_binds_source_and_final_versions() -> None:
    client = FakeS3Client(versioning=True, conditional_copy=False)
    storage = FakeS3Storage(client)
    adapter = UploadAdapterRegistry().resolve(storage)
    assert isinstance(adapter, S3ProxyUploadAdapter)
    stage_key = "gm-staging/versioned-flow.bin"
    final_key = "files/versioned-flow.bin"
    version = adapter.save_stage(
        stage_key,
        [b"versioned flow"],
        content_type="application/octet-stream",
    )
    assert version.version_id

    adapter.materialize(
        stage_key,
        version,
        final_key,
        intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
    )
    final_version = adapter.inspect_materialized(
        final_key,
        version,
        intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
    )
    assert final_version.version_id
    assert final_version.version_id != version.version_id

    adapter.delete_stage(stage_key, version)

    assert (stage_key, version.version_id) not in client.objects
    assert (final_key, final_version.version_id) in client.objects


def test_s3_proxy_replaced_cleanup_conditionally_deletes_only_claimed_etag() -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    key = "files/old.bin"
    version = adapter.save_stage(
        key,
        [b"old payload"],
        content_type="application/octet-stream",
    )
    cleanup_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")
    claimed = adapter.plan_replaced_object_claim(
        key,
        version,
        cleanup_id=cleanup_id,
    )
    adapter.claim_replaced_object(key, claimed, cleanup_id=cleanup_id)
    replacement = b"new payload"
    client.objects[(key, None)] = {
        **client.objects[(key, None)],
        "Body": replacement,
        "ETag": '"replacement-etag"',
        "ChecksumSHA256": base64.b64encode(hashlib.sha256(replacement).digest()).decode(
            "ascii"
        ),
        "ContentLength": len(replacement),
    }

    with pytest.raises(UploadStorageChangedError):
        adapter.delete_claimed_object(claimed, cleanup_id=cleanup_id)

    assert client.objects[(key, None)]["Body"] == replacement


@pytest.mark.parametrize("signature_version", [None, "s3", "v2", 4, True])
def test_s3_direct_mode_requires_explicit_sigv4_configuration(
    signature_version: object,
) -> None:
    client = FakeS3Client(signature_version=signature_version)
    storage = FakeS3Storage(client)

    assert S3UploadAdapter.supports_direct(storage) is False
    assert isinstance(UploadAdapterRegistry().resolve(storage), ProxyUploadAdapter)
    with pytest.raises(UploadBackendUnsupportedError):
        S3UploadAdapter(storage)


def test_s3_direct_construction_rejects_suspended_versioning() -> None:
    storage = FakeS3Storage(FakeS3Client(versioning=False))

    with pytest.raises(UploadBackendUnsupportedError):
        S3UploadAdapter(storage)


def test_s3_direct_mode_requires_conditional_destination_copy() -> None:
    client = FakeS3Client(conditional_copy=False)
    storage = FakeS3Storage(client)

    assert S3UploadAdapter.supports_direct(storage) is False
    assert isinstance(UploadAdapterRegistry().resolve(storage), S3ProxyUploadAdapter)


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


def test_s3_adapters_reject_insecure_aws_endpoint() -> None:
    storage = FakeS3Storage(
        FakeS3Client(versioning=False),
        endpoint_url="http://s3.us-east-1.amazonaws.com",
    )

    assert S3UploadAdapter.supports_direct(storage) is False
    assert isinstance(UploadAdapterRegistry().resolve(storage), ProxyUploadAdapter)
    with pytest.raises(UploadBackendUnsupportedError):
        S3ProxyUploadAdapter(storage)


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
    assert instructions.method == "PUT"
    assert instructions.fields == {}
    assert instructions.headers["Content-Type"] == "text/plain"
    assert instructions.headers["Content-Length"] == "7"
    assert instructions.headers["x-amz-checksum-sha256"] == base64.b64encode(
        bytes.fromhex(checksum)
    ).decode("ascii")
    operation, call = client.presigned_url_calls[0]
    assert operation == "put_object"
    assert call["Params"] == {
        "Bucket": "uploads",
        "Key": "gm-staging/intent.bin",
        "ContentType": "text/plain",
        "ContentLength": 7,
        "ChecksumSHA256": instructions.headers["x-amz-checksum-sha256"],
    }
    assert "gm-staging/intent.bin" not in repr(instructions)
    assert "credential=secret" not in repr(instructions)


def test_s3_presigned_put_rejects_objects_above_the_single_put_limit() -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.create_upload_instructions(
            stage_key="gm-staging/intent.bin",
            upload_url=None,
            content_type="application/octet-stream",
            size=5 * 1024**3 + 1,
            checksum_sha256="a" * 64,
        )

    assert client.presigned_url_calls == []


@pytest.mark.parametrize("expires_in", [604_801, 10_000_000])
def test_s3_presigned_put_rejects_expiry_beyond_sigv4_limit(
    expires_in: int,
) -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.create_upload_instructions(
            stage_key="gm-staging/intent.bin",
            upload_url=None,
            content_type="application/octet-stream",
            size=1,
            checksum_sha256="a" * 64,
            expires_in=expires_in,
        )

    assert client.presigned_url_calls == []


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

    assert "x-amz-acl" not in instructions.headers
    assert "ACL" not in client.presigned_url_calls[0][1]["Params"]


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

    assert instructions.headers["x-amz-acl"] == "bucket-owner-full-control"
    assert client.presigned_url_calls[0][1]["Params"]["ACL"] == (
        "bucket-owner-full-control"
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

    assert "x-amz-acl" not in instructions.headers


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

    expected_headers = {
        "x-amz-server-side-encryption": "aws:kms",
        "x-amz-server-side-encryption-aws-kms-key-id": "alias/uploads",
        "x-amz-server-side-encryption-bucket-key-enabled": "true",
    }
    assert expected_headers.items() <= instructions.headers.items()
    assert "x-amz-acl" not in instructions.headers
    assert "x-amz-storage-class" not in instructions.headers
    put_params = client.presigned_url_calls[0][1]["Params"]
    assert put_params["ServerSideEncryption"] == "aws:kms"
    assert put_params["SSEKMSKeyId"] == "alias/uploads"
    assert put_params["BucketKeyEnabled"] is True
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

    assert "x-amz-acl" not in instructions.headers
    assert "x-amz-storage-class" not in instructions.headers
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


@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"MAX_BYTES": 1}})
def test_s3_exact_stream_does_not_reapply_global_size_over_field_policy() -> None:
    client = FakeS3Client()
    expected = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with adapter.open_stage("gm-staging/intent.bin", expected) as opened:
        assert opened.read() == b"immutable staged payload"


def test_s3_spools_non_seekable_short_read_image_body_for_pillow() -> None:
    from PIL import Image

    class NonSeekableBody:
        def __init__(self, content: bytes) -> None:
            self.content = content
            self.offset = 0
            self.closed = False

        def read(self, size: int = -1) -> bytes:
            del size
            if self.offset >= len(self.content):
                return b""
            chunk = self.content[self.offset : self.offset + 1]
            self.offset += 1
            return chunk

        def close(self) -> None:
            self.closed = True

    encoded = BytesIO()
    Image.new("RGB", (3, 2)).save(encoded, format="PNG")
    payload = encoded.getvalue()
    checksum = hashlib.sha256(payload).digest()
    client = FakeS3Client()
    bodies: list[NonSeekableBody] = []

    def body_factory(content: bytes) -> NonSeekableBody:
        body = NonSeekableBody(content)
        bodies.append(body)
        return body

    client.body_factory = body_factory
    client.objects[("gm-staging/image.png", "image-version-1")] = {
        "VersionId": "image-version-1",
        "ETag": '"image-etag"',
        "ChecksumSHA256": base64.b64encode(checksum).decode("ascii"),
        "ContentLength": len(payload),
        "ContentType": "image/png",
        "Metadata": {},
        "Body": payload,
    }
    version = ObjectVersion(
        version_id="image-version-1",
        etag='"image-etag"',
        checksum_sha256=checksum.hex(),
        size=len(payload),
        content_type="image/png",
    )
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with adapter.open_stage("gm-staging/image.png", version) as opened:
        assert opened.seekable()
        with Image.open(opened) as image:
            assert image.size == (3, 2)

    assert bodies[0].closed is True


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


def test_s3_inspects_and_deletes_exact_intent_owned_final_version() -> None:
    client = FakeS3Client()
    source_version = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))
    intent_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")
    final_key = adapter.materialize(
        "gm-staging/intent.bin",
        source_version,
        "files/report.txt",
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

    assert final_version.version_id == "final-version"
    assert client.delete_calls[-1] == {
        "Bucket": "uploads",
        "Key": "files/report.txt",
        "VersionId": "final-version",
    }


def test_s3_replaced_object_claim_is_the_exact_persisted_version_id() -> None:
    client = FakeS3Client()
    version = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))
    cleanup_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")

    claimed = adapter.plan_replaced_object_claim(
        "gm-staging/intent.bin",
        version,
        cleanup_id=cleanup_id,
    )
    adapter.claim_replaced_object(
        "gm-staging/intent.bin",
        claimed,
        cleanup_id=cleanup_id,
    )
    adapter.delete_claimed_object(claimed, cleanup_id=cleanup_id)

    assert claimed.key == "gm-staging/intent.bin"
    assert claimed.version.version_id == "stage-version-1"
    assert client.delete_calls[-1] == {
        "Bucket": "uploads",
        "Key": "gm-staging/intent.bin",
        "VersionId": "stage-version-1",
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
        "https://uploads.s3.us-east-1.amazonaws.com/files/report.txt"
    )
    assert public.public_download_url(
        "files/report.txt",
        version=version,
    ) == (
        "https://uploads.s3.us-east-1.amazonaws.com/files/report.txt"
        "?versionId=stage-version-1"
    )


def test_s3_public_download_rejects_storage_that_drops_version_id() -> None:
    client = FakeS3Client()
    version = _stage(client)
    storage = FakeS3Storage(client, public=True, ignore_url_parameters=True)
    storage.upload_staging_prefix_private = True
    adapter = S3UploadAdapter(storage)

    with pytest.raises(PublicUploadUrlUnsupportedError):
        adapter.public_download_url("files/report.txt", version=version)


def test_s3_public_download_rejects_custom_domain_even_when_query_is_preserved() -> (
    None
):
    client = FakeS3Client()
    version = _stage(client)
    storage = FakeS3Storage(
        client,
        public=True,
        custom_domain="cdn.example.test",
    )
    storage.upload_staging_prefix_private = True
    adapter = S3UploadAdapter(storage)

    with pytest.raises(PublicUploadUrlUnsupportedError):
        adapter.public_download_url("files/report.txt", version=version)


def test_s3_public_download_rejects_an_unexpected_non_s3_host() -> None:
    client = FakeS3Client()
    version = _stage(client)
    storage = FakeS3Storage(
        client,
        public=True,
        public_url_host="cdn.example.test",
    )
    storage.upload_staging_prefix_private = True
    adapter = S3UploadAdapter(storage)

    with pytest.raises(PublicUploadUrlUnsupportedError):
        adapter.public_download_url("files/report.txt", version=version)


def test_s3_public_download_requires_matching_custom_endpoint_origin() -> None:
    client = FakeS3Client()
    version = _stage(client)
    storage = FakeS3Storage(
        client,
        public=True,
        endpoint_url="https://objects.example.test:9443",
        conditional_copy=True,
        public_url_host="objects.example.test:9443",
    )
    storage.upload_staging_prefix_private = True
    adapter = S3UploadAdapter(storage)

    assert adapter.public_download_url("files/report.txt", version=version) == (
        "https://objects.example.test:9443/files/report.txt?versionId=stage-version-1"
    )


@pytest.mark.parametrize(
    ("endpoint_url", "public_url_host"),
    [
        ("https://objects.example.test:9443", "objects.example.test"),
        ("http://objects.example.test", "objects.example.test"),
    ],
)
def test_s3_public_download_rejects_custom_endpoint_scheme_or_port_mismatch(
    endpoint_url: str,
    public_url_host: str,
) -> None:
    client = FakeS3Client()
    version = _stage(client)
    storage = FakeS3Storage(
        client,
        public=True,
        endpoint_url=endpoint_url,
        conditional_copy=True,
        public_url_host=public_url_host,
    )
    storage.upload_staging_prefix_private = True
    adapter = S3UploadAdapter(storage)

    with pytest.raises(PublicUploadUrlUnsupportedError):
        adapter.public_download_url("files/report.txt", version=version)


def test_s3_private_download_binds_exact_version_and_response_headers() -> None:
    client = FakeS3Client()
    version = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))

    assert (
        adapter.private_download_url(
            "files/report.txt",
            expires_in=45,
            version=version,
            response_content_type="text/plain",
            response_content_disposition=(
                "inline; filename=\"report.txt\"; filename*=utf-8''report.txt"
            ),
        )
        == "https://signed.example.test/get?signature=secret"
    )
    assert client.presigned_url_calls == [
        (
            "get_object",
            {
                "Params": {
                    "Bucket": "uploads",
                    "Key": "files/report.txt",
                    "VersionId": "stage-version-1",
                    "ResponseContentType": "text/plain",
                    "ResponseContentDisposition": (
                        "inline; filename=\"report.txt\"; filename*=utf-8''report.txt"
                    ),
                },
                "ExpiresIn": 45,
            },
        )
    ]


@pytest.mark.parametrize("expires_in", [True, 0, 604_801])
def test_s3_private_download_rejects_invalid_sigv4_expiry(expires_in: object) -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.private_download_url("files/report.txt", expires_in=expires_in)  # type: ignore[arg-type]

    assert client.presigned_url_calls == []


def test_s3_private_download_rejects_checksum_only_retained_version() -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(FakeS3Storage(client))
    checksum_only = ObjectVersion(
        version_id=None,
        etag=None,
        checksum_sha256="a" * 64,
        size=1,
        content_type="text/plain",
    )

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.private_download_url(
            "files/report.txt",
            expires_in=60,
            version=checksum_only,
        )

    assert client.presigned_url_calls == []


def test_s3_download_inspection_never_falls_back_to_latest_version() -> None:
    client = FakeS3Client()
    retained = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))

    assert adapter.inspect_download("gm-staging/intent.bin", retained) == retained
    del client.objects[("gm-staging/intent.bin", "stage-version-1")]
    client.objects[("gm-staging/intent.bin", "newer-version")] = {
        "VersionId": "newer-version",
        "ETag": '"newer"',
        "ChecksumSHA256": base64.b64encode(b"n" * 32).decode("ascii"),
        "ContentLength": 5,
        "ContentType": "text/plain",
        "Metadata": {},
        "Body": b"newer",
    }

    with pytest.raises(UploadStorageError):
        adapter.inspect_download("gm-staging/intent.bin", retained)

    assert client.head_calls[-1]["VersionId"] == "stage-version-1"


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
    ["presign_put", "head", "get", "delete", "presign_get"],
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
        if operation == "presign_put":
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


def test_s3_proxy_exposes_only_supported_instructions_and_urls() -> None:
    client = FakeS3Client(versioning=False)
    storage = FakeS3Storage(client, public=True)
    storage.upload_staging_prefix_private = True
    adapter = S3ProxyUploadAdapter(storage)

    assert adapter.supports_direct(storage) is False
    assert adapter.supports_public_urls is True
    with pytest.raises(ValueError, match="upload_url is required"):
        adapter.create_upload_instructions(
            stage_key="gm-staging/proxy.bin",
            upload_url=None,
            content_type="text/plain",
            size=3,
            checksum_sha256="a" * 64,
        )

    instructions = adapter.create_upload_instructions(
        stage_key="gm-staging/proxy.bin",
        upload_url="/uploads/proxy/intent",
        content_type="text/plain",
        size=3,
        checksum_sha256="a" * 64,
        headers={"X-Upload-Token": "token"},
    )

    assert instructions.transport is UploadTransport.PROXY
    assert instructions.url == "/uploads/proxy/intent"
    assert instructions.headers == {"X-Upload-Token": "token"}
    assert adapter.public_url("files/proxy.bin").endswith("/files/proxy.bin")
    assert adapter.storage_fingerprint().startswith("sha256:")
    with pytest.raises(UploadBackendUnsupportedError):
        adapter.private_download_url("files/proxy.bin", expires_in=60)

    private_adapter = S3ProxyUploadAdapter(FakeS3Storage(FakeS3Client()))
    with pytest.raises(PublicUploadUrlUnsupportedError):
        private_adapter.public_url("files/private.bin")


def test_s3_proxy_rejects_invalid_stream_metadata_before_writing() -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))

    with pytest.raises(UploadChecksumMismatchError):
        adapter.save_stage(
            "gm-staging/checksum.bin",
            [b"payload"],
            content_type="text/plain",
            checksum_sha256="0" * 64,
        )
    with pytest.raises(UploadStorageError, match="size did not match"):
        adapter.save_stage(
            "gm-staging/size.bin",
            [b"payload"],
            content_type="text/plain",
            size=8,
        )

    assert client.put_calls == []


def test_s3_proxy_stage_write_is_retry_safe_and_preserves_upload_errors() -> None:
    payload = b"retry-safe payload"
    checksum = hashlib.sha256(payload).hexdigest()
    identity = {
        "gm-stage-state": "completed",
        "gm-checksum-sha256": checksum,
    }
    client = FakeS3Client(versioning=False)
    client.objects[("gm-staging/retry.bin", None)] = {
        "VersionId": None,
        "ETag": '"existing"',
        "ChecksumSHA256": base64.b64encode(bytes.fromhex(checksum)).decode("ascii"),
        "ContentLength": len(payload),
        "ContentType": "text/plain",
        "Metadata": identity,
        "Body": payload,
    }
    client.operation_errors["put"] = PreconditionError()
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))

    retried = adapter.save_stage(
        "gm-staging/retry.bin",
        [payload],
        content_type="text/plain",
        checksum_sha256=checksum,
        size=len(payload),
    )

    assert retried.etag == '"existing"'

    explicit = UploadStorageChangedError()
    client.operation_errors["put"] = explicit
    with pytest.raises(UploadStorageChangedError) as captured:
        adapter.save_stage(
            "gm-staging/explicit.bin",
            [payload],
            content_type="text/plain",
        )
    assert captured.value is explicit


def test_s3_proxy_stage_write_distinguishes_conflicts_from_outages() -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    client.operation_errors["put"] = PreconditionError()

    with pytest.raises(UploadTransferConflictError):
        adapter.save_stage(
            "gm-staging/conflict.bin",
            [b"payload"],
            content_type="text/plain",
        )

    client.operation_errors["put"] = FakeSDKError()
    with pytest.raises(UploadStorageError) as captured:
        adapter.save_stage(
            "gm-staging/outage.bin",
            [b"payload"],
            content_type="text/plain",
        )
    assert isinstance(captured.value.__cause__, FakeSDKError)


def test_s3_proxy_detects_stage_metadata_changed_after_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client(versioning=False)
    original_put = client.put_object

    def put_with_changed_metadata(**kwargs: Any) -> dict[str, Any]:
        response = original_put(**kwargs)
        client.objects[(kwargs["Key"], None)]["Metadata"] = {}
        return response

    monkeypatch.setattr(client, "put_object", put_with_changed_metadata)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))

    with pytest.raises(UploadStorageChangedError):
        adapter.save_stage(
            "gm-staging/changed.bin",
            [b"payload"],
            content_type="text/plain",
        )


def test_s3_proxy_materialization_retries_matching_destination() -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    version = adapter.save_stage(
        "gm-staging/retry-materialize.bin",
        [b"payload"],
        content_type="text/plain",
    )
    intent_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")

    adapter.materialize(
        "gm-staging/retry-materialize.bin",
        version,
        "files/retry-materialize.bin",
        intent_id=intent_id,
    )
    assert (
        adapter.materialize(
            "gm-staging/retry-materialize.bin",
            version,
            "files/retry-materialize.bin",
            intent_id=intent_id,
        )
        == "files/retry-materialize.bin"
    )

    checksum_only = ObjectVersion(
        version_id=None,
        etag=None,
        checksum_sha256=version.checksum_sha256,
        size=version.size,
    )
    with pytest.raises(UploadBackendUnsupportedError, match="requires an ETag"):
        adapter.materialize(
            "gm-staging/retry-materialize.bin",
            checksum_only,
            "files/no-etag.bin",
            intent_id=intent_id,
        )


@pytest.mark.parametrize(
    ("race", "error", "expected_exception"),
    [
        ("matching", FakeSDKError(), None),
        ("occupied", FakeSDKError(), UploadTransferConflictError),
        (None, PreconditionError(), UploadTransferConflictError),
        (None, FakeSDKError(), UploadStorageError),
    ],
)
def test_s3_proxy_materialization_normalizes_write_races(
    monkeypatch: pytest.MonkeyPatch,
    race: str | None,
    error: Exception,
    expected_exception: type[Exception] | None,
) -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    stage_key = "gm-staging/materialize-race.bin"
    final_key = "files/materialize-race.bin"
    version = adapter.save_stage(stage_key, [b"payload"], content_type="text/plain")
    intent_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")

    def racing_put(**kwargs: Any) -> dict[str, Any]:
        if kwargs["Key"] == final_key and race is not None:
            metadata = dict(kwargs["Metadata"])
            if race == "occupied":
                metadata["gm-intent-id"] = "different-intent"
            client.objects[(final_key, None)] = {
                **client.objects[(stage_key, None)],
                "Metadata": metadata,
            }
        raise error

    monkeypatch.setattr(client, "put_object", racing_put)

    if expected_exception is None:
        assert (
            adapter.materialize(
                stage_key,
                version,
                final_key,
                intent_id=intent_id,
            )
            == final_key
        )
    else:
        with pytest.raises(expected_exception):
            adapter.materialize(
                stage_key,
                version,
                final_key,
                intent_id=intent_id,
            )


def test_s3_proxy_detects_destination_changed_after_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    stage_key = "gm-staging/changed-final.bin"
    final_key = "files/changed-final.bin"
    version = adapter.save_stage(stage_key, [b"payload"], content_type="text/plain")
    original_put = client.put_object

    def put_with_changed_metadata(**kwargs: Any) -> dict[str, Any]:
        response = original_put(**kwargs)
        if kwargs["Key"] == final_key:
            client.objects[(final_key, None)]["Metadata"] = {}
        return response

    monkeypatch.setattr(client, "put_object", put_with_changed_metadata)

    with pytest.raises(UploadStorageChangedError):
        adapter.materialize(
            stage_key,
            version,
            final_key,
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )


def test_s3_proxy_reads_require_an_etag_and_normalize_backend_failures() -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    version = adapter.save_stage(
        "gm-staging/read.bin",
        [b"payload"],
        content_type="text/plain",
    )
    checksum_only = ObjectVersion(
        version_id=None,
        etag=None,
        checksum_sha256=version.checksum_sha256,
        size=version.size,
    )

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.open_stage("gm-staging/read.bin", checksum_only)
    with pytest.raises(UploadBackendUnsupportedError):
        adapter.delete_stage("gm-staging/read.bin")

    client.operation_errors["get"] = MissingObjectError()
    with pytest.raises(UploadObjectMissingError):
        adapter.open_stage("gm-staging/read.bin", version)
    client.operation_errors["get"] = FakeSDKError()
    with pytest.raises(UploadStorageError):
        adapter.open_stage("gm-staging/read.bin", version)


def test_s3_proxy_download_inspection_binds_the_claimed_object() -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    key = "files/download.bin"
    version = adapter.save_stage(key, [b"payload"], content_type="text/plain")

    assert adapter.inspect_replaced_object(key) == version
    assert adapter.inspect_download(key, version) == version
    with adapter.open_download(key, version) as opened:
        assert opened.read() == b"payload"

    changed = ObjectVersion(
        version_id=version.version_id,
        etag='"changed"',
        checksum_sha256=version.checksum_sha256,
        size=version.size,
        content_type=version.content_type,
    )
    with pytest.raises(UploadStorageChangedError):
        adapter.inspect_download(key, changed)
    del client.objects[(key, None)]
    with pytest.raises(UploadObjectMissingError):
        adapter.inspect_download(key, version)
    with pytest.raises(UploadObjectMissingError):
        adapter.inspect_staged(key)

    client.operation_errors["head"] = FakeSDKError()
    with pytest.raises(UploadStorageError):
        adapter.inspect_staged(key)


def test_s3_proxy_materialized_inspection_and_deletion_require_intent_identity() -> (
    None
):
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    stage_key = "gm-staging/final-inspection.bin"
    final_key = "files/final-inspection.bin"
    intent_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")
    version = adapter.save_stage(stage_key, [b"payload"], content_type="text/plain")

    with pytest.raises(UploadObjectMissingError):
        adapter.inspect_materialized(final_key, version, intent_id=intent_id)

    adapter.materialize(stage_key, version, final_key, intent_id=intent_id)
    final_version = adapter.inspect_materialized(
        final_key,
        version,
        intent_id=intent_id,
    )
    with pytest.raises(UploadStorageChangedError):
        adapter.inspect_materialized(
            final_key,
            version,
            intent_id=UUID("1aeff4c6-4895-4114-a984-b3d136083d33"),
        )

    adapter.delete_materialized(final_key, final_version, intent_id=intent_id)
    assert (final_key, None) not in client.objects


@pytest.mark.parametrize(
    ("error", "expected_exception"),
    [
        (MissingObjectError(), UploadObjectMissingError),
        (PreconditionError(), UploadStorageChangedError),
        (FakeSDKError(), UploadStorageError),
    ],
)
def test_s3_proxy_delete_normalizes_conditional_backend_failures(
    error: Exception,
    expected_exception: type[Exception],
) -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    key = "gm-staging/delete-error.bin"
    version = adapter.save_stage(key, [b"payload"], content_type="text/plain")
    client.operation_errors["delete"] = error

    with pytest.raises(expected_exception):
        adapter.delete_stage(key, version)


def test_s3_proxy_cleanup_claims_tolerate_absence_but_reject_replacement() -> None:
    client = FakeS3Client(versioning=False)
    adapter = S3ProxyUploadAdapter(FakeS3Storage(client))
    key = "files/claimed.bin"
    version = adapter.save_stage(key, [b"payload"], content_type="text/plain")
    cleanup_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")
    claimed = adapter.plan_replaced_object_claim(key, version, cleanup_id=cleanup_id)

    with pytest.raises(UploadStorageChangedError):
        adapter.claim_replaced_object(
            key,
            ClaimedObject(key="files/different.bin", version=version),
            cleanup_id=cleanup_id,
        )

    del client.objects[(key, None)]
    adapter.claim_replaced_object(key, claimed, cleanup_id=cleanup_id)
    adapter.delete_claimed_object(claimed, cleanup_id=cleanup_id)

    version = adapter.save_stage(key, [b"payload"], content_type="text/plain")
    claimed = adapter.plan_replaced_object_claim(key, version, cleanup_id=cleanup_id)
    client.objects[(key, None)]["ETag"] = '"replacement"'
    with pytest.raises(UploadStorageChangedError):
        adapter.claim_replaced_object(key, claimed, cleanup_id=cleanup_id)


def test_s3_direct_rejects_malformed_signing_and_incomplete_copy_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client()
    adapter = S3UploadAdapter(FakeS3Storage(client))

    def malformed_url(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return ""

    monkeypatch.setattr(client, "generate_presigned_url", malformed_url)

    with pytest.raises(UploadStorageError, match="malformed upload instructions"):
        adapter.create_upload_instructions(
            stage_key="gm-staging/malformed.bin",
            upload_url=None,
            content_type="text/plain",
            size=7,
            checksum_sha256=hashlib.sha256(b"payload").hexdigest(),
        )

    checksum_only = ObjectVersion(
        version_id=None,
        etag='"etag"',
        checksum_sha256="a" * 64,
        size=1,
    )
    with pytest.raises(UploadBackendUnsupportedError, match="VersionId and ETag"):
        adapter.materialize(
            "gm-staging/incomplete.bin",
            checksum_only,
            "files/incomplete.bin",
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )


@pytest.mark.parametrize(
    ("race", "error", "expected_exception"),
    [
        ("matching", FakeSDKError(), None),
        ("occupied", FakeSDKError(), UploadTransferConflictError),
        (None, PreconditionError(), UploadTransferConflictError),
        (None, FakeSDKError(), UploadStorageError),
    ],
)
def test_s3_direct_materialization_normalizes_copy_races(
    monkeypatch: pytest.MonkeyPatch,
    race: str | None,
    error: Exception,
    expected_exception: type[Exception] | None,
) -> None:
    client = FakeS3Client()
    version = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))
    final_key = "files/copy-race.bin"
    intent_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")

    def racing_copy(**kwargs: Any) -> dict[str, Any]:
        if race is not None:
            metadata = dict(kwargs["Metadata"])
            if race == "occupied":
                metadata["gm-intent-id"] = "different-intent"
            client.objects[(final_key, "raced-version")] = {
                **client.objects[("gm-staging/intent.bin", "stage-version-1")],
                "VersionId": "raced-version",
                "Metadata": metadata,
            }
        raise error

    monkeypatch.setattr(client, "copy_object", racing_copy)

    if expected_exception is None:
        assert (
            adapter.materialize(
                "gm-staging/intent.bin",
                version,
                final_key,
                intent_id=intent_id,
            )
            == final_key
        )
    else:
        with pytest.raises(expected_exception):
            adapter.materialize(
                "gm-staging/intent.bin",
                version,
                final_key,
                intent_id=intent_id,
            )


def test_s3_direct_detects_destination_changed_after_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client()
    version = _stage(client)
    original_copy = client.copy_object

    def copy_with_changed_metadata(**kwargs: Any) -> dict[str, Any]:
        response = original_copy(**kwargs)
        client.objects[(kwargs["Key"], "final-version")]["Metadata"] = {}
        return response

    monkeypatch.setattr(client, "copy_object", copy_with_changed_metadata)
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with pytest.raises(UploadStorageChangedError, match="materialization identity"):
        adapter.materialize(
            "gm-staging/intent.bin",
            version,
            "files/changed-copy.bin",
            intent_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )


def test_s3_direct_stream_rejects_missing_and_non_bytes_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client()
    version = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))
    no_version = ObjectVersion(
        version_id=None,
        etag=version.etag,
        checksum_sha256=version.checksum_sha256,
        size=version.size,
    )

    with pytest.raises(UploadBackendUnsupportedError, match="VersionId"):
        adapter.open_stage("gm-staging/intent.bin", no_version)

    def missing_body(**kwargs: Any) -> dict[str, object]:
        del kwargs
        return {"Body": object()}

    monkeypatch.setattr(client, "get_object", missing_body)
    with pytest.raises(UploadStorageError, match="without a body"):
        adapter.open_stage("gm-staging/intent.bin", version)

    class TextBody:
        def __init__(self) -> None:
            self.closed = False

        def read(self, size: int = -1) -> str:
            del size
            return "not bytes"

        def close(self) -> None:
            self.closed = True

    body = TextBody()

    def text_body(**kwargs: Any) -> dict[str, object]:
        del kwargs
        return {"Body": body}

    monkeypatch.setattr(client, "get_object", text_body)
    with pytest.raises(UploadStorageError):
        adapter.open_stage("gm-staging/intent.bin", version)
    assert body.closed is True


@pytest.mark.parametrize("failure", ["oversized", "short", "checksum"])
def test_s3_direct_stream_verifies_size_and_checksum(failure: str) -> None:
    client = FakeS3Client()
    version = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))
    if failure == "oversized":
        expected = ObjectVersion(
            version_id=version.version_id,
            etag=version.etag,
            checksum_sha256=version.checksum_sha256,
            size=version.size - 1,
        )
    elif failure == "short":
        client.objects[("gm-staging/intent.bin", "stage-version-1")]["Body"] = b"short"
        expected = version
    else:
        expected = ObjectVersion(
            version_id=version.version_id,
            etag=version.etag,
            checksum_sha256="a" * 64,
            size=version.size,
        )

    with pytest.raises(UploadStorageChangedError):
        adapter.open_stage("gm-staging/intent.bin", expected)


@pytest.mark.parametrize(
    ("close_error", "expected_exception"),
    [
        (FakeSDKError(), UploadStorageError),
        (UploadStorageChangedError(), UploadStorageChangedError),
    ],
)
def test_s3_direct_stream_propagates_body_close_failures(
    close_error: Exception,
    expected_exception: type[Exception],
) -> None:
    class FailingCloseBody(BytesIO):
        def close(self) -> None:
            raise close_error

    client = FakeS3Client()
    version = _stage(client)
    client.body_factory = FailingCloseBody
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with pytest.raises(expected_exception):
        adapter.open_stage("gm-staging/intent.bin", version)


def test_s3_direct_cleanup_requires_the_exact_immutable_object() -> None:
    client = FakeS3Client()
    version = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))
    cleanup_id = UUID("9c90741f-72ce-4f34-886c-297bc019db16")
    no_version = ObjectVersion(
        version_id=None,
        etag=version.etag,
        checksum_sha256=version.checksum_sha256,
        size=version.size,
    )

    assert adapter.inspect_replaced_object("gm-staging/intent.bin") == version
    with pytest.raises(UploadBackendUnsupportedError):
        adapter.plan_replaced_object_claim(
            "gm-staging/intent.bin",
            no_version,
            cleanup_id=cleanup_id,
        )
    with pytest.raises(UploadBackendUnsupportedError):
        adapter.delete_object("gm-staging/intent.bin", no_version)
    with pytest.raises(UploadBackendUnsupportedError):
        adapter.delete_claimed_object(
            ClaimedObject(key="gm-staging/intent.bin", version=no_version),
            cleanup_id=cleanup_id,
        )

    claimed = adapter.plan_replaced_object_claim(
        "gm-staging/intent.bin",
        version,
        cleanup_id=cleanup_id,
    )
    with pytest.raises(UploadStorageChangedError):
        adapter.claim_replaced_object(
            "different-key",
            claimed,
            cleanup_id=cleanup_id,
        )

    missing = ObjectVersion(
        version_id="missing-version",
        etag=version.etag,
        checksum_sha256=version.checksum_sha256,
        size=version.size,
    )
    del client.objects[("gm-staging/intent.bin", "stage-version-1")]
    with pytest.raises(UploadObjectMissingError):
        adapter.delete_object("gm-staging/intent.bin", missing)

    _stage(client)
    changed = ObjectVersion(
        version_id=version.version_id,
        etag='"changed"',
        checksum_sha256=version.checksum_sha256,
        size=version.size,
    )
    with pytest.raises(UploadStorageChangedError):
        adapter.delete_object("gm-staging/intent.bin", changed)


@pytest.mark.parametrize(
    ("error", "expected_exception"),
    [
        (MissingObjectError(), UploadObjectMissingError),
        (FakeSDKError(), UploadStorageError),
    ],
)
def test_s3_direct_claimed_deletion_normalizes_backend_failures(
    error: Exception,
    expected_exception: type[Exception],
) -> None:
    client = FakeS3Client()
    version = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))
    client.operation_errors["delete"] = error

    with pytest.raises(expected_exception):
        adapter.delete_claimed_object(
            ClaimedObject(key="gm-staging/intent.bin", version=version),
            cleanup_id=UUID("9c90741f-72ce-4f34-886c-297bc019db16"),
        )


def test_s3_direct_delete_stage_maps_missing_version() -> None:
    client = FakeS3Client()
    version = _stage(client)
    client.operation_errors["delete"] = MissingVersionError()
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with pytest.raises(UploadObjectMissingError):
        adapter.delete_stage("gm-staging/intent.bin", version)


def test_s3_direct_retained_download_rejects_missing_or_changed_identity() -> None:
    client = FakeS3Client()
    version = _stage(client)
    adapter = S3UploadAdapter(FakeS3Storage(client))
    no_version = ObjectVersion(
        version_id=None,
        etag=version.etag,
        checksum_sha256=version.checksum_sha256,
        size=version.size,
    )

    with pytest.raises(UploadBackendUnsupportedError):
        adapter.inspect_download("gm-staging/intent.bin", no_version)
    with pytest.raises(PublicUploadUrlUnsupportedError):
        adapter.public_download_url("gm-staging/intent.bin", version=no_version)

    changed = ObjectVersion(
        version_id=version.version_id,
        etag=version.etag,
        checksum_sha256="a" * 64,
        size=version.size,
    )
    with pytest.raises(UploadStorageChangedError):
        adapter.inspect_download("gm-staging/intent.bin", changed)
    with adapter.open_download("gm-staging/intent.bin", version) as opened:
        assert opened.read() == b"immutable staged payload"


@pytest.mark.parametrize(
    "returned_url",
    [
        "",
        "https://uploads.s3.us-east-1.amazonaws.com/files/report.txt?invalid",
    ],
)
def test_s3_public_download_rejects_malformed_storage_urls(
    monkeypatch: pytest.MonkeyPatch,
    returned_url: str,
) -> None:
    client = FakeS3Client()
    version = _stage(client)
    storage = FakeS3Storage(client, public=True)
    storage.upload_staging_prefix_private = True

    def malformed_url(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return returned_url

    monkeypatch.setattr(storage, "url", malformed_url)
    adapter = S3UploadAdapter(storage)

    expected = (
        UploadStorageError if returned_url == "" else PublicUploadUrlUnsupportedError
    )
    with pytest.raises(expected):
        adapter.public_download_url("files/report.txt", version=version)


def test_s3_signing_preserves_explicit_upload_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client()
    explicit = UploadStorageChangedError()

    def raise_explicit(*args: Any, **kwargs: Any) -> str:
        raise explicit

    monkeypatch.setattr(client, "generate_presigned_url", raise_explicit)
    adapter = S3UploadAdapter(FakeS3Storage(client))

    with pytest.raises(UploadStorageChangedError) as captured:
        adapter.private_download_url("files/report.txt", expires_in=60)
    assert captured.value is explicit
