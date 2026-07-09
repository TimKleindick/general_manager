"""Optional, immutable-version-aware S3 upload adapter."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import importlib.util
import re
from typing import IO, ClassVar, Protocol, TypeVar, cast
from urllib.parse import urlsplit
from uuid import UUID

from django.core.files.storage import Storage

from general_manager.uploads.adapters import (
    PublicUploadUrlUnsupportedError,
    UploadInstructions,
    build_storage_fingerprint,
)
from general_manager.uploads.errors import (
    UploadBackendUnsupportedError,
    UploadStorageChangedError,
    UploadError,
    UploadStorageError,
    UploadTransferConflictError,
)
from general_manager.uploads.types import ObjectVersion, UploadTransport


_ExceptionT = TypeVar("_ExceptionT", bound=Exception)
_ResultT = TypeVar("_ResultT")


def _exception(
    exception_type: type[_ExceptionT],
    message: str,
) -> _ExceptionT:
    return exception_type(message)


def _sdk_call(operation: Callable[[], _ResultT], message: str) -> _ResultT:
    try:
        return operation()
    except UploadError:
        raise
    except Exception as exc:
        raise _exception(UploadStorageError, message) from exc


class _S3ClientProtocol(Protocol):
    """Small runtime shape used to keep boto3 an optional dependency."""

    def get_bucket_versioning(self, **kwargs: object) -> Mapping[str, object]: ...

    def generate_presigned_post(self, **kwargs: object) -> Mapping[str, object]: ...

    def generate_presigned_url(self, operation: str, **kwargs: object) -> str: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def copy_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class _S3ObjectOptions:
    staging_post_fields: Mapping[str, str]
    final_copy_arguments: Mapping[str, object]


class S3UploadAdapter:
    """Direct adapter requiring S3 bucket versioning and exact source versions."""

    adapter_id: ClassVar[str] = "s3-versioned"
    adapter_version: ClassVar[int] = 1

    def __init__(self, storage: Storage) -> None:
        client, bucket, object_options = _validate_direct_support(storage)
        self.storage = storage
        self._client = client
        self._bucket = bucket
        self._object_options = object_options

    @property
    def supports_public_urls(self) -> bool:
        return _storage_is_public(self.storage, self._object_options)

    @classmethod
    def supports_direct(cls, storage: Storage) -> bool:
        try:
            _validate_direct_support(storage)
        except UploadBackendUnsupportedError:
            return False
        return True

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
        del upload_url, headers
        checksum_base64 = _hex_checksum_to_base64(checksum_sha256)
        fields = {
            "key": stage_key,
            "Content-Type": content_type,
            "x-amz-checksum-sha256": checksum_base64,
        }
        fields.update(self._object_options.staging_post_fields)
        conditions: list[object] = [
            {"key": stage_key},
            {"Content-Type": content_type},
            {"x-amz-checksum-sha256": checksum_base64},
            ["content-length-range", size, size],
        ]
        conditions.extend(
            {key: value}
            for key, value in self._object_options.staging_post_fields.items()
        )
        response = _sdk_call(
            lambda: self._client.generate_presigned_post(
                Bucket=self._bucket,
                Key=stage_key,
                Fields=fields,
                Conditions=conditions,
                ExpiresIn=expires_in,
            ),
            "S3 could not create upload instructions.",
        )
        url = response.get("url")
        response_fields = response.get("fields")
        if not isinstance(url, str) or not isinstance(response_fields, Mapping):
            raise _exception(
                UploadStorageError,
                "S3 returned malformed upload instructions.",
            )
        return UploadInstructions(
            transport=UploadTransport.DIRECT,
            method="POST",
            url=url,
            fields={str(key): str(value) for key, value in response_fields.items()},
        )

    def inspect_staged(self, stage_key: str) -> ObjectVersion:
        response = _sdk_call(
            lambda: self._client.head_object(
                Bucket=self._bucket,
                Key=stage_key,
                ChecksumMode="ENABLED",
            ),
            "S3 could not inspect the staged object.",
        )
        return _object_version(response)

    def materialize(
        self,
        stage_key: str,
        version: ObjectVersion,
        final_key: str,
        *,
        intent_id: UUID,
    ) -> str:
        if not version.version_id or not version.etag:
            raise _exception(
                UploadBackendUnsupportedError,
                "S3 materialization requires VersionId and ETag.",
            )
        identity = {
            "gm-intent-id": str(intent_id),
            "gm-checksum-sha256": version.checksum_sha256,
        }
        existing = self._head_optional(final_key)
        if existing is not None:
            if _matches_materialization(existing, version, identity):
                return final_key
            raise _exception(
                UploadTransferConflictError,
                "The reserved final S3 key is already occupied.",
            )
        try:
            self._client.copy_object(
                Bucket=self._bucket,
                Key=final_key,
                CopySource={
                    "Bucket": self._bucket,
                    "Key": stage_key,
                    "VersionId": version.version_id,
                },
                CopySourceIfMatch=version.etag,
                IfNoneMatch="*",
                Metadata=identity,
                MetadataDirective="REPLACE",
                ContentType=version.content_type or "application/octet-stream",
                ChecksumAlgorithm="SHA256",
                **self._object_options.final_copy_arguments,
            )
        except UploadError:
            raise
        except Exception as exc:
            raced = self._head_optional(final_key)
            if raced is not None and _matches_materialization(raced, version, identity):
                return final_key
            if _is_precondition_error(exc) or raced is not None:
                raise _exception(
                    UploadTransferConflictError,
                    "The reserved final S3 key is already occupied.",
                ) from exc
            raise _exception(
                UploadStorageError,
                "S3 could not materialize the staged object.",
            ) from exc
        copied = self._head_optional(final_key)
        if copied is None or not _matches_materialization(copied, version, identity):
            raise _exception(
                UploadStorageChangedError,
                "S3 did not preserve the expected materialization identity.",
            )
        return final_key

    def open_stage(self, stage_key: str, version: ObjectVersion) -> IO[bytes]:
        if not version.version_id:
            raise _exception(
                UploadBackendUnsupportedError,
                "S3 staged reads require an immutable VersionId.",
            )
        response = _sdk_call(
            lambda: self._client.get_object(
                Bucket=self._bucket,
                Key=stage_key,
                VersionId=version.version_id,
            ),
            "S3 could not open the staged object.",
        )
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise _exception(
                UploadStorageError,
                "S3 returned a staged object without a body.",
            )
        return cast(IO[bytes], body)

    def delete_stage(
        self,
        stage_key: str,
        version: ObjectVersion | None = None,
    ) -> None:
        if version is None or not version.version_id:
            raise _exception(
                UploadBackendUnsupportedError,
                "S3 staged deletion requires an immutable VersionId.",
            )
        parameters: dict[str, object] = {"Bucket": self._bucket, "Key": stage_key}
        parameters["VersionId"] = version.version_id
        _sdk_call(
            lambda: self._client.delete_object(**parameters),
            "S3 could not delete the staged object.",
        )

    def private_download_url(self, key: str, *, expires_in: int) -> str:
        return _sdk_call(
            lambda: self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            ),
            "S3 could not create a private download URL.",
        )

    def public_url(self, key: str) -> str:
        if not self.supports_public_urls:
            raise _exception(
                PublicUploadUrlUnsupportedError,
                "This S3 storage was not explicitly configured as public.",
            )
        return _sdk_call(
            lambda: self.storage.url(key),
            "S3 storage could not create a public URL.",
        )

    def storage_fingerprint(self) -> str:
        return build_storage_fingerprint(
            self.storage,
            identity={"bucket": self._bucket},
        )

    def _head_optional(self, key: str) -> Mapping[str, object] | None:
        try:
            return self._client.head_object(
                Bucket=self._bucket,
                Key=key,
                ChecksumMode="ENABLED",
            )
        except Exception as exc:
            if _is_missing_error(exc):
                return None
            raise _exception(
                UploadStorageError,
                "S3 could not inspect the destination key.",
            ) from exc

    def __repr__(self) -> str:
        return (
            f"S3UploadAdapter(adapter_id={self.adapter_id!r}, "
            f"adapter_version={self.adapter_version!r}, "
            f"storage_fingerprint={self.storage_fingerprint()!r})"
        )


def _looks_like_s3_storage(storage: Storage) -> bool:
    storage_type = type(storage)
    return bool(
        getattr(storage, "_gm_s3_storage", False) is True
        or storage_type.__module__.startswith("storages.backends.s3")
        or storage_type.__name__ in {"S3Storage", "S3Boto3Storage"}
    )


def _storage_is_public(storage: Storage, object_options: _S3ObjectOptions) -> bool:
    return bool(
        getattr(storage, "public", False) is True
        or getattr(storage, "querystring_auth", True) is False
        or object_options.final_copy_arguments.get("ACL")
        in {"public-read", "public-read-write"}
    )


def _validate_direct_support(
    storage: Storage,
) -> tuple[_S3ClientProtocol, str, _S3ObjectOptions]:
    if not _looks_like_s3_storage(storage):
        raise _exception(
            UploadBackendUnsupportedError,
            "The storage backend is not a recognized S3 storage.",
        )
    object_options = _storage_object_options(storage)
    if (
        _storage_is_public(storage, object_options)
        and getattr(storage, "upload_staging_prefix_private", False) is not True
    ):
        raise _exception(
            UploadBackendUnsupportedError,
            "Public S3 storage requires an explicitly private staging prefix.",
        )
    if not _is_aws_s3_endpoint(storage):
        try:
            custom_conditional_copy = storage.supports_conditional_copy  # type: ignore[attr-defined]
        except AttributeError:
            custom_conditional_copy = False
        if custom_conditional_copy is not True:
            raise _exception(
                UploadBackendUnsupportedError,
                "Custom S3 endpoints require explicit conditional-copy support.",
            )
    if getattr(storage, "versioning_enabled", True) is False:
        raise _exception(
            UploadBackendUnsupportedError,
            "S3 bucket versioning is not enabled.",
        )
    bucket = getattr(storage, "bucket_name", None)
    client = _client_for_storage(storage)
    if not isinstance(bucket, str) or not bucket or client is None:
        raise _exception(
            UploadBackendUnsupportedError,
            "The storage does not expose a version-capable S3 client.",
        )
    if not _supports_conditional_copy(client):
        raise _exception(
            UploadBackendUnsupportedError,
            "The S3 client cannot conditionally create copied objects.",
        )
    try:
        response = client.get_bucket_versioning(Bucket=bucket)
    except Exception as exc:
        raise _exception(
            UploadBackendUnsupportedError,
            "S3 bucket versioning capability could not be verified.",
        ) from exc
    if response.get("Status") != "Enabled":
        raise _exception(
            UploadBackendUnsupportedError,
            "S3 bucket versioning is not enabled.",
        )
    return client, bucket, object_options


def _storage_object_options(storage: Storage) -> _S3ObjectOptions:
    configured = getattr(storage, "object_parameters", {})
    if configured is None:
        configured = {}
    if not isinstance(configured, Mapping):
        raise _exception(
            UploadBackendUnsupportedError,
            "S3 object_parameters must be a mapping.",
        )
    allowed = {
        "ACL",
        "ServerSideEncryption",
        "SSEKMSKeyId",
        "BucketKeyEnabled",
        "StorageClass",
    }
    unknown = set(configured) - allowed
    if unknown:
        raise _exception(
            UploadBackendUnsupportedError,
            "S3 object_parameters contain unsupported options.",
        )
    values = dict(configured)
    default_acl = getattr(storage, "default_acl", None)
    if "ACL" not in values and default_acl is not None:
        values["ACL"] = default_acl

    encryption = values.get("ServerSideEncryption")
    requires_kms = "SSEKMSKeyId" in values or values.get("BucketKeyEnabled") is True
    if requires_kms and encryption != "aws:kms":
        raise _exception(
            UploadBackendUnsupportedError,
            "S3 KMS key and bucket-key options require aws:kms encryption.",
        )

    post_names = {
        "ACL": "acl",
        "ServerSideEncryption": "x-amz-server-side-encryption",
        "SSEKMSKeyId": "x-amz-server-side-encryption-aws-kms-key-id",
        "BucketKeyEnabled": "x-amz-server-side-encryption-bucket-key-enabled",
        "StorageClass": "x-amz-storage-class",
    }
    staging_post_fields: dict[str, str] = {}
    final_copy_arguments: dict[str, object] = {}
    for name, value in values.items():
        if name == "BucketKeyEnabled":
            if not isinstance(value, bool):
                raise _exception(
                    UploadBackendUnsupportedError,
                    "S3 BucketKeyEnabled must be a boolean.",
                )
            staging_post_fields[post_names[name]] = str(value).lower()
            final_copy_arguments[name] = value
            continue
        if not isinstance(value, str) or not value:
            raise _exception(
                UploadBackendUnsupportedError,
                "S3 object parameter values must be non-empty strings.",
            )
        if name == "ACL" and value == "bucket-owner-full-control":
            staging_post_fields[post_names[name]] = value
        elif name not in {"ACL", "StorageClass"}:
            staging_post_fields[post_names[name]] = value
        final_copy_arguments[name] = value
    return _S3ObjectOptions(
        staging_post_fields=staging_post_fields,
        final_copy_arguments=final_copy_arguments,
    )


def _client_for_storage(storage: Storage) -> _S3ClientProtocol | None:
    injected = getattr(storage, "s3_client", None)
    if injected is not None and getattr(storage, "_gm_s3_storage", False) is True:
        return cast(_S3ClientProtocol, injected)
    if not _optional_dependencies_available():
        return None
    try:
        connection = storage.connection  # type: ignore[attr-defined]
        return cast(_S3ClientProtocol, connection.meta.client)
    except (AttributeError, RuntimeError, TypeError):
        return None


def _optional_dependencies_available() -> bool:
    return (
        importlib.util.find_spec("boto3") is not None
        and importlib.util.find_spec("storages") is not None
    )


def _supports_conditional_copy(client: _S3ClientProtocol) -> bool:
    """Require the SDK/backend to expose destination ``IfNoneMatch``."""
    try:
        operation = client.meta.service_model.operation_model(  # type: ignore[attr-defined]
            "CopyObject"
        )
        members = operation.input_shape.members
    except (AttributeError, KeyError, RuntimeError, TypeError):
        return False
    return isinstance(members, Mapping) and "IfNoneMatch" in members


def _is_aws_s3_endpoint(storage: Storage) -> bool:
    endpoint = getattr(storage, "endpoint_url", None)
    if endpoint is None or endpoint == "":
        return True
    if not isinstance(endpoint, str):
        return False
    try:
        hostname = urlsplit(endpoint).hostname
    except ValueError:
        return False
    if hostname is None:
        return False
    labels = hostname.lower().split(".")
    is_aws_domain = hostname.lower().endswith((".amazonaws.com", ".amazonaws.com.cn"))
    return is_aws_domain and any(
        label == "s3" or label.startswith("s3-") for label in labels
    )


def _object_version(response: Mapping[str, object]) -> ObjectVersion:
    version_id = response.get("VersionId")
    etag = response.get("ETag")
    checksum = response.get("ChecksumSHA256")
    size = response.get("ContentLength")
    content_type = response.get("ContentType")
    if (
        not isinstance(version_id, str)
        or not version_id
        or not isinstance(etag, str)
        or not etag
        or not isinstance(checksum, str)
        or not isinstance(size, int)
    ):
        raise _exception(
            UploadBackendUnsupportedError,
            "S3 staged objects require VersionId, ETag, SHA-256, and size.",
        )
    return ObjectVersion(
        version_id=version_id,
        etag=etag,
        checksum_sha256=_base64_checksum_to_hex(checksum),
        size=size,
        content_type=content_type if isinstance(content_type, str) else None,
    )


def _matches_materialization(
    response: Mapping[str, object],
    version: ObjectVersion,
    identity: Mapping[str, str],
) -> bool:
    metadata = response.get("Metadata")
    size = response.get("ContentLength")
    checksum = response.get("ChecksumSHA256")
    if not isinstance(metadata, Mapping) or size != version.size:
        return False
    if any(metadata.get(key) != value for key, value in identity.items()):
        return False
    return isinstance(checksum, str) and (
        _base64_checksum_to_hex(checksum) == version.checksum_sha256
    )


def _hex_checksum_to_base64(checksum: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise _exception(
            UploadChecksumValueError,
            "SHA-256 must contain 64 hexadecimal digits.",
        )
    return base64.b64encode(bytes.fromhex(checksum)).decode("ascii")


def _base64_checksum_to_hex(checksum: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        return checksum.lower()
    try:
        decoded = base64.b64decode(checksum, validate=True)
    except (ValueError, TypeError) as exc:
        raise _exception(
            UploadBackendUnsupportedError,
            "S3 returned an invalid SHA-256.",
        ) from exc
    if len(decoded) != 32:
        raise _exception(
            UploadBackendUnsupportedError,
            "S3 returned an invalid SHA-256.",
        )
    return decoded.hex()


class UploadChecksumValueError(ValueError):
    """Raised before signing malformed caller-provided checksums."""


def _error_code(exc: Exception) -> str | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, Mapping):
        return None
    error = response.get("Error")
    if not isinstance(error, Mapping):
        return None
    code = error.get("Code")
    return str(code) if code is not None else None


def _is_missing_error(exc: Exception) -> bool:
    return _error_code(exc) in {"404", "NoSuchKey", "NotFound"}


def _is_precondition_error(exc: Exception) -> bool:
    return _error_code(exc) in {
        "409",
        "412",
        "ConditionalRequestConflict",
        "PreconditionFailed",
    }
