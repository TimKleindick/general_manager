"""Optional, immutable-version-aware S3 upload adapter."""

from __future__ import annotations

import base64
from collections.abc import Mapping
import importlib.util
import re
from typing import IO, ClassVar, Protocol, TypeVar, cast
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
    UploadStorageError,
    UploadTransferConflictError,
)
from general_manager.uploads.types import ObjectVersion, UploadTransport


_ExceptionT = TypeVar("_ExceptionT", bound=Exception)


def _exception(
    exception_type: type[_ExceptionT],
    message: str,
) -> _ExceptionT:
    return exception_type(message)


class _S3ClientProtocol(Protocol):
    """Small runtime shape used to keep boto3 an optional dependency."""

    def get_bucket_versioning(self, **kwargs: object) -> Mapping[str, object]: ...

    def generate_presigned_post(self, **kwargs: object) -> Mapping[str, object]: ...

    def generate_presigned_url(self, operation: str, **kwargs: object) -> str: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def copy_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> object: ...


class S3UploadAdapter:
    """Direct adapter requiring S3 bucket versioning and exact source versions."""

    adapter_id: ClassVar[str] = "s3-versioned"
    adapter_version: ClassVar[int] = 1

    def __init__(self, storage: Storage) -> None:
        client = _client_for_storage(storage)
        bucket = getattr(storage, "bucket_name", None)
        if client is None or not isinstance(bucket, str) or not bucket:
            raise _exception(
                UploadBackendUnsupportedError,
                "The storage does not expose a version-capable S3 client.",
            )
        self.storage = storage
        self._client = client
        self._bucket = bucket

    @property
    def supports_public_urls(self) -> bool:
        return bool(
            getattr(self.storage, "public", False) is True
            or getattr(self.storage, "default_acl", None) == "public-read"
            or getattr(self.storage, "querystring_auth", True) is False
        )

    @classmethod
    def supports_direct(cls, storage: Storage) -> bool:
        if not _looks_like_s3_storage(storage):
            return False
        if getattr(storage, "versioning_enabled", True) is False:
            return False
        bucket = getattr(storage, "bucket_name", None)
        client = _client_for_storage(storage)
        if not isinstance(bucket, str) or not bucket or client is None:
            return False
        if not _supports_conditional_copy(client):
            return False
        try:
            response = client.get_bucket_versioning(Bucket=bucket)
        except Exception:  # noqa: BLE001 - capability detection must fail closed
            return False
        return response.get("Status") == "Enabled"

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
        response = self._client.generate_presigned_post(
            Bucket=self._bucket,
            Key=stage_key,
            Fields=fields,
            Conditions=[
                {"key": stage_key},
                {"Content-Type": content_type},
                {"x-amz-checksum-sha256": checksum_base64},
                ["content-length-range", size, size],
            ],
            ExpiresIn=expires_in,
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
        response = self._client.head_object(
            Bucket=self._bucket,
            Key=stage_key,
            ChecksumMode="ENABLED",
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
            )
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
        response = self._client.get_object(
            Bucket=self._bucket,
            Key=stage_key,
            VersionId=version.version_id,
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
        parameters: dict[str, object] = {"Bucket": self._bucket, "Key": stage_key}
        if version is not None and version.version_id:
            parameters["VersionId"] = version.version_id
        self._client.delete_object(**parameters)

    def private_download_url(self, key: str, *, expires_in: int) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def public_url(self, key: str) -> str:
        if not self.supports_public_urls:
            raise _exception(
                PublicUploadUrlUnsupportedError,
                "This S3 storage was not explicitly configured as public.",
            )
        return self.storage.url(key)

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
        explicit = client.supports_conditional_copy  # type: ignore[attr-defined]
    except AttributeError:
        pass
    else:
        if isinstance(explicit, bool):
            return explicit
    try:
        operation = client.meta.service_model.operation_model(  # type: ignore[attr-defined]
            "CopyObject"
        )
        members = operation.input_shape.members
    except (AttributeError, KeyError, RuntimeError, TypeError):
        return False
    return isinstance(members, Mapping) and "IfNoneMatch" in members


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
