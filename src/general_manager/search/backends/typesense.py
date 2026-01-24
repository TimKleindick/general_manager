"""Typesense backend stub."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from general_manager.search.backend import (
    SearchBackendNotImplementedError,
    SearchDocument,
    SearchResult,
)


class TypesenseBackend:
    """Typesense implementation stub."""

    def __init__(self, *_: Any, **__: Any) -> None:
        raise SearchBackendNotImplementedError("Typesense")

    def ensure_index(self, index_name: str, settings: Mapping[str, Any]) -> None:
        raise SearchBackendNotImplementedError("Typesense")

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        raise SearchBackendNotImplementedError("Typesense")

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
        raise SearchBackendNotImplementedError("Typesense")

    def search(
        self,
        index_name: str,
        query: str,
        *,
        filters: Mapping[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
        types: Sequence[str] | None = None,
    ) -> SearchResult:
        raise SearchBackendNotImplementedError("Typesense")
