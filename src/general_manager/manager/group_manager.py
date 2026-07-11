"""Utility manager that aggregates grouped GeneralManager data."""

from __future__ import annotations
from collections.abc import Iterable, Iterator
from typing import Generic, cast, get_args
from datetime import datetime, date, time
from general_manager.api.property import GraphQLProperty
from general_manager.measurement import Measurement
from general_manager.manager.general_manager import GeneralManager
from general_manager.bucket.base_bucket import (
    Bucket,
    GeneralManagerType,
)
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import Dependency


def _is_stable_group_data(data: object) -> bool:
    """Return whether group entries can be reused across aggregate reads."""
    return type(data) is tuple or bool(
        getattr(data, "_group_materialization_safe", False)
    )


def _freeze_manager_value(value: object) -> object:
    """Return a hashable representation for manager-backed group state."""
    if isinstance(value, GeneralManager):
        return tuple(
            sorted(
                (
                    key,
                    _freeze_manager_value(identifier),
                )
                for key, identifier in value.identification.items()
            )
        )
    if isinstance(value, dict):
        frozen_items = (
            (
                _freeze_manager_value(key),
                _freeze_manager_value(item),
            )
            for key, item in value.items()
        )
        return tuple(
            sorted(
                frozen_items,
                key=lambda entry: (type(entry[0]).__name__, repr(entry[0])),
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_manager_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_manager_value(item) for item in value)
    return value


class MissingGroupAttributeError(AttributeError):
    """Raised when a GroupManager access attempts to use an undefined attribute."""

    def __init__(self, manager_name: str, attribute: str) -> None:
        """
        Initialize the exception indicating that a GroupManager attempted to access an undefined attribute.

        Parameters:
            manager_name (str): Name of the manager where the attribute access occurred.
            attribute (str): The missing attribute name that was accessed.
        """
        super().__init__(f"{manager_name} has no attribute {attribute}.")


class GroupManager(Generic[GeneralManagerType]):
    """Represent aggregated results for grouped GeneralManager records."""

    def __init__(
        self,
        manager_class: type[GeneralManagerType],
        group_by_value: dict[str, object],
        data: Bucket[GeneralManagerType],
    ) -> None:
        """
        Initialise a grouped manager with the underlying bucket and grouping keys.

        Parameters:
            manager_class: Manager subclass whose records were grouped.
            group_by_value: Grouping key values describing this group.
            data: Bucket of records belonging to the group.

        Returns:
            None
        """
        self._manager_class = manager_class
        self._group_by_value = group_by_value
        self._data = data
        self._grouped_data: dict[str, object] = {}
        self._materialized = _is_stable_group_data(data)
        self._entries_snapshot: tuple[GeneralManagerType, ...] | None = None
        self._captured_dependencies: frozenset[Dependency] = frozenset(
            getattr(data, "_dependencies", ())
        )
        self._frozen_entries: frozenset[object] | None = None

    def _replay_dependencies(self) -> None:
        if self._materialized:
            DependencyTracker._track_many_validated(self._captured_dependencies)

    def _entries_for_read(self) -> Iterable[GeneralManagerType]:
        if not self._materialized:
            return self._data
        if self._entries_snapshot is None:
            with DependencyTracker() as captured_dependencies:
                entries = tuple(self._data)
            self._entries_snapshot = entries
            self._captured_dependencies = frozenset(
                (*self._captured_dependencies, *captured_dependencies)
            )
        self._replay_dependencies()
        return self._entries_snapshot

    def _frozen_entry_values(self) -> frozenset[object]:
        if self._materialized and self._frozen_entries is not None:
            self._replay_dependencies()
            return self._frozen_entries
        frozen_entries = frozenset(
            _freeze_manager_value(entry) for entry in self._entries_for_read()
        )
        if self._materialized:
            self._frozen_entries = frozen_entries
        return frozen_entries

    def __hash__(self) -> int:
        """
        Return a hash based on the manager class, group keys, and grouped data.

        Manager instances, mappings, lists, tuples, and sets are recursively
        frozen before hashing. Mapping entries are sorted by their frozen
        key/value tuples; sets become `frozenset` values. The resulting hash
        follows normal Python hash
        stability rules: it is suitable for the lifetime of unchanged group
        state, but it is not a cross-process persistent identifier and can
        change if mutable grouped data changes after construction.

        Returns:
            int: Hash value combining class, keys, and data.
        """
        return hash(
            (
                self._manager_class,
                _freeze_manager_value(self._group_by_value),
                self._frozen_entry_values(),
            )
        )

    def __eq__(self, other: object) -> bool:
        """
        Compare grouped managers by manager class, keys, and grouped data.

        Parameters:
            other (object): Object to compare against.

        Returns:
            bool: True when both grouped managers describe the same data.
        """
        return (
            isinstance(other, self.__class__)
            and self._manager_class == other._manager_class
            and self._group_by_value == other._group_by_value
            and self._frozen_entry_values() == other._frozen_entry_values()
        )

    def __repr__(self) -> str:
        """
        Return a debug representation showing grouped keys and data.

        Returns:
            Debug string in the form
            `"GroupManager(<manager_class>, <group_by_value>, <data>)"`.
        """
        return f"{self.__class__.__name__}({self._manager_class}, {self._group_by_value}, {self._data})"

    def __iter__(self) -> Iterator[tuple[str, object]]:
        """
        Iterate over attribute names and their aggregated values.

        Yields:
            Attribute name and aggregated value pairs. Interface attributes are
            the keys returned by `manager_class.Interface.get_attributes()` and
            are yielded in that mapping's iteration order. `GraphQLProperty`
            values declared directly on the manager class are yielded after
            interface attributes in class `__dict__` order. Duplicate names are
            not filtered.
        """
        for attribute in self._manager_class.Interface.get_attributes().keys():
            yield attribute, getattr(self, attribute)
        for attribute, attr_value in self._manager_class.__dict__.items():
            if isinstance(attr_value, GraphQLProperty):
                yield attribute, getattr(self, attribute)

    def __getattr__(self, item: str) -> object:
        """
        Lazily compute aggregated attribute values when accessed.

        Parameters:
            item (str): Attribute name requested by the caller.

        Returns:
            Group-by key value or cached aggregate for the requested attribute.
            Cached aggregate values are stored in the private `_grouped_data`
            dictionary under the requested attribute name and are not
            invalidated if the underlying bucket or `group_by_value` mapping is
            mutated after first access.

        Raises:
            MissingGroupAttributeError: If the attribute cannot be resolved
                from group metadata or a `GraphQLProperty` return annotation.
            Exception: Exceptions raised while iterating the underlying bucket
                or reading grouped record attributes propagate unchanged.
        """
        group_by_value = self.__dict__.get("_group_by_value")
        if group_by_value is None:
            raise AttributeError(item)
        if item in group_by_value:
            return group_by_value[item]
        grouped_data = self.__dict__.get("_grouped_data")
        if grouped_data is None:
            raise AttributeError(item)
        if item not in grouped_data:
            grouped_data[item] = self.combine_value(item)
        else:
            self._replay_dependencies()
        return grouped_data[item]

    def combine_value(self, item: str) -> object:
        """
        Aggregate the values of a named attribute across all records in the group.

        Parameters:
            item (str): Attribute name to aggregate from each grouped record.

        Returns:
            Aggregated value for `item`: group `"id"`, empty buckets, and
            all-`None` values return `None`; bucket/manager values are unioned
            with `|`; lists are concatenated; dicts are merged with later values
            overwriting earlier keys; strings are deduplicated in encounter
            order and joined by `", "`; booleans use `any()` before numeric
            handling; numeric and `Measurement` values are summed;
            datetime/date/time values use `max()`. The aggregation branch is
            selected from interface metadata or a concrete `GraphQLProperty`
            return annotation, not from each runtime value, so mixed runtime
            values follow the selected branch and may raise from that operation.

        Raises:
            MissingGroupAttributeError: If the attribute does not exist or its
                type cannot be determined on the manager. `GraphQLProperty`
                annotations use the first `typing.get_args()` entry when
                present, otherwise the annotation object itself; unsupported
                non-class annotations raise this error.
            Exception: Exceptions raised while reading grouped record
                attributes, unioning bucket/manager values, merging containers,
                summing values, or comparing date/time values propagate
                unchanged.
        """
        if item == "id":
            return None

        attribute_types = self._manager_class.Interface.get_attribute_types()
        attr_info = attribute_types.get(item)
        data_type = attr_info["type"] if attr_info else None
        if data_type is None and item in self._manager_class.__dict__:
            attr_value = self._manager_class.__dict__[item]
            if isinstance(attr_value, GraphQLProperty):
                type_hints = get_args(attr_value.graphql_type_hint)
                data_type = (
                    type_hints[0]
                    if type_hints
                    else cast(type, attr_value.graphql_type_hint)
                )
        if data_type is None or not isinstance(data_type, type):
            raise MissingGroupAttributeError(self.__class__.__name__, item)

        entries = self._entries_for_read()
        if issubclass(data_type, (Bucket, GeneralManager)):
            new_data: object = None
            for entry in entries:
                value = getattr(entry, item)
                if value is None:
                    continue
                if new_data is None:
                    new_data = value
                else:
                    new_data = value | new_data
            return new_data
        if issubclass(data_type, list):
            list_data: list[object] = []
            saw_value = False
            for entry in entries:
                value = getattr(entry, item)
                if value is not None:
                    saw_value = True
                    list_data.extend(cast(list[object], value))
            return list_data if saw_value else None
        if issubclass(data_type, dict):
            dict_data: dict[object, object] = {}
            saw_value = False
            for entry in entries:
                value = getattr(entry, item)
                if value is not None:
                    saw_value = True
                    dict_data.update(cast(dict[object, object], value))
            return dict_data if saw_value else None
        if issubclass(data_type, str):
            text_data: list[str] = []
            seen_text: set[str] = set()
            saw_value = False
            for entry in entries:
                value = getattr(entry, item)
                if value is None:
                    continue
                saw_value = True
                text_value = str(value)
                if text_value not in seen_text:
                    seen_text.add(text_value)
                    text_data.append(text_value)
            return ", ".join(text_data) if saw_value else None
        if issubclass(data_type, bool):
            saw_value = False
            result = False
            for entry in entries:
                value = getattr(entry, item)
                if value is not None:
                    saw_value = True
                    result = result or bool(value)
            return result if saw_value else None
        if issubclass(data_type, (int, float, Measurement)):
            saw_value = False
            numeric_result: object = 0
            for entry in entries:
                value = getattr(entry, item)
                if value is not None:
                    saw_value = True
                    numeric_result = numeric_result + value
            return numeric_result if saw_value else None
        if issubclass(data_type, (datetime, date, time)):
            temporal_result: object = None
            for entry in entries:
                value = getattr(entry, item)
                if value is not None and (
                    temporal_result is None or value > temporal_result
                ):
                    temporal_result = value
            return temporal_result
        for entry in entries:
            getattr(entry, item)
        return None
