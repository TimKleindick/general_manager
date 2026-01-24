"""Search backend protocol and shared result models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class SearchDocument:
    """Normalized document payload for search backends."""

    id: str
    type: str
    identification: dict[str, Any]
    index: str
    data: Mapping[str, Any]
    field_boosts: Mapping[str, float]
    index_boost: float | None = None


@dataclass(frozen=True)
class SearchHit:
    """Search hit metadata returned by backends."""

    id: str
    type: str
    identification: dict[str, Any]
    score: float | None = None
    index: str | None = None
    data: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class SearchResult:
    """Container for search responses."""

    hits: Sequence[SearchHit]
    total: int
    took_ms: int | None = None
    raw: Any | None = None


class SearchBackend(Protocol):
    """Protocol for search backend adapters."""

    def ensure_index(self, index_name: str, settings: Mapping[str, Any]) -> None:
        """Ensure an index exists and apply the provided settings."""

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        """Insert or update search documents for the given index."""

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
        """Delete documents by ID for the given index."""

    def search(
        self,
        index_name: str,
        query: str,
        *,
        filters: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
        filter_expression: str | None = None,
        sort_by: str | None = None,
        sort_desc: bool = False,
        limit: int = 10,
        offset: int = 0,
        types: Sequence[str] | None = None,
    ) -> SearchResult:
        """Search for documents matching the query and filters."""


class SearchBackendError(RuntimeError):
    """Raised when a backend operation fails."""


class SearchBackendNotImplementedError(SearchBackendError):
    """Raised when a backend implementation is not available."""

    def __init__(self, backend_name: str) -> None:
        super().__init__(f"{backend_name} backend is not implemented yet.")


class SearchBackendNotConfiguredError(RuntimeError):
    """Raised when a configured backend cannot be resolved."""

    def __init__(self) -> None:
        super().__init__("No search backend configured.")


class SearchBackendClientMissingError(SearchBackendError):
    """Raised when a backend client dependency is missing."""

    def __init__(self, backend_name: str) -> None:
        super().__init__(
            f"{backend_name} client is not installed. Install the required package."
        )
