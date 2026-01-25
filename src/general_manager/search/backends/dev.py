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
        """
        Initialize the backend with an empty registry that maps index names (str) to _IndexStore instances.
        """
        self._indexes: dict[str, _IndexStore] = {}

    def ensure_index(self, index_name: str, settings: Mapping[str, Any]) -> None:
        """
        Ensure an index exists and update its settings.

        Parameters:
            index_name (str): Name of the index to create or retrieve.
            settings (Mapping[str, Any]): Settings to assign to the index; replaces any existing settings.
        """
        store = self._indexes.setdefault(index_name, _IndexStore())
        store.settings = settings

    def upsert(self, index_name: str, documents: Sequence[SearchDocument]) -> None:
        """
        Insert or update the given documents in the named in-memory index.

        Each document is stored by its `id` in the index's document map and a per-document token index is built and stored for use by searches; existing documents with the same id are replaced.

        Parameters:
            index_name (str): Name of the index to modify.
            documents (Sequence[SearchDocument]): Documents to insert or update.
        """
        store = self._indexes.setdefault(index_name, _IndexStore())
        for document in documents:
            store.documents[document.id] = document
            store.token_index[document.id] = self._tokenize_document(document)

    def delete(self, index_name: str, ids: Sequence[str]) -> None:
        """
        Remove documents and their token indexes from the specified in-memory index.

        This performs a best-effort removal: if an id is not present in the index, it is ignored.

        Parameters:
            index_name (str): Name of the index to modify.
            ids (Sequence[str]): Document ids to remove from the index.
        """
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
        """
        Search an index for documents matching a query and return scored, optionally filtered and sorted hits.

        Parameters:
            index_name (str): Name of the index to search.
            query (str): Query string to tokenize and match against indexed documents.
            filters (Mapping[str, Any] | Sequence[Mapping[str, Any]] | None): Field-based filters to apply; may be a single mapping or a sequence of alternative filter groups.
            filter_expression (str | None): Unsupported in this backend; passing a value raises NotImplementedError.
            sort_by (str | None): Document field name to sort results by; if omitted results are sorted by score.
            sort_desc (bool): If True, sort results in descending order for the chosen sort key.
            limit (int): Maximum number of hits to return.
            offset (int): Number of matching results to skip before collecting hits.
            types (Sequence[str] | None): If provided, restrict results to documents whose type is in this sequence.

        Returns:
            SearchResult: Object containing `hits` (list of SearchHit), `total` (number of matching documents), and `took_ms` (search time in milliseconds).

        Raises:
            NotImplementedError: If `filter_expression` is not None.
        """
        if filter_expression is not None:
            raise NotImplementedError(
                "filter_expression is not supported by the dev backend."
            )
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

            def _value_key(
                item: tuple[SearchDocument, float],
            ) -> tuple[int, float, str]:
                value = item[0].data.get(sort_by)
                if value is None:
                    return (2, 0.0, "")
                if isinstance(value, (int, float)):
                    return (0, float(value), "")
                return (1, 0.0, str(value))

            results.sort(key=_value_key, reverse=sort_desc)
            results.sort(key=lambda item: item[0].data.get(sort_by) is None)
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
        """
        Split a query string into lowercase whitespace-separated tokens.

        Parameters:
            query (str): The input query string to tokenize.

        Returns:
            list[str]: A list of lowercase tokens extracted from the query; empty tokens are omitted.
        """
        return [token for token in query.lower().split() if token]

    def _tokenize_document(self, document: SearchDocument) -> dict[str, set[str]]:
        """
        Create a mapping from each document field name to the set of tokens extracted from that field's value.

        Parameters:
            document (SearchDocument): The document whose field values will be tokenized.

        Returns:
            dict[str, set[str]]: A dictionary mapping field names to the set of lowercase tokens found in each field's value.
        """
        token_map: dict[str, set[str]] = {}
        for field_name, value in document.data.items():
            token_map[field_name] = self._tokenize_value(value)
        return token_map

    def _tokenize_value(self, value: Any) -> set[str]:
        """
        Extract lowercase whitespace-separated tokens from a value.

        Parameters:
            value (Any): The input to tokenize. If None, returns an empty set. Strings are split on whitespace. Iterables (list/tuple/set) are tokenized recursively; other values are converted to string before tokenization.

        Returns:
            set[str]: A set of lowercase tokens extracted from the input.
        """
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
        """
        Compute a relevance score for a document based on matching query tokens and configured boosts.

        Each time a token from `tokens` is present in a field's token set the field's boost is added to the score. After summing matches across all fields, the total is multiplied by `document.index_boost` when it is set.

        Parameters:
            tokens: The list of query tokens to match against the document's token index.
            token_index: Mapping from field name to the set of tokens present in that field (may be None).

        Returns:
            A float score: the sum of field boosts for each matching token, multiplied by `document.index_boost` if provided.
        """
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
        """
        Determine whether a document satisfies the provided filter or filter groups.

        Filters may be a mapping of field lookups to values or a sequence of such mappings. A sequence is treated as an OR of its element mappings; a mapping is treated as an AND of its key/value checks. Keys may include a lookup suffix using the form "field__lookup"; if omitted the "exact" lookup is used. For "exact" and "in" lookups, if either the document field or the filter value is a collection, the check succeeds when the two collections have any intersection. Other lookups are evaluated using apply_lookup.

        Parameters:
            document (SearchDocument): Document to test against the filters.
            filters (Mapping[str, Any] | Sequence[Mapping[str, Any]]): A filter mapping or a sequence of filter mappings.

        Returns:
            bool: `true` if the document matches the filters, `false` otherwise.
        """
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
