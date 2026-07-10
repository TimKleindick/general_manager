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


class UploadObjectMissingError(UploadStorageError):
    """Stable adapter signal that an exact object is already absent.

    Custom adapters should raise this only when they can distinguish absence
    from an outage. Cleanup treats it as idempotent success; client boundaries
    always map it to a generic safe upload error.
    """

    default_message = "The exact upload object no longer exists."


class UploadAuthenticationError(UploadError):
    """Raised when beginning an upload has no durable authenticated owner."""

    code = "UNAUTHENTICATED"
    default_message = "Authentication is required to upload a file."


class UploadManagerInvalidError(UploadError):
    """Raised when the requested manager is not in the live GraphQL registry."""

    code = "UPLOAD_MANAGER_INVALID"
    default_message = "The requested upload destination is not available."


class UploadFieldInvalidError(UploadError):
    """Raised when the requested field is not an editable ORM file field."""

    code = "UPLOAD_FIELD_INVALID"
    default_message = "The requested upload destination is not available."


class UploadOperationInvalidError(UploadError):
    """Raised when create/update target inputs are inconsistent."""

    code = "UPLOAD_OPERATION_INVALID"
    default_message = "The requested upload operation is invalid."


class UploadTargetUnavailableError(UploadError):
    """Hide whether an update target is absent or unreadable by the actor."""

    code = "UPLOAD_TARGET_UNAVAILABLE"
    default_message = "The requested upload target is not available."


class InvalidUploadFilenameError(UploadError):
    """Raised when a client filename is not one safe basename."""

    code = "INVALID_UPLOAD_FILENAME"
    default_message = "The upload filename is invalid."


class InvalidUploadSizeError(UploadError):
    """Raised when a declared byte size is malformed or outside policy."""

    code = "INVALID_UPLOAD_SIZE"
    default_message = "The upload size is invalid."


class InvalidUploadChecksumError(UploadError):
    """Raised when a declared checksum is not one valid SHA-256 digest."""

    code = "INVALID_UPLOAD_CHECKSUM"
    default_message = "The upload checksum is invalid."


class UploadQuotaExceededError(UploadError):
    """Raised when the owner has no pending-intent capacity."""

    code = "UPLOAD_QUOTA_EXCEEDED"
    default_message = "The pending upload limit has been reached."


class UploadRateLimitExceededError(UploadError):
    """Raised by a configured begin-upload request-rate hook."""

    code = "UPLOAD_RATE_LIMITED"
    default_message = "Too many upload requests were made."


class UploadDatabaseMismatchError(UploadError):
    """Raised when manager and upload-intent writes cannot be atomic."""

    code = "UPLOAD_DATABASE_MISMATCH"
    default_message = "The requested upload destination is not available."


_FRAMEWORK_UPLOAD_ERROR_TYPES: frozenset[type[UploadError]] = frozenset(
    value
    for value in tuple(globals().values())
    if isinstance(value, type)
    and issubclass(value, UploadError)
    and value.__module__ == __name__
)


def stable_upload_error(error: UploadError) -> UploadError:
    """Return a fresh framework-owned public error for one caught failure."""

    error_type = type(error)
    if error_type not in _FRAMEWORK_UPLOAD_ERROR_TYPES:
        return UploadStorageError()
    return error_type()
