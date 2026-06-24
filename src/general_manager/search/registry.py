"""Search configuration registry helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import (
    IndexConfig,
    resolve_search_config,
    SearchConfigSpec,
)


@dataclass(frozen=True)
class SearchIndexSettings:
    """Aggregated index settings derived from manager configurations.

    Attributes:
        searchable_fields: Search document field names in first-seen order.
        filterable_fields: Alphabetically sorted filterable field names,
            including the synthetic `"type"` field.
        sortable_fields: Alphabetically sorted sortable field names.
        field_boosts: Highest configured boost per field name.
    """

    searchable_fields: tuple[str, ...]
    filterable_fields: tuple[str, ...]
    sortable_fields: tuple[str, ...]
    field_boosts: Mapping[str, float]


def iter_searchable_managers() -> Iterable[type[GeneralManager]]:
    """
    Iterate manager classes that define at least one search index.

    Yields:
        Manager classes from `GeneralManagerMeta.all_classes` whose resolved
        `SearchConfig` is present and contains one or more `IndexConfig` values,
        preserving `GeneralManagerMeta.all_classes` order.

    Raises:
        Exceptions from `resolve_search_config()` propagate for malformed
        manager search configuration.
    """
    for manager_class in GeneralManagerMeta.all_classes:
        config = resolve_search_config(getattr(manager_class, "SearchConfig", None))
        if config is None or not config.indexes:
            continue
        yield manager_class


def get_search_config(
    manager_class: type[GeneralManager],
) -> SearchConfigSpec | None:
    """
    Obtain the manager's configured search specification.

    Returns:
        The resolved `SearchConfigSpec` for the manager, or `None` if the
        manager does not define `SearchConfig`.

    Raises:
        Exceptions from `resolve_search_config()` propagate for malformed
        manager search configuration.
    """
    return resolve_search_config(getattr(manager_class, "SearchConfig", None))


def get_index_config(
    manager_class: type[GeneralManager],
    index_name: str,
) -> IndexConfig | None:
    """
    Return the first configured index matching `index_name`.

    Args:
        manager_class: Manager class whose search configuration is inspected.
        index_name: Index name to retrieve.

    Returns:
        The first matching `IndexConfig`, or `None` when the manager has no
        search config or no index with that name.

    Raises:
        Exceptions from `resolve_search_config()` propagate through
        `get_search_config()` for malformed manager search configuration.
    """
    config = get_search_config(manager_class)
    if config is None:
        return None
    for index in config.indexes:
        if index.name == index_name:
            return index
    return None


def iter_index_configs(
    index_name: str,
) -> Iterable[tuple[type[GeneralManager], IndexConfig]]:
    """
    Yield manager/index pairs for every searchable manager declaring an index.

    Args:
        index_name: Index name to search for across registered managers.

    Yields:
        `(manager_class, index_config)` for each manager whose first matching
        `IndexConfig` has `name == index_name`, preserving searchable manager
        order.

    Raises:
        Exceptions from `resolve_search_config()` propagate while searchable
        managers are discovered.
    """
    for manager_class in iter_searchable_managers():
        index_config = get_index_config(manager_class, index_name)
        if index_config is None:
            continue
        yield manager_class, index_config


def get_type_label(manager_class: type[GeneralManager]) -> str:
    """
    Return the searchable type label for a manager class.

    Returns:
        The resolved `SearchConfig.type_label` when configured; otherwise the
        manager class name.

    Raises:
        Exceptions from `resolve_search_config()` propagate through
        `get_search_config()` for malformed manager search configuration.
    """
    config = get_search_config(manager_class)
    if config and config.type_label:
        return config.type_label
    return manager_class.__name__


def get_searchable_type_map() -> dict[str, type[GeneralManager]]:
    """
    Map searchable type labels to their manager classes.

    Returns:
        Mapping from a manager's searchable type label to its manager class.
        Only managers that define search indexes are included. If multiple
        managers resolve to the same type label, the later manager in
        `GeneralManagerMeta.all_classes` overwrites the earlier one without a
        warning or error.

    Raises:
        Exceptions from `resolve_search_config()` propagate while searchable
        managers are discovered.
    """
    return {get_type_label(manager): manager for manager in iter_searchable_managers()}


def collect_index_settings(index_name: str) -> SearchIndexSettings:
    """
    Collect aggregate field roles and boost values for an index name.

    Args:
        index_name: Index name to collect settings for.

    Returns:
        Aggregated settings. Searchable fields preserve first-seen order across
        matching manager configs. Filterable and sortable fields are sorted
        alphabetically. The synthetic `"type"` filter is always included.
        Duplicate boosts keep the highest configured value; missing boosts do
        not create a field boost entry. Boosts are collected only from
        `IndexConfig.iter_fields()` entries and therefore correspond to fields
        included in `searchable_fields`.

    Raises:
        Exceptions from `resolve_search_config()` propagate while searchable
        managers are discovered.
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
        Unique configured index name strings from every searchable manager.

    Raises:
        Exceptions from `resolve_search_config()` propagate while searchable
        managers are discovered.
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
        Field names allowed for filtering for the index, including the synthetic
        `"type"` field.

    Raises:
        Exceptions from `resolve_search_config()` propagate while searchable
        managers are discovered.
    """
    settings = collect_index_settings(index_name)
    return set(settings.filterable_fields)


def validate_filter_keys(index_name: str, filters: Mapping[str, object]) -> None:
    """
    Ensure the provided filter keys are allowed for the specified index.

    Args:
        index_name: Index name whose configured filterable fields are used for
            validation.
        filters: Mapping of filter keys to arbitrary values. Values are not
            inspected. Keys may include lookup suffixes separated by `"__"`;
            only the portion before the first `"__"` is validated.

    Raises:
        InvalidFilterFieldError: If a base filter field is not configured as
            filterable for the given index.
        Exceptions from `resolve_search_config()` propagate while searchable
            managers are discovered.
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
        Initialize the error for a filter field not allowed on an index.

        Args:
            field_name: Name of the filter field that is not allowed.
            index_name: Name of the index for which the filter field is invalid.
        """
        super().__init__(
            f"Filter field '{field_name}' is not allowed for '{index_name}'."
        )
