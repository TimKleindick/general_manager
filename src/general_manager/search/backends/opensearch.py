"""OpenSearch/Elasticsearch backend stub."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from general_manager.search.backend import (
    SearchBackendNotImplementedError,
    SearchDocument,
    SearchResult,
)


class OpenSearchBackend:
    """OpenSearch/Elasticsearch backend placeholder.

    The adapter is publicly importable for configuration compatibility, but it
    is not implemented. Construction and every backend operation raise
    `SearchBackendNotImplementedError`.
    """

    def __init__(self, *_: object, **__: object) -> None:
        """
        Initialize the OpenSearch backend stub which always fails construction.

        Raises:
            SearchBackendNotImplementedError: Always raised with message "OpenSearch/Elasticsearch".
        """
        raise SearchBackendNotImplementedError("OpenSearch/Elasticsearch")

    def ensure_index(self, index_name: str, settings: Mapping[str, object]) -> None:
        """
        Ensure the named index exists with the provided settings.

        Parameters:
            index_name (str): The name of the index to create or verify.
            settings: Index configuration, such as mappings, analyzers, and
                other OpenSearch/Elasticsearch settings. The value is accepted
                for protocol compatibility but never inspected.

        Raises:
            SearchBackendNotImplementedError: Always raised because this backend
                is not implemented.
        """
        raise SearchBackendNotImplementedError("OpenSearch")

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        """
        Placeholder to insert or update documents in the specified index.

        Parameters:
            index_name (str): Name of the index where documents would be upserted.
            documents (Sequence[SearchDocument]): Documents to insert or update.

        Raises:
            SearchBackendNotImplementedError: Always raised because the OpenSearch backend is not implemented.
        """
        raise SearchBackendNotImplementedError("OpenSearch")

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
        """
        Delete documents by ID from the specified index.

        Parameters:
            index_name (str): Name of the index containing the documents.
            ids (Sequence[str]): Sequence of document IDs to delete.

        Raises:
            SearchBackendNotImplementedError: Raised unconditionally because the OpenSearch backend is not implemented.
        """
        raise SearchBackendNotImplementedError("OpenSearch")

    def list_document_ids(
        self,
        index_name: str,
        *,
        types: Sequence[str] | None = None,
    ) -> set[str]:
        """
        List stored document IDs in the specified index.

        Raises:
            SearchBackendNotImplementedError: Always raised because the OpenSearch backend is not implemented.
        """
        raise SearchBackendNotImplementedError("OpenSearch")

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
        Execute a search query against the specified index using optional filters, sorting, pagination, and type constraints.

        Parameters:
            index_name (str): Name of the index to search.
            query (str): Full-text query string to match documents.
            filters: Optional structured filters. Accepted for protocol
                compatibility but never inspected.
            filter_expression (str | None): Optional boolean-style filter expression string to further constrain results.
            sort_by (str | None): Optional field name to sort results by.
            sort_desc (bool): If true, sort results in descending order; otherwise sort ascending.
            limit (int): Maximum number of results to return.
            offset (int): Number of results to skip (for pagination).
            types (Sequence[str] | None): Optional sequence of document types to restrict the search to.

        Returns:
            SearchResult: The search result containing matching documents and metadata.

        Raises:
            SearchBackendNotImplementedError: Always raised by this backend stub indicating OpenSearch is not implemented.
        """
        raise SearchBackendNotImplementedError("OpenSearch")
