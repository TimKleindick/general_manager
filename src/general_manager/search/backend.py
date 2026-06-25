"""Search backend protocol and shared result models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SearchDocument:
    """Normalized document payload sent to search backends.

    `id` is the stable backend document identifier used by upsert and delete;
    uniqueness is scoped to one backend index unless a concrete adapter
    documents broader constraints. `type` is the manager type label.
    `identification` is the manager reconstruction payload. `index` records
    the logical index the document was built for; indexer-managed calls pass
    the same value as the `index_name` argument used for backend writes. The
    protocol does not require adapters to reject mismatches between
    `document.index` and an `index_name` argument. `data` contains
    searchable/stored fields. `field_boosts` and `index_boost` are hints for
    adapters that support weighted matching. The payload mappings, including
    `identification`, are not copied by the model; callers should treat them as
    immutable after handing them to a backend. Dataclass field annotations are
    the validation boundary; this model does not coerce or validate runtime
    values. `frozen=True` prevents attribute reassignment but does not make
    nested mappings immutable or guarantee practical hashability when fields
    contain unhashable values.
    """

    id: str
    type: str
    identification: dict[str, object]
    index: str
    data: Mapping[str, object]
    field_boosts: Mapping[str, float]
    index_boost: float | None = None


@dataclass(frozen=True)
class SearchHit:
    """Search hit metadata returned by backends after a query.

    `id`, `type`, and `identification` identify the matched manager document.
    `score`, `index`, and `data` are optional because not every backend returns
    a relevance score, index name, or stored data fields in every response.
    When `data` is present, ownership and copy semantics are adapter-defined;
    callers should treat returned mappings as read-only.
    """

    id: str
    type: str
    identification: dict[str, object]
    score: float | None = None
    index: str | None = None
    data: Mapping[str, object] | None = None


@dataclass(frozen=True)
class SearchResult:
    """Container for normalized search responses.

    `hits` is the current page after applying `limit` and `offset`. `total` is
    the total number of matches before pagination when the backend can report
    it; when a backend cannot report a pre-pagination count, its adapter
    defines the best-effort value. `raw` may carry the backend-native response
    object for debugging or advanced callers and is not copied or normalized by
    this model; callers should treat it as read-only.
    """

    hits: Sequence[SearchHit]
    total: int
    took_ms: int | None = None
    raw: object | None = None


@runtime_checkable
class SearchBackend(Protocol):
    """Protocol implemented by search backend adapters.

    The portable contract is intentionally narrow: method shapes, document
    identity by index/name/id, object-valued payloads, basic structured filter
    transport, type restrictions, one-field sorting, paginated results, and the
    broad error boundary below. Backend-specific behavior is part of the public
    adapter contract, not an omission from this protocol. That includes exact
    settings support and merge/replace behavior, duplicate IDs inside one batch,
    grouped-filter semantics, `filter_expression` precedence, non-DevSearch
    lookup/sort grammar, negative pagination values, batch atomicity,
    concurrency guarantees, runtime validation, and concrete exception classes.
    Adapters may validate inputs more strictly than these dataclasses do
    without violating the protocol. All protocol methods are synchronous.
    """

    def ensure_index(self, index_name: str, settings: Mapping[str, object]) -> None:
        """
        Ensure the named index exists and apply the given settings.

        Parameters:
            index_name: Name of the index to create or update.
            settings: Configuration settings to apply to the index. `None` is
                not part of the protocol; pass an empty mapping for no settings.
                Backends
                decide which keys are supported, whether settings replace or
                merge with previous settings, and whether unknown keys are
                ignored.

        Raises:
            SearchBackendError: Backend adapters may raise this for operational
                failures. Concrete adapters may also propagate client-library,
                validation, or configuration exceptions.
        """

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        """
        Upsert the provided search documents into the specified index.

        Parameters:
            index_name: Name of the index where documents will be stored.
            documents: Documents to insert or update; each document's `id` is
                used to identify and replace existing entries when present.
                Duplicate IDs in one call and partial-write behavior after a
                batch failure are adapter-defined. Backends may retain
                references to document payloads unless their adapter documents
                copy-on-write behavior.

        Raises:
            SearchBackendError: Backend adapters may raise this for operational
                failures. Concrete adapters may also propagate client-library,
                validation, or configuration exceptions.
        """

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
        """
        Delete documents from the specified index by their document IDs.

        Parameters:
            index_name: Name of the index to remove documents from.
            ids: Document IDs to delete. Missing IDs may be ignored by
                backends that support idempotent deletion. Duplicate IDs in
                one call and partial-delete behavior after a batch failure are
                adapter-defined.

        Raises:
            SearchBackendError: Backend adapters may raise this for operational
                failures. Concrete adapters may also propagate client-library,
                validation, or configuration exceptions.
        """

    def list_document_ids(
        self,
        index_name: str,
        *,
        types: Sequence[str] | None = None,
    ) -> set[str]:
        """
        Return all document IDs currently stored in an index.

        Parameters:
            index_name: Name of the index to inspect.
            types: Optional document type labels to include. `None` includes
                every type stored in the index.

        Returns:
            Backend document IDs in their original GeneralManager form, for
            example the same strings passed as `SearchDocument.id` such as
            `"Project:{'id': 1}"`.

        Raises:
            SearchBackendError: Backend adapters may raise this for operational
                failures. Concrete adapters may also propagate client-library,
                validation, or configuration exceptions.
        """

    def search(
        self,
        index_name: str,
        query: str,
        *,
        filters: Mapping[str, object] | Sequence[Mapping[str, object]] | None = None,
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
            index_name: Name of the index to search.
            query: Query string used to match documents.
            filters: Optional structured filters applied to the query. A single
                mapping is an AND group; mapping keys use backend-supported
                field names or lookup expressions such as `field__in`, where
                `__` is the conventional suffix delimiter. The portable field
                namespace is `SearchDocument.data`; `types` handles type labels
                separately. Nested-field syntax, if any, is adapter-specific. A
                sequence of mappings represents backend-specific grouped
                filtering, typically OR between groups.
            filter_expression: Optional backend-specific boolean expression.
                When provided, concrete backends define precedence relative to
                `filters` and `types`. The portable protocol intentionally
                makes no precedence promise beyond passing all supplied values
                to the adapter.
            sort_by: Field name to sort results by.
            sort_desc: If true, sort results in descending order.
            limit: Maximum number of hits to return. The protocol does not
                clamp or validate negative values; concrete backends define
                invalid-value behavior.
            offset: Number of hits to skip before returning results. Concrete
                backends define invalid-value behavior.
            types: Optional document type labels to restrict the search to.
                Unknown type labels and unknown indexes are handled by the
                concrete adapter.

        Returns:
            Container with matching hits, the total number of matches, optional
            elapsed time in milliseconds, and any raw backend response.

        Raises:
            SearchBackendError: Backend adapters may raise this for operational
                failures. Concrete adapters may also propagate client-library,
                validation, unsupported-feature, or configuration exceptions.
                The protocol intentionally does not define a closed exception
                taxonomy; unsupported `filter_expression` may be reported as a
                backend-specific exception such as `NotImplementedError`.
                Adapters should normalize operational failures only when doing
                so does not hide useful backend-native context.
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
        """Build an error message from a backend setting with secrets masked."""
        masked_setting = _mask_backend_setting(backend_setting)
        message = (
            f"Search backend could not be resolved from setting: {masked_setting!r}"
        )
        return cls(message)


def _mask_backend_setting(setting: object) -> object:
    """Return a backend setting representation with sensitive values removed."""
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
