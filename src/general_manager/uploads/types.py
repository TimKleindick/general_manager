"""Immutable values shared by file upload components."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID


class UploadOperation(StrEnum):
    """Domain mutation that will consume an upload."""

    CREATE = "create"
    UPDATE = "update"


class UploadTransport(StrEnum):
    """Mechanism used to transfer staged bytes."""

    DIRECT = "direct"
    PROXY = "proxy"


class UploadIntentState(StrEnum):
    """Durable state of an upload intent."""

    PENDING = "pending"
    TRANSFERRING = "transferring"
    UPLOADED = "uploaded"
    FINALIZING = "finalizing"
    CONSUMED = "consumed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    EXPIRED = "expired"


class StoredFileStatus(StrEnum):
    """Client-visible availability of a stored file."""

    AVAILABLE = "available"
    PROCESSING = "processing"
    FAILED = "failed"


class ChecksumAlgorithm(StrEnum):
    """Supported content checksum algorithms."""

    SHA256 = "sha256"


class _InvalidObjectVersionError(ValueError):
    """Raised when immutable object metadata is incomplete or malformed."""

    @classmethod
    def invalid_size(cls) -> _InvalidObjectVersionError:
        return cls("size must be a non-negative integer.")

    @classmethod
    def missing_identity(cls) -> _InvalidObjectVersionError:
        return cls("version_id or checksum_sha256 is required.")


@dataclass(frozen=True, slots=True)
class UploadCandidate:
    """Permission-safe upload metadata passed through a domain mutation."""

    intent_id: UUID
    filename: str
    size: int
    content_type: str
    checksum_sha256: str

    def __repr__(self) -> str:
        """Return a representation that excludes checksums and credentials."""
        return (
            f"UploadCandidate(intent_id={self.intent_id!r}, filename={self.filename!r})"
        )


@dataclass(frozen=True, slots=True)
class ObjectVersion:
    """Immutable identity and verified metadata for a staged object."""

    version_id: str | None = field(repr=False)
    etag: str | None = field(repr=False)
    checksum_sha256: str = field(repr=False)
    size: int
    content_type: str | None = None

    def __post_init__(self) -> None:
        """Require a valid byte size and at least one immutable identity."""
        if (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or self.size < 0
        ):
            raise _InvalidObjectVersionError.invalid_size()
        if not self.version_id and not self.checksum_sha256:
            raise _InvalidObjectVersionError.missing_identity()
