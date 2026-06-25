"""Typesense backend stub."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from general_manager.search.backend import (
    SearchBackendNotImplementedError,
    SearchDocument,
    SearchResult,
)


class TypesenseBackend:
    """Typesense backend placeholder.

    The adapter is publicly importable for configuration compatibility, but it
    is not implemented. Construction and every backend operation raise
    `SearchBackendNotImplementedError`.
    """

    def __init__(self, *_: object, **__: object) -> None:
        """
        Constructor for TypesenseBackend that indicates the backend is not implemented.

        Raises:
            SearchBackendNotImplementedError: Always raised with message "Typesense" to signal the backend is not implemented.
        """
        raise SearchBackendNotImplementedError("Typesense")

    def ensure_index(self, index_name: str, settings: Mapping[str, object]) -> None:
        """
        Ensure an index with the given name and settings exists in the backend.

        Parameters:
            index_name (str): Name of the index to create or ensure.
            settings: Index configuration to apply. The value is accepted for
                protocol compatibility but never inspected.

        Raises:
            SearchBackendNotImplementedError: Always raised because the Typesense backend is not implemented.
        """
        raise SearchBackendNotImplementedError("Typesense")

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        """
        Insert or update the given documents in the specified index.

        Parameters:
            index_name (str): Name of the index where documents should be upserted.
            documents (Sequence[SearchDocument]): Documents to insert or update.

        Raises:
            SearchBackendNotImplementedError: Always raised because the Typesense backend is not implemented.
        """
        raise SearchBackendNotImplementedError("Typesense")

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
        """
        Delete documents identified by their IDs from the specified index.

        Parameters:
            index_name (str): Name of the index from which to delete documents.
            ids (Sequence[str]): Sequence of document IDs to remove.

        Raises:
            SearchBackendNotImplementedError: Always raised because the Typesense backend is not implemented.
        """
        raise SearchBackendNotImplementedError("Typesense")

    def list_document_ids(
        self,
        index_name: str,
        *,
        types: Sequence[str] | None = None,
    ) -> set[str]:
        """
        List stored document IDs in the specified index.

        Raises:
            SearchBackendNotImplementedError: Always raised because the Typesense backend is not implemented.
        """
        raise SearchBackendNotImplementedError("Typesense")

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
        Perform a search against the specified index using the provided query and optional filtering, sorting, and pagination.

        Parameters:
            index_name (str): Name of the index to search.
            query (str): Query string to match documents.
            filters: Filter criteria as a single mapping or a sequence of
                mappings. Accepted for protocol compatibility but never
                inspected.
            filter_expression (str | None): A raw filter expression string to apply instead of or in addition to `filters`.
            sort_by (str | None): Field name to sort results by.
            sort_desc (bool): If `True`, sort results in descending order; ascending otherwise.
            limit (int): Maximum number of results to return.
            offset (int): Number of results to skip (for pagination).
            types (Sequence[str] | None): Optional list of document types to restrict the search to.

        Returns:
            SearchResult: Search results including matched documents and metadata such as total hits and pagination info.

        Raises:
            SearchBackendNotImplementedError: Always raised because the Typesense
                backend is not implemented.
        """
        raise SearchBackendNotImplementedError("Typesense")
