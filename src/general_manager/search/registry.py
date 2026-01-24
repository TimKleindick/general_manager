"""Search configuration registry helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import (
    IndexConfig,
    resolve_search_config,
    SearchConfigSpec,
)


@dataclass(frozen=True)
class SearchIndexSettings:
    """Aggregated index settings derived from manager configurations."""

    searchable_fields: tuple[str, ...]
    filterable_fields: tuple[str, ...]
    field_boosts: Mapping[str, float]


def iter_searchable_managers() -> Iterable[type]:
    """Yield managers that declare a SearchConfig."""
    for manager_class in GeneralManagerMeta.all_classes:
        config = resolve_search_config(getattr(manager_class, "SearchConfig", None))
        if config is None or not config.indexes:
            continue
        yield manager_class


def get_search_config(manager_class: type) -> SearchConfigSpec | None:
    """Return the resolved SearchConfig for a manager class, if any."""
    return resolve_search_config(getattr(manager_class, "SearchConfig", None))


def get_index_config(manager_class: type, index_name: str) -> IndexConfig | None:
    """Return the IndexConfig for a manager and index name, if configured."""
    config = get_search_config(manager_class)
    if config is None:
        return None
    for index in config.indexes:
        if index.name == index_name:
            return index
    return None


def iter_index_configs(index_name: str) -> Iterable[tuple[type, IndexConfig]]:
    """Iterate configured IndexConfig entries for a given index name."""
    for manager_class in iter_searchable_managers():
        index_config = get_index_config(manager_class, index_name)
        if index_config is None:
            continue
        yield manager_class, index_config


def get_type_label(manager_class: type) -> str:
    """Return the type label for a manager class."""
    config = get_search_config(manager_class)
    if config and config.type_label:
        return config.type_label
    return manager_class.__name__


def get_searchable_type_map() -> dict[str, type]:
    """Return a mapping of type labels to manager classes."""
    return {get_type_label(manager): manager for manager in iter_searchable_managers()}


def collect_index_settings(index_name: str) -> SearchIndexSettings:
    """Aggregate searchable/filterable fields and field boosts for an index."""
    searchable_fields: list[str] = []
    filterable_fields: set[str] = {"type"}
    field_boosts: dict[str, float] = {}

    for _manager_class, index_config in iter_index_configs(index_name):
        for field_config in index_config.iter_fields():
            if field_config.name not in searchable_fields:
                searchable_fields.append(field_config.name)
            if field_config.boost is not None:
                existing = field_boosts.get(field_config.name, 1.0)
                field_boosts[field_config.name] = max(existing, field_config.boost)
        for filter_field in index_config.filters:
            filterable_fields.add(filter_field)

    return SearchIndexSettings(
        searchable_fields=tuple(searchable_fields),
        filterable_fields=tuple(sorted(filterable_fields)),
        field_boosts=field_boosts,
    )
