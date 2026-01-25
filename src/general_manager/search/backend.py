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
        """
        Ensure the named index exists and apply the given settings.

        Parameters:
            index_name (str): Name of the index to create or update.
            settings (Mapping[str, Any]): Configuration settings to apply to the index.
        """

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        """
        Upsert the provided search documents into the specified index.

        Parameters:
            index_name (str): Name of the index where documents will be stored.
            documents (Sequence[SearchDocument]): Documents to insert or update; each document's `id` is used to identify and replace existing entries when present.
        """

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
        """
        Delete documents from the specified index by their document IDs.

        Parameters:
            index_name (str): Name of the index to remove documents from.
            ids (Sequence[str]): Sequence of document IDs to delete.
        """

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
        """
        Search for documents in an index that match the given query and optional filters.

        Parameters:
            index_name (str): Name of the index to search.
            query (str): Query string used to match documents.
            filters (Mapping[str, Any] | Sequence[Mapping[str, Any]] | None): Optional structured filters applied to the query. May be a single filter mapping or a sequence of filter mappings to be combined.
            filter_expression (str | None): Optional boolean expression combining filter fields (backend-specific syntax).
            sort_by (str | None): Field name to sort results by.
            sort_desc (bool): If true, sort results in descending order.
            limit (int): Maximum number of hits to return.
            offset (int): Number of hits to skip before returning results (pagination).
            types (Sequence[str] | None): Optional list of document types to restrict the search to.

        Returns:
            SearchResult: Container with matching hits, the total number of matches, optional elapsed time in milliseconds, and any raw backend response.
        """


class SearchBackendError(RuntimeError):
    """Raised when a backend operation fails."""


class SearchBackendNotImplementedError(SearchBackendError):
    """Raised when a backend implementation is not available."""

    def __init__(self, backend_name: str) -> None:
        """
        Initialize the error indicating a specific search backend is not implemented.

        Parameters:
            backend_name (str): Name of the backend used to compose the exception message.
        """
        super().__init__(f"{backend_name} backend is not implemented yet.")


class SearchBackendNotConfiguredError(RuntimeError):
    """Raised when a configured backend cannot be resolved."""

    def __init__(self, message: str | None = None) -> None:
        """
        Initialize the error with a message indicating no search backend is configured.
        """
        super().__init__(message or "No search backend configured.")

    @classmethod
    def from_setting(cls, backend_setting: object) -> "SearchBackendNotConfiguredError":
        masked_setting = _mask_backend_setting(backend_setting)
        message = (
            f"Search backend could not be resolved from setting: {masked_setting!r}"
        )
        return cls(message)


def _mask_backend_setting(setting: object) -> object:
    secret_keys = {"password", "secret", "api_key", "apikey", "token", "auth"}
    if isinstance(setting, Mapping):
        masked: dict[object, object] = {}
        for key, value in setting.items():
            key_str = str(key).lower()
            if key_str in secret_keys:
                masked[key] = "<masked>"
            else:
                masked[key] = _mask_backend_setting(value)
        return masked
    if isinstance(setting, Sequence) and not isinstance(setting, (str, bytes)):
        if isinstance(setting, tuple):
            return tuple(_mask_backend_setting(item) for item in setting)
        return [_mask_backend_setting(item) for item in setting]
    if isinstance(setting, str):
        if "://" in setting:
            try:
                from urllib.parse import urlsplit

                parts = urlsplit(setting)
                host = parts.hostname or "<masked>"
                port = f":{parts.port}" if parts.port else ""
            except ValueError:
                return "<masked>"
            else:
                return f"{parts.scheme}://{host}{port}"
        if ":" in setting or "=" in setting:
            return "<masked>"
        return setting
    return setting


class SearchBackendClientMissingError(SearchBackendError):
    """Raised when a backend client dependency is missing."""

    def __init__(self, backend_name: str) -> None:
        """
        Initialize the exception indicating a backend client dependency is missing.

        Parameters:
            backend_name (str): Name of the missing backend client; used to construct the exception message advising installation of the required package.
        """
        super().__init__(
            f"{backend_name} client is not installed. Install the required package."
        )
