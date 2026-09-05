"""In-memory development search backend."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from threading import RLock

from general_manager.search.backend import SearchDocument, SearchHit, SearchResult
from general_manager.bucket._ordering import normalize_ordering, sort_items
from general_manager.utils.filter_parser import apply_lookup


@dataclass
class _IndexStore:
    documents: dict[str, SearchDocument] = field(default_factory=dict)
    token_index: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    settings: Mapping[str, object] = field(default_factory=dict)


class DevSearchBackend:
    """Process-local development search with optional lazy per-index hydration."""

    def __init__(self, *, auto_reindex: bool = False) -> None:
        """
        Initialize an empty in-memory registry and optional hydration lifecycle.

        Automatic hydration is disabled for directly constructed backends unless
        explicitly opted in. When enabled, each index hydrates on its first
        search in this process. Failed hydration propagates and remains
        retryable. The lock coordinates only first-search hydration; ordinary
        reads and writes retain the backend's existing unsynchronized behavior.
        """
        self._indexes: dict[str, _IndexStore] = {}
        self._auto_reindex = auto_reindex
        self._hydrated_indexes: set[str] = set()
        self._hydrating_indexes: set[str] = set()
        self._hydration_lock = RLock()

    @property
    def auto_reindex_enabled(self) -> bool:
        """Return whether first-search hydration is enabled for this backend."""
        return self._auto_reindex

    def configure_auto_reindex(self, enabled: bool) -> None:
        """Enable or disable first-search hydration for this backend instance."""
        self._auto_reindex = enabled

    def ensure_index(self, index_name: str, settings: Mapping[str, object]) -> None:
        """
        Ensure an index exists and update its settings.

        Parameters:
            index_name (str): Name of the index to create or retrieve.
            settings: Settings to assign to the index; replaces any existing
                settings. The dev backend stores the mapping but does not
                inspect it.
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

    def list_document_ids(
        self,
        index_name: str,
        *,
        types: Sequence[str] | None = None,
    ) -> set[str]:
        """Return stored document IDs, optionally restricted by document type."""
        store = self._indexes.setdefault(index_name, _IndexStore())
        type_filter = set(types or ())
        return {
            document.id
            for document in store.documents.values()
            if not type_filter or document.type in type_filter
        }

    def search(
        self,
        index_name: str,
        query: str,
        *,
        filters: Mapping[str, object] | Sequence[Mapping[str, object]] | None = None,
        filter_expression: str | None = None,
        sort: Sequence[str] | None = None,
        limit: int = 10,
        offset: int = 0,
        types: Sequence[str] | None = None,
    ) -> SearchResult:
        """
        Search an index for documents matching a query and return scored, optionally filtered and sorted hits.

        When auto-reindex is enabled, the first search for an index lazily
        hydrates that index in this process before evaluating the query. The
        hydration guard coordinates concurrent and nested first searches only;
        a failed source error propagates and the next search can retry.
        The backend reads and writes documents under the `index_name` argument;
        `SearchDocument.index` is stored but not validated. Document IDs are
        unique only inside one in-memory index. Duplicate IDs inside one
        `upsert()` call are processed in order, so the last document wins.
        Deletes are best-effort and duplicate IDs are harmless. `ensure_index()`
        is idempotent and replaces the stored settings mapping for the index.
        Operations are not transactional; mutations completed before an
        exception remain in memory. Queries are lowercased and split on
        whitespace, with duplicate query tokens removed in first-seen order.
        Indexed tokens are built from every top-level
        `SearchDocument.data` value: `None` yields no tokens, strings split on
        whitespace, lists/tuples/sets are processed recursively, and all other
        values become `str(value).lower().split()`. Dict values are not
        traversed; they are tokenized from their string representation. A
        document matches when every distinct query token equals or prefixes a
        token extracted from any indexed field. Empty queries match every document
        that passes type and structured filters. The operation
        order is type filtering, structured filtering, query scoring/matching,
        sorting, and then pagination with `results[offset:offset + limit]`;
        negative values intentionally follow Python slice behavior.
        Scores sum matching field boosts, then multiply by `index_boost` when
        set. Results sort by score descending unless `sort` is provided.
        Sorting applies each signed document data field in sequence. `None` and missing
        fields are treated as missing and kept last for both ascending and
        descending sorts. `bool` values use Python's numeric ordering because
        `bool` is an `int` subclass. Other numeric values sort numerically,
        and every non-numeric, non-missing value is compared as `str(value)`.
        Explicit sort ties use complete logical document identity (type and
        typed identification), then adapter document metadata, so page
        membership agrees with manager ordering. Source insertion order is
        preserved when no usable logical identity exists or identities are
        equal, including equal-score default ordering. The backend stores
        document and settings objects by reference, returns hit data from the
        stored `SearchDocument.data` mapping, is
        process-local memory only, and does not provide persistence or ordinary
        read/write synchronization beyond first-search hydration.

        Parameters:
            index_name (str): Name of the index to search.
            query (str): Query string to tokenize and match against indexed documents.
            filters: Field-based filters to apply; may be a single mapping or
                a sequence of alternative filter groups. Mapping keys target
                `SearchDocument.data` field names, optionally followed by one
                lookup suffix separated by `__`. Supported lookup suffixes are
                `exact`, `lt`, `lte`, `gt`, `gte`, `contains`, `startswith`,
                `endswith`, and `in`; no nested data traversal is supported.
                A key without a suffix uses `exact`. Within one mapping fields
                are ANDed; between mappings groups are ORed. Comparisons use
                the shared `apply_lookup()` helper: string operations are
                case-sensitive, missing fields behave like `None`, incompatible
                mixed-type comparisons and invalid lookup/value combinations
                return `False`, and `None` compares only through `exact`.
            filter_expression (str | None): Unsupported in this backend; passing a value raises NotImplementedError.
            sort (Sequence[str] | None): Signed document data field names.
                Prefix a field with ``-`` for descending order. If omitted,
                results are sorted by score.
            limit (int): Maximum number of hits to return. Native slice
                semantics apply, including for negative values.
            offset (int): Number of matching results to skip before collecting hits. Native slice semantics apply, including for negative values.
            types (Sequence[str] | None): If provided, restrict results to documents whose type is in this sequence.

        Returns:
            SearchResult: Object containing `hits` (the returned page with
            data fields included), `total` (matching documents before
            pagination), and `took_ms` (search time in milliseconds).

        Raises:
            NotImplementedError: If `filter_expression` is not None. Other
                operational failures are not normalized and may surface as
                ordinary Python exceptions.
        """
        self._ensure_hydrated(index_name)
        if filter_expression is not None:
            raise NotImplementedError(
                "filter_expression is not supported by the dev backend."
            )
        start = time.perf_counter()
        store = self._indexes.setdefault(index_name, _IndexStore())
        tokens = self._tokenize_query(query)
        results: list[tuple[SearchDocument, float]] = []

        for doc_id, document in store.documents.items():
            if not self._matches_predicates(document, filters=filters, types=types):
                continue
            score = self._score_document(
                document, tokens, store.token_index.get(doc_id)
            )
            if tokens and score <= 0:
                continue
            results.append((document, score))

        terms = normalize_ordering(sort if sort is not None else ())
        if terms:
            results = sort_items(
                results,
                terms,
                value_for=lambda item, field: item[0].data.get(field),
                identity_for=lambda item: (
                    item[0].type,
                    item[0].identification,
                    item[0].index,
                    item[0].id,
                ),
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

    def _ensure_hydrated(self, index_name: str) -> None:
        """Hydrate one index once when this backend has opted into the lifecycle."""
        if not self._auto_reindex or index_name in self._hydrated_indexes:
            return
        with self._hydration_lock:
            if index_name in self._hydrated_indexes:
                return
            if index_name in self._hydrating_indexes:
                return
            self._hydrating_indexes.add(index_name)
            try:
                self._reindex_configured_managers(index_name)
                self._hydrated_indexes.add(index_name)
            finally:
                self._hydrating_indexes.discard(index_name)

    def _reindex_configured_managers(self, index_name: str) -> None:
        """Reindex every manager configured for one index into this process."""
        from general_manager.search.indexer import SearchIndexer
        from general_manager.search.registry import iter_index_configs

        indexer = SearchIndexer(self)
        for manager_class, _index_config in iter_index_configs(index_name):
            indexer.reindex_manager_index(manager_class, index_name)

    @staticmethod
    def _tokenize_query(query: str) -> list[str]:
        """
        Split a query string into lowercase whitespace-separated tokens.

        Parameters:
            query (str): The input query string to tokenize.

        Returns:
            list[str]: Distinct lowercase tokens extracted from the query in first-seen order.
        """
        return list(dict.fromkeys(query.lower().split()))

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

    def _tokenize_value(self, value: object) -> set[str]:
        """
        Extract lowercase whitespace-separated tokens from a value.

        Parameters:
            value: The input to tokenize. If None, returns an empty set.
                Strings are split on whitespace. Lists, tuples, and sets are
                tokenized recursively; other values are converted to string
                before tokenization.

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

        Every distinct query token must be present in at least one field's token
        set (or be a prefix of a field token). For each matching token, the
        boosts of all matching fields are added to the score. After summing
        matches across all fields, the total is multiplied by
        `document.index_boost` when it is set.

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
        for token in tokens:
            token_score = 0.0
            for field_name, field_tokens in token_index.items():
                field_boost = document.field_boosts.get(field_name, 1.0)
                if token in field_tokens or any(
                    field_token.startswith(token) for field_token in field_tokens
                ):
                    token_score += field_boost
            if token_score <= 0:
                return 0.0
            score += token_score
        if document.index_boost:
            score *= document.index_boost
        return score

    def _passes_filters(
        self,
        document: SearchDocument,
        filters: Mapping[str, object] | Sequence[Mapping[str, object]],
    ) -> bool:
        """
        Determine whether a document satisfies the provided filter or filter groups.

        Filters may be a mapping of field lookups to values or a sequence of such mappings. A sequence is treated as an OR of its element mappings; a mapping is treated as an AND of its key/value checks. Keys may include a lookup suffix using the form "field__lookup"; if omitted the "exact" lookup is used. For "exact" and "in" lookups, if either the document field or the filter value is a collection, the check succeeds when the two collections have any intersection. Other lookups are evaluated using apply_lookup.

        Parameters:
            document (SearchDocument): Document to test against the filters.
            filters: A filter mapping or a sequence of filter mappings.

        Returns:
            bool: `true` if the document matches the filters, `false` otherwise.
        """
        if not isinstance(filters, Mapping):
            if not isinstance(filters, Sequence) or isinstance(
                filters, str | bytes | bytearray
            ):
                return False
            if not all(isinstance(group, Mapping) for group in filters):
                return False
            return any(self._passes_filters(document, group) for group in filters)
        for key, value in filters.items():
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
            if (
                lookup == "in"
                and isinstance(doc_value, (list, tuple, set))
                and isinstance(value, (list, tuple, set))
            ):
                if not set(doc_value).intersection(value):
                    return False
                continue
            if not apply_lookup(doc_value, lookup, value):
                return False
        return True

    def _matches_predicates(
        self,
        document: SearchDocument,
        *,
        filters: Mapping[str, object] | Sequence[Mapping[str, object]] | None,
        types: Sequence[str] | None,
    ) -> bool:
        """Return whether a document satisfies both type and filter predicates."""
        if types and document.type not in types:
            return False
        return not filters or self._passes_filters(document, filters)
