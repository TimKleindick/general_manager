"""Stable exceptions raised by the file upload workflow."""

from __future__ import annotations

from typing import ClassVar


class UploadError(Exception):
    """Base class for expected upload failures with stable client codes."""

    code: ClassVar[str] = "UPLOAD_ERROR"
    default_message: ClassVar[str] = "The file upload could not be completed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class UploadExpiredError(UploadError):
    code = "UPLOAD_EXPIRED"


class UploadTokenInvalidError(UploadError):
    code = "UPLOAD_TOKEN_INVALID"


class UploadIncompleteError(UploadError):
    code = "UPLOAD_INCOMPLETE"


class UploadAlreadyConsumedError(UploadError):
    code = "UPLOAD_ALREADY_CONSUMED"


class UploadTransferConflictError(UploadError):
    code = "UPLOAD_TRANSFER_CONFLICT"


class UploadSupersededError(UploadError):
    code = "UPLOAD_SUPERSEDED"


class UploadBindingMismatchError(UploadError):
    code = "UPLOAD_BINDING_MISMATCH"


class UploadSizeMismatchError(UploadError):
    code = "UPLOAD_SIZE_MISMATCH"


class UploadChecksumMismatchError(UploadError):
    code = "UPLOAD_CHECKSUM_MISMATCH"


class InvalidFileTypeError(UploadError):
    code = "INVALID_FILE_TYPE"


class InvalidImageError(UploadError):
    code = "INVALID_IMAGE"


class UploadBackendUnsupportedError(UploadError):
    code = "UPLOAD_BACKEND_UNSUPPORTED"


class UploadStorageChangedError(UploadError):
    code = "UPLOAD_STORAGE_CHANGED"


class UploadFinalizationFailedError(UploadError):
    code = "UPLOAD_FINALIZATION_FAILED"


class UploadStorageError(UploadError):
    code = "UPLOAD_STORAGE_ERROR"
