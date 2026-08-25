"""Deterministic local resolution of chat-exposed managers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

from general_manager.chat.planned.catalog import (
    ManagerCatalog,
    ManagerCatalogEntry,
    normalize_match_text,
)
from general_manager.chat.schema_index import find_exposed_path


_NO_PATH_DISTANCE = 1_000_000
_MAX_CANDIDATES = 5
_MAX_EXPLANATIONS = 3
PathFinder = Callable[[str, str], list[str] | None]


@dataclass(frozen=True)
class ManagerCandidate:
    """One compact, locally ranked manager candidate."""

    manager: str
    explanations: tuple[str, ...]
    exact: bool


@dataclass(frozen=True)
class _ScoredCandidate:
    candidate: ManagerCandidate
    rank: tuple[int, int, int, int, int, str]


def _singularize(term: str) -> str:
    if len(term) > 3 and term.endswith("ies"):
        return f"{term[:-3]}y"
    if len(term) > 3 and term.endswith("s"):
        return term[:-1]
    return term


def _matching_terms(value: str) -> set[str]:
    terms = set(normalize_match_text(value).split())
    return terms | {_singularize(term) for term in terms}


def _source_matches(query_terms: set[str], value: str) -> bool:
    return bool(query_terms & _matching_terms(value))


def _schema_text(summary: Mapping[str, Any], key: str) -> str:
    value = summary.get(key, "")
    if isinstance(value, str):
        return value
    if not isinstance(value, Sequence):
        return ""
    if key != "relations":
        return " ".join(item for item in value if isinstance(item, str))
    relation_text: list[str] = []
    for relation in value:
        if not isinstance(relation, Mapping):
            continue
        for relation_key in ("name", "target"):
            relation_value = relation.get(relation_key)
            if isinstance(relation_value, str):
                relation_text.append(relation_value)
    return " ".join(relation_text)


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _schema_fingerprint(schema_index: Mapping[str, Mapping[str, Any]]) -> str:
    serialized = json.dumps(
        _jsonable(schema_index), sort_keys=True, separators=(",", ":")
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


class ManagerResolver:
    """Rank only managers present in the live chat schema index."""

    def __init__(
        self,
        schema_index: Mapping[str, Mapping[str, Any]],
        catalog: ManagerCatalog,
        *,
        path_finder: PathFinder = find_exposed_path,
    ) -> None:
        self.schema_index = schema_index
        self.catalog = catalog
        self._path_finder = path_finder
        self.cache: dict[
            tuple[str, str, str, tuple[str, ...]], tuple[ManagerCandidate, ...]
        ] = {}

    def resolve(
        self,
        query: str,
        anchors: tuple[str, ...] = (),
    ) -> tuple[ManagerCandidate, ...]:
        """Return up to five candidates in the specified lexicographic order."""
        normalized_query = normalize_match_text(query)
        if not normalized_query:
            return ()
        normalized_anchors = tuple(
            sorted({anchor for anchor in anchors if anchor in self.schema_index})
        )
        cache_key = (
            normalized_query,
            _schema_fingerprint(self.schema_index),
            self.catalog.fingerprint,
            normalized_anchors,
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        query_terms = _matching_terms(normalized_query)
        exact_manager_counts = self._exact_manager_counts(normalized_query)
        exact_alias_counts = self._exact_alias_counts(normalized_query)
        scored = [
            candidate
            for manager_name, summary in self.schema_index.items()
            if (
                candidate := self._score_candidate(
                    manager_name,
                    summary,
                    normalized_query,
                    query_terms,
                    exact_manager_counts,
                    exact_alias_counts,
                    normalized_anchors,
                )
            )
            is not None
        ]
        scored.sort(key=lambda item: item.rank)
        result = tuple(item.candidate for item in scored[:_MAX_CANDIDATES])
        self.cache[cache_key] = result
        return result

    def _exact_manager_counts(self, normalized_query: str) -> Mapping[str, int]:
        return {
            manager_name: int(normalize_match_text(manager_name) == normalized_query)
            for manager_name in self.schema_index
        }

    def _exact_alias_counts(self, normalized_query: str) -> Mapping[str, int]:
        return {
            manager_name: sum(alias == normalized_query for alias in entry.aliases)
            for manager_name, entry in self.catalog.entries.items()
        }

    def _score_candidate(
        self,
        manager_name: str,
        summary: Mapping[str, Any],
        normalized_query: str,
        query_terms: set[str],
        exact_manager_counts: Mapping[str, int],
        exact_alias_counts: Mapping[str, int],
        anchors: tuple[str, ...],
    ) -> _ScoredCandidate | None:
        entry = self.catalog.entries.get(manager_name)
        manager_exact = (
            normalize_match_text(manager_name) == normalized_query
            and sum(exact_manager_counts.values()) == 1
        )
        alias_exact = (
            entry is not None
            and normalized_query in entry.aliases
            and sum(exact_alias_counts.values()) == 1
        )
        catalog_sources = self._catalog_sources(entry, query_terms)
        schema_sources = self._schema_sources(summary, query_terms)
        name_matches = _source_matches(query_terms, manager_name)
        if not (
            manager_exact
            or alias_exact
            or catalog_sources
            or schema_sources
            or name_matches
        ):
            return None
        exact = manager_exact or alias_exact
        explanations = self._explanations(
            manager_exact, alias_exact, catalog_sources, schema_sources
        )
        return _ScoredCandidate(
            candidate=ManagerCandidate(
                manager=manager_name,
                explanations=explanations,
                exact=exact,
            ),
            rank=(
                -int(manager_exact),
                -int(alias_exact),
                -len(catalog_sources),
                -len(schema_sources),
                self._anchor_distance(manager_name, anchors),
                manager_name,
            ),
        )

    @staticmethod
    def _catalog_sources(
        entry: ManagerCatalogEntry | None,
        query_terms: set[str],
    ) -> tuple[str, ...]:
        if entry is None:
            return ()
        sources = (
            ("catalog domain", entry.domain),
            ("catalog aliases", " ".join(entry.aliases)),
            ("catalog use_when", entry.use_when),
        )
        return tuple(
            source_name
            for source_name, source_value in sources
            if _source_matches(query_terms, source_value)
        )

    @staticmethod
    def _schema_sources(
        summary: Mapping[str, Any],
        query_terms: set[str],
    ) -> tuple[str, ...]:
        sources = (
            ("schema description", _schema_text(summary, "description")),
            ("schema fields", _schema_text(summary, "fields")),
            ("schema filters", _schema_text(summary, "filters")),
            ("schema relations", _schema_text(summary, "relations")),
        )
        return tuple(
            source_name
            for source_name, source_value in sources
            if _source_matches(query_terms, source_value)
        )

    @staticmethod
    def _explanations(
        manager_exact: bool,
        alias_exact: bool,
        catalog_sources: tuple[str, ...],
        schema_sources: tuple[str, ...],
    ) -> tuple[str, ...]:
        explanations: list[str] = []
        if manager_exact:
            explanations.append("exact manager name")
        if alias_exact:
            explanations.append("exact catalog alias")
        explanations.extend(catalog_sources)
        explanations.extend(schema_sources)
        return tuple(explanations[:_MAX_EXPLANATIONS])

    def _anchor_distance(self, manager_name: str, anchors: tuple[str, ...]) -> int:
        if not anchors:
            return _NO_PATH_DISTANCE
        distances: list[int] = []
        for anchor in anchors:
            if anchor == manager_name:
                distances.append(0)
                continue
            path = self._path_finder(anchor, manager_name)
            if path is not None:
                distances.append(len(path))
        return min(distances, default=_NO_PATH_DISTANCE)
