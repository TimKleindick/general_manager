from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from general_manager.uploads.errors import (
    InvalidFileTypeError,
    InvalidImageError,
    UploadAlreadyConsumedError,
    UploadBackendUnsupportedError,
    UploadBindingMismatchError,
    UploadChecksumMismatchError,
    UploadError,
    UploadExpiredError,
    UploadFinalizationFailedError,
    UploadIncompleteError,
    UploadSizeMismatchError,
    UploadStorageChangedError,
    UploadStorageError,
    UploadSupersededError,
    UploadTokenInvalidError,
    UploadTransferConflictError,
)
from general_manager.uploads.types import (
    ChecksumAlgorithm,
    ObjectVersion,
    StoredFileStatus,
    UploadCandidate,
    UploadIntentState,
    UploadOperation,
    UploadTransport,
)


def test_upload_enums_have_stable_string_values() -> None:
    assert {member.name: member.value for member in UploadOperation} == {
        "CREATE": "create",
        "UPDATE": "update",
    }
    assert {member.name: member.value for member in UploadTransport} == {
        "DIRECT": "direct",
        "PROXY": "proxy",
    }
    assert {member.name: member.value for member in UploadIntentState} == {
        "PENDING": "pending",
        "TRANSFERRING": "transferring",
        "UPLOADED": "uploaded",
        "FINALIZING": "finalizing",
        "CONSUMED": "consumed",
        "SUPERSEDED": "superseded",
        "REJECTED": "rejected",
        "EXPIRED": "expired",
    }
    assert {member.name: member.value for member in StoredFileStatus} == {
        "AVAILABLE": "available",
        "PROCESSING": "processing",
        "FAILED": "failed",
    }
    assert ChecksumAlgorithm.SHA256.value == "sha256"


def test_upload_candidate_repr_redacts_checksum_and_sensitive_metadata() -> None:
    sensitive_checksum = "secret-token-digest-and-credentials"
    candidate = UploadCandidate(
        intent_id=UUID("a8aaae1b-2c19-414e-8621-f3c31a7dc9a2"),
        filename="avatar.png",
        size=123,
        content_type="image/png",
        checksum_sha256=sensitive_checksum,
    )

    representation = repr(candidate)

    assert representation == (
        "UploadCandidate(intent_id="
        "UUID('a8aaae1b-2c19-414e-8621-f3c31a7dc9a2'), "
        "filename='avatar.png')"
    )
    assert sensitive_checksum not in representation


def test_object_version_is_frozen_and_accepts_checksum_identity() -> None:
    version = ObjectVersion(
        version_id=None,
        etag=None,
        checksum_sha256="a" * 64,
        size=123,
        content_type="image/png",
    )

    assert version.checksum_sha256 == "a" * 64
    with pytest.raises(FrozenInstanceError):
        version.size = 0  # type: ignore[misc]


def test_object_version_repr_redacts_immutable_storage_identity() -> None:
    secrets = ("secret-version-id", "secret-etag", "secret-checksum")
    version = ObjectVersion(
        version_id=secrets[0],
        etag=secrets[1],
        checksum_sha256=secrets[2],
        size=123,
        content_type="image/png",
    )

    representation = repr(version)

    assert representation == "ObjectVersion(size=123, content_type='image/png')"
    assert all(secret not in representation for secret in secrets)


@pytest.mark.parametrize(
    ("version_id", "checksum_sha256", "size"),
    [(None, "", 1), (None, "a" * 64, -1)],
)
def test_object_version_rejects_invalid_identity_or_size(
    version_id: str | None,
    checksum_sha256: str,
    size: int,
) -> None:
    with pytest.raises(ValueError):
        ObjectVersion(
            version_id=version_id,
            etag=None,
            checksum_sha256=checksum_sha256,
            size=size,
        )


@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        (UploadExpiredError, "UPLOAD_EXPIRED"),
        (UploadTokenInvalidError, "UPLOAD_TOKEN_INVALID"),
        (UploadIncompleteError, "UPLOAD_INCOMPLETE"),
        (UploadAlreadyConsumedError, "UPLOAD_ALREADY_CONSUMED"),
        (UploadTransferConflictError, "UPLOAD_TRANSFER_CONFLICT"),
        (UploadSupersededError, "UPLOAD_SUPERSEDED"),
        (UploadBindingMismatchError, "UPLOAD_BINDING_MISMATCH"),
        (UploadSizeMismatchError, "UPLOAD_SIZE_MISMATCH"),
        (UploadChecksumMismatchError, "UPLOAD_CHECKSUM_MISMATCH"),
        (InvalidFileTypeError, "INVALID_FILE_TYPE"),
        (InvalidImageError, "INVALID_IMAGE"),
        (UploadBackendUnsupportedError, "UPLOAD_BACKEND_UNSUPPORTED"),
        (UploadStorageChangedError, "UPLOAD_STORAGE_CHANGED"),
        (UploadFinalizationFailedError, "UPLOAD_FINALIZATION_FAILED"),
        (UploadStorageError, "UPLOAD_STORAGE_ERROR"),
    ],
)
def test_upload_errors_expose_stable_codes(
    error_type: type[UploadError],
    code: str,
) -> None:
    error = error_type()

    assert isinstance(error, UploadError)
    assert error.code == code
