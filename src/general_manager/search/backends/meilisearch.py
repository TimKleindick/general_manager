"""Meilisearch backend adapter."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from general_manager.search.backend import (
    SearchBackendClientMissingError,
    SearchDocument,
    SearchHit,
    SearchResult,
)


class MeilisearchBackend:
    """Meilisearch implementation of the SearchBackend protocol."""

    def __init__(
        self,
        url: str = "http://127.0.0.1:7700",
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            try:
                import meilisearch  # type: ignore[import]
            except ImportError as exc:
                raise SearchBackendClientMissingError("Meilisearch") from exc
            client = meilisearch.Client(url, api_key)
        self._client = client

    def ensure_index(self, index_name: str, settings: Mapping[str, Any]) -> None:
        index = self._get_or_create_index(index_name)
        searchable_fields = settings.get("searchable_fields", [])
        filterable_fields = settings.get("filterable_fields", [])
        if searchable_fields:
            index.update_settings({"searchableAttributes": list(searchable_fields)})
        if filterable_fields:
            index.update_settings({"filterableAttributes": list(filterable_fields)})

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        index = self._get_or_create_index(index_name)
        payload = [self._document_payload(doc) for doc in documents]
        if payload:
            index.add_documents(payload)

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
        index = self._get_or_create_index(index_name)
        if ids:
            index.delete_documents(list(ids))

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
        index = self._get_or_create_index(index_name)
        payload: dict[str, Any] = {
            "q": query,
            "limit": limit,
            "offset": offset,
        }
        filter_expr = self._build_filter_expression(filters, types)
        if filter_expr:
            payload["filter"] = filter_expr

        response = index.search(**payload)
        hits = [
            SearchHit(
                id=hit.get("id"),
                type=hit.get("type"),
                identification=hit.get("identification", {}),
                score=hit.get("_rankingScore"),
                index=index_name,
                data=hit.get("data", {}),
            )
            for hit in response.get("hits", [])
        ]
        return SearchResult(
            hits=hits,
            total=response.get("estimatedTotalHits", len(hits)),
            took_ms=response.get("processingTimeMs"),
            raw=response,
        )

    def _get_or_create_index(self, index_name: str) -> Any:
        try:
            return self._client.get_index(index_name)
        except Exception:  # noqa: BLE001
            self._client.create_index(index_name, {"primaryKey": "id"})
            return self._client.get_index(index_name)

    @staticmethod
    def _document_payload(document: SearchDocument) -> dict[str, Any]:
        return {
            "id": document.id,
            "type": document.type,
            "identification": document.identification,
            "data": document.data,
            **document.data,
        }

    @staticmethod
    def _build_filter_expression(
        filters: Mapping[str, Any] | None,
        types: Sequence[str] | None,
    ) -> str | None:
        clauses: list[str] = []
        if types:
            type_clause = " OR ".join([f'type = "{type_name}"' for type_name in types])
            clauses.append(f"({type_clause})")
        if filters:
            for key, value in filters.items():
                if isinstance(value, (list, tuple, set)):
                    options = " OR ".join([f'{key} = "{item}"' for item in value])
                    clauses.append(f"({options})")
                else:
                    clauses.append(f'{key} = "{value}"')
        if not clauses:
            return None
        return " AND ".join(clauses)
