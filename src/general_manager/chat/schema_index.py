"""Schema indexing helpers for chat."""

from __future__ import annotations

from collections import deque
from functools import lru_cache
import re
from typing import Any, cast

from general_manager.api.graphql import GraphQL
from general_manager.utils.path_mapping import PathMap

DEFAULT_SEARCH_LIMIT = 10


def _unwrap_graphene_type(field_type: Any) -> Any:
    current = field_type
    while hasattr(current, "of_type"):
        current = current.of_type
    return current


def _is_exposed_manager(manager_class: type[Any]) -> bool:
    return bool(getattr(manager_class, "chat_exposed", True))


def _get_exposed_manager_names() -> set[str]:
    return {
        name
        for name, manager_class in GraphQL.manager_registry.items()
        if _is_exposed_manager(manager_class)
    }


def _schema_index_cache_key() -> tuple[int, int, int]:
    return (
        id(GraphQL.manager_registry),
        id(GraphQL.graphql_type_registry),
        id(GraphQL.graphql_filter_type_registry),
    )


def clear_schema_index_cache() -> None:
    """Clear the cached schema index."""
    _build_schema_index_cached.cache_clear()


@lru_cache(maxsize=8)
def _build_schema_index_cached(
    _cache_key: tuple[int, int, int],
) -> dict[str, dict[str, Any]]:
    """Build a compact index of chat-exposed managers from the GraphQL registry."""
    del _cache_key
    index: dict[str, dict[str, Any]] = {}
    exposed_names = _get_exposed_manager_names()
    for manager_name in sorted(exposed_names):
        graphene_type = GraphQL.graphql_type_registry.get(manager_name)
        if graphene_type is None:
            continue
        graphene_meta = cast(Any, graphene_type)._meta
        description = getattr(graphene_type, "__doc__", None) or ""
        description = " ".join(description.strip().split())
        fields: list[str] = []
        relations: list[dict[str, str]] = []
        for field_name, field in sorted(
            cast(dict[str, Any], graphene_meta.fields).items()
        ):
            unwrapped = _unwrap_graphene_type(field.type)
            target_name = next(
                (
                    candidate_name
                    for candidate_name, candidate_type in GraphQL.graphql_type_registry.items()
                    if candidate_type is unwrapped and candidate_name in exposed_names
                ),
                None,
            )
            if target_name is not None:
                relations.append({"name": field_name, "target": target_name})
            else:
                fields.append(field_name)
        filter_type = GraphQL.graphql_filter_type_registry.get(manager_name)
        filters = (
            sorted(cast(dict[str, Any], cast(Any, filter_type)._meta.fields).keys())
            if filter_type is not None
            else []
        )
        index[manager_name] = {
            "manager": manager_name,
            "description": description,
            "fields": fields,
            "relations": relations,
            "filters": filters,
        }
    return index


def build_schema_index() -> dict[str, dict[str, Any]]:
    """Build or reuse the compact index of chat-exposed managers."""
    return _build_schema_index_cached(_schema_index_cache_key())


def _tokenize_search_text(value: str) -> list[str]:
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", value)
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", spaced).lower()
    return [term for term in normalized.split() if term]


def _singularize(term: str) -> str:
    if len(term) > 3 and term.endswith("ies"):
        return f"{term[:-3]}y"
    if len(term) > 3 and term.endswith("s"):
        return term[:-1]
    return term


def _search_term_groups(query: str) -> list[set[str]]:
    terms = _tokenize_search_text(query)
    groups: list[set[str]] = []
    for term in terms:
        variants = {term}
        singular = _singularize(term)
        if singular != term:
            variants.add(singular)
        groups.append(variants)
    return groups


def _summary_search_tokens(manager_name: str, summary: dict[str, Any]) -> list[str]:
    relation_names = [relation["name"] for relation in summary["relations"]]
    return _tokenize_search_text(
        " ".join(
            [
                manager_name,
                summary["description"],
                " ".join(summary["fields"]),
                " ".join(relation_names),
                " ".join(summary["filters"]),
            ]
        )
    )


def search_manager_summaries(
    query: str, *, limit: int = DEFAULT_SEARCH_LIMIT
) -> list[dict[str, Any]]:
    """Search the schema index by manager name, description, and field names."""
    index = build_schema_index()
    query_term_groups = _search_term_groups(query)
    if not query_term_groups:
        return []
    scored: list[tuple[int, int, str, dict[str, Any]]] = []
    for manager_name, summary in index.items():
        tokens = _summary_search_tokens(manager_name, summary)
        token_set = set(tokens)
        haystack = " ".join(tokens)
        score = sum(
            any(term in token_set or term in haystack for term in variants)
            for variants in query_term_groups
        )
        if score:
            scored.append(
                (score, len(_tokenize_search_text(manager_name)), manager_name, summary)
            )
    full_score = len(query_term_groups)
    full_matches = [item for item in scored if item[0] == full_score]
    if full_matches:
        scored = full_matches
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [summary for _, _, _, summary in scored[:limit]]


def get_manager_schema_summary(manager_name: str) -> dict[str, Any] | None:
    """Return the indexed schema summary for one exposed manager."""
    return build_schema_index().get(manager_name)


def find_exposed_path(from_manager: str, to_manager: str) -> list[str] | None:
    """Return a PathMap traversal between exposed managers only."""
    exposed_names = _get_exposed_manager_names()
    if from_manager not in exposed_names or to_manager not in exposed_names:
        return None
    tracer = PathMap.mapping.get((from_manager, to_manager))
    if tracer is None:
        tracer = PathMap(from_manager).to(to_manager)
    if tracer is not None:
        path = getattr(tracer, "path", None)
        if path:
            return list(path)
    return _find_relational_path(from_manager, to_manager)


def _find_relational_path(from_manager: str, to_manager: str) -> list[str] | None:
    """Find a manager path from exposed schema relations, including reverse hops."""
    if from_manager == to_manager:
        return []
    index = build_schema_index()
    queue: deque[tuple[str, list[str]]] = deque([(from_manager, [])])
    visited = {from_manager}

    while queue:
        current, path = queue.popleft()
        summary = index.get(current)
        if summary is None:
            continue

        for relation in summary["relations"]:
            target = relation["target"]
            if target in visited:
                continue
            next_path = [*path, relation["name"]]
            if target == to_manager:
                return next_path
            visited.add(target)
            queue.append((target, next_path))

        for candidate_name, candidate_summary in index.items():
            if candidate_name in visited:
                continue
            reverse_relation = next(
                (
                    relation["name"]
                    for relation in candidate_summary["relations"]
                    if relation["target"] == current
                ),
                None,
            )
            if reverse_relation is None:
                continue
            next_path = [*path, reverse_relation]
            if candidate_name == to_manager:
                return next_path
            visited.add(candidate_name)
            queue.append((candidate_name, next_path))

    return None
