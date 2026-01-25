"""OpenSearch/Elasticsearch backend stub."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from general_manager.search.backend import (
    SearchBackendNotImplementedError,
    SearchDocument,
    SearchResult,
)


class OpenSearchBackend:
    """OpenSearch/Elasticsearch implementation stub."""

    def __init__(self, *_: Any, **__: Any) -> None:
        """
        Initialize the OpenSearch backend stub which always fails construction.

        Raises:
            SearchBackendNotImplementedError: Always raised with message "OpenSearch/Elasticsearch".
        """
        raise SearchBackendNotImplementedError("OpenSearch/Elasticsearch")

    def ensure_index(self, index_name: str, settings: Mapping[str, Any]) -> None:
        """
        Ensure the named index exists with the provided settings.

        Parameters:
            index_name (str): The name of the index to create or verify.
            settings (Mapping[str, Any]): Index configuration (for example mappings, analyzers, and other OpenSearch/Elasticsearch settings).
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
        Execute a search query against the specified index using optional filters, sorting, pagination, and type constraints.

        Parameters:
            index_name (str): Name of the index to search.
            query (str): Full-text query string to match documents.
            filters (Mapping[str, Any] | Sequence[Mapping[str, Any]] | None): Optional filter(s) to restrict results; can be a single mapping or a sequence of mappings representing filter clauses.
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
