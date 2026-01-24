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
        raise SearchBackendNotImplementedError("OpenSearch/Elasticsearch")

    def ensure_index(self, index_name: str, settings: Mapping[str, Any]) -> None:
        raise SearchBackendNotImplementedError("OpenSearch")

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        raise SearchBackendNotImplementedError("OpenSearch")

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
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
        raise SearchBackendNotImplementedError("OpenSearch")
