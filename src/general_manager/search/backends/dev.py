"""In-memory development search backend."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence, cast

from general_manager.search.backend import SearchDocument, SearchHit, SearchResult
from general_manager.utils.filter_parser import apply_lookup


@dataclass
class _IndexStore:
    documents: dict[str, SearchDocument] = field(default_factory=dict)
    token_index: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    settings: Mapping[str, Any] = field(default_factory=dict)


class DevSearchBackend:
    """Simple in-memory search backend intended for development."""

    def __init__(self) -> None:
        self._indexes: dict[str, _IndexStore] = {}

    def ensure_index(self, index_name: str, settings: Mapping[str, Any]) -> None:
        store = self._indexes.setdefault(index_name, _IndexStore())
        store.settings = settings

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        store = self._indexes.setdefault(index_name, _IndexStore())
        for document in documents:
            store.documents[document.id] = document
            store.token_index[document.id] = self._tokenize_document(document)

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
        store = self._indexes.setdefault(index_name, _IndexStore())
        for doc_id in ids:
            store.documents.pop(doc_id, None)
            store.token_index.pop(doc_id, None)

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
        start = time.perf_counter()
        store = self._indexes.setdefault(index_name, _IndexStore())
        tokens = self._tokenize_query(query)
        results: list[tuple[SearchDocument, float]] = []

        for doc_id, document in store.documents.items():
            if types and document.type not in types:
                continue
            if filters and not self._passes_filters(document, filters):
                continue
            score = self._score_document(
                document, tokens, store.token_index.get(doc_id)
            )
            if tokens and score <= 0:
                continue
            results.append((document, score))

        if sort_by:

            def _sort_key(item: tuple[SearchDocument, float]) -> tuple[bool, str]:
                value = item[0].data.get(sort_by)
                return (value is None, str(value))

            results.sort(
                key=_sort_key,
                reverse=sort_desc,
            )
        else:
            results.sort(key=lambda item: item[1], reverse=True)
        sliced = results[offset : offset + limit]

        hits = [
            SearchHit(
                id=document.id,
                type=document.type,
                identification=document.identification,
                score=score,
                index=index_name,
                data=document.data,
            )
            for document, score in sliced
        ]

        took_ms = int((time.perf_counter() - start) * 1000)
        return SearchResult(hits=hits, total=len(results), took_ms=took_ms)

    @staticmethod
    def _tokenize_query(query: str) -> list[str]:
        return [token for token in query.lower().split() if token]

    def _tokenize_document(self, document: SearchDocument) -> dict[str, set[str]]:
        token_map: dict[str, set[str]] = {}
        for field_name, value in document.data.items():
            token_map[field_name] = self._tokenize_value(value)
        return token_map

    def _tokenize_value(self, value: Any) -> set[str]:
        tokens: set[str] = set()
        if value is None:
            return tokens
        if isinstance(value, str):
            tokens.update(value.lower().split())
            return tokens
        if isinstance(value, (list, tuple, set)):
            for entry in value:
                tokens.update(self._tokenize_value(entry))
            return tokens
        tokens.update(str(value).lower().split())
        return tokens

    def _score_document(
        self,
        document: SearchDocument,
        tokens: list[str],
        token_index: dict[str, set[str]] | None,
    ) -> float:
        if not tokens:
            return 0.0
        token_index = token_index or {}
        score = 0.0
        for field_name, field_tokens in token_index.items():
            field_boost = document.field_boosts.get(field_name, 1.0)
            for token in tokens:
                if token in field_tokens:
                    score += field_boost
        if document.index_boost:
            score *= document.index_boost
        return score

    def _passes_filters(
        self,
        document: SearchDocument,
        filters: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    ) -> bool:
        if isinstance(filters, (list, tuple)):
            return any(self._passes_filters(document, group) for group in filters)
        mapping = cast(Mapping[str, Any], filters)
        for key, value in mapping.items():
            if "__" in key:
                field_name, lookup = key.split("__", 1)
            else:
                field_name, lookup = key, "exact"
            doc_value = document.data.get(field_name)
            if lookup == "exact" and isinstance(value, (list, tuple, set)):
                if isinstance(doc_value, (list, tuple, set)):
                    if not set(doc_value).intersection(value):
                        return False
                    continue
            if lookup == "in" and isinstance(doc_value, (list, tuple, set)):
                if not set(doc_value).intersection(value):
                    return False
                continue
            if not apply_lookup(doc_value, lookup, value):
                return False
        return True
