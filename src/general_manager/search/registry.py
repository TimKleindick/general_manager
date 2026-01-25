"""Search configuration registry helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

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
    sortable_fields: tuple[str, ...]
    field_boosts: Mapping[str, float]


def iter_searchable_managers() -> Iterable[type]:
    """
    Iterate manager classes that define a SearchConfig with at least one index.
    
    @returns An iterable of manager classes that have a configured `SearchConfig` containing one or more indexes.
    """
    for manager_class in GeneralManagerMeta.all_classes:
        config = resolve_search_config(getattr(manager_class, "SearchConfig", None))
        if config is None or not config.indexes:
            continue
        yield manager_class


def get_search_config(manager_class: type) -> SearchConfigSpec | None:
    """
    Obtain the manager's configured search specification.
    
    Returns:
    	The resolved SearchConfigSpec for the manager, or `None` if the manager does not define a `SearchConfig`.
    """
    return resolve_search_config(getattr(manager_class, "SearchConfig", None))


def get_index_config(manager_class: type, index_name: str) -> IndexConfig | None:
    """
    Get the IndexConfig for the given manager class and index name.
    
    Parameters:
        manager_class (type): Manager class whose search configuration will be inspected.
        index_name (str): Name of the index to retrieve.
    
    Returns:
        The matching IndexConfig if configured, `None` otherwise.
    """
    config = get_search_config(manager_class)
    if config is None:
        return None
    for index in config.indexes:
        if index.name == index_name:
            return index
    return None


def iter_index_configs(index_name: str) -> Iterable[tuple[type, IndexConfig]]:
    """
    Yield (manager_class, IndexConfig) pairs for every manager that defines the given index name.
    
    Parameters:
        index_name (str): Name of the index to search for across registered managers.
    
    Returns:
        Iterable[tuple[type, IndexConfig]]: An iterator yielding tuples of the manager class and its matching IndexConfig for each manager that declares the index.
    """
    for manager_class in iter_searchable_managers():
        index_config = get_index_config(manager_class, index_name)
        if index_config is None:
            continue
        yield manager_class, index_config


def get_type_label(manager_class: type) -> str:
    """
    Get the type label for a manager class.
    
    Returns:
        The configured type label for the manager if present, otherwise the manager class's `__name__`.
    """
    config = get_search_config(manager_class)
    if config and config.type_label:
        return config.type_label
    return manager_class.__name__


def get_searchable_type_map() -> dict[str, type]:
    """
    Map searchable type labels to their manager classes.
    
    Returns:
        mapping (dict[str, type]): Mapping from a manager's searchable type label to its manager class.
        Only managers that define search indexes are included.
    """
    return {get_type_label(manager): manager for manager in iter_searchable_managers()}


def collect_index_settings(index_name: str) -> SearchIndexSettings:
    """
    Collect aggregated field roles and boost values for the specified index across all searchable managers.
    
    Parameters:
    	index_name (str): Name of the index to collect settings for.
    
    Returns:
    	SearchIndexSettings: Aggregated settings containing:
    		- searchable_fields: tuple of field names in the order they were first encountered.
    		- filterable_fields: tuple of filterable field names sorted alphabetically (always includes "type").
    		- sortable_fields: tuple of sortable field names sorted alphabetically.
    		- field_boosts: mapping from field name to the highest boost value found (defaults to 1.0 when unspecified).
    """
    searchable_fields: list[str] = []
    filterable_fields: set[str] = {"type"}
    sortable_fields: set[str] = set()
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
        for sort_field in index_config.sorts:
            sortable_fields.add(sort_field)

    return SearchIndexSettings(
        searchable_fields=tuple(searchable_fields),
        filterable_fields=tuple(sorted(filterable_fields)),
        sortable_fields=tuple(sorted(sortable_fields)),
        field_boosts=field_boosts,
    )


def get_index_names() -> set[str]:
    """
    List all configured search index names across searchable managers.
    
    Returns:
        A set of configured index name strings.
    """
    names: set[str] = set()
    for manager_class in iter_searchable_managers():
        config = get_search_config(manager_class)
        if config is None:
            continue
        for index in config.indexes:
            names.add(index.name)
    return names


def get_filterable_fields(index_name: str) -> set[str]:
    """
    Get filterable field names for the given index.
    
    Returns:
        filterable_fields (set[str]): Field names allowed for filtering for the index.
    """
    settings = collect_index_settings(index_name)
    return set(settings.filterable_fields)


def validate_filter_keys(index_name: str, filters: Mapping[str, Any]) -> None:
    """
    Ensure the provided filter keys are allowed for the specified index.
    
    Parameters:
        index_name (str): The index name whose configured filterable fields are used for validation.
        filters (Mapping[str, Any]): Mapping of filter keys to values; keys may include lookup suffixes separated by '__'. Only the portion before the first '__' (the base field name) is validated.
    
    Raises:
        InvalidFilterFieldError: If a base filter field is not configured as filterable for the given index.
    """
    allowed = get_filterable_fields(index_name)
    for key in filters.keys():
        base_key = key.split("__")[0]
        if base_key not in allowed:
            raise InvalidFilterFieldError(base_key, index_name)


class InvalidFilterFieldError(ValueError):
    """Raised when a filter field is not configured as filterable."""

    def __init__(self, field_name: str, index_name: str) -> None:
        """
        Initialize the InvalidFilterFieldError for a filter field not allowed on a given index.
        
        Parameters:
            field_name (str): Name of the filter field that is not allowed.
            index_name (str): Name of the index for which the filter field is invalid.
        """
        super().__init__(
            f"Filter field '{field_name}' is not allowed for '{index_name}'."
        )