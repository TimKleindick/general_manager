"""Utility manager that aggregates grouped GeneralManager data."""

from __future__ import annotations
from collections.abc import Iterator
from typing import Generic, cast, get_args
from datetime import datetime, date, time
from general_manager.api.property import GraphQLProperty
from general_manager.measurement import Measurement
from general_manager.manager.general_manager import GeneralManager
from general_manager.bucket.base_bucket import (
    Bucket,
    GeneralManagerType,
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
        return tuple(
            sorted(
                (
                    _freeze_manager_value(key),
                    _freeze_manager_value(item),
                )
                for key, item in value.items()
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
                frozenset(_freeze_manager_value(entry) for entry in self._data),
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
            and frozenset(_freeze_manager_value(entry) for entry in self._data)
            == frozenset(_freeze_manager_value(entry) for entry in other._data)
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
        if item in self._group_by_value:
            return self._group_by_value[item]
        if item not in self._grouped_data:
            self._grouped_data[item] = self.combine_value(item)
        return self._grouped_data[item]

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

        total_data: list[object] = []
        for entry in self._data:
            total_data.append(getattr(entry, item))

        new_data: object = None
        if all(i is None for i in total_data):
            return new_data
        total_data = [i for i in total_data if i is not None]

        if issubclass(data_type, (Bucket, GeneralManager)):
            for value in total_data:
                if new_data is None:
                    new_data = value
                else:
                    new_data = value | new_data  # type: ignore[operator]
        elif issubclass(data_type, list):
            list_data: list[object] = []
            for value in total_data:
                list_data.extend(cast(list[object], value))
            new_data = list_data
        elif issubclass(data_type, dict):
            dict_data: dict[object, object] = {}
            for value in total_data:
                dict_data.update(cast(dict[object, object], value))
            new_data = dict_data
        elif issubclass(data_type, str):
            text_data: list[str] = []
            for value in total_data:
                text_value = str(value)
                if text_value not in text_data:
                    text_data.append(text_value)
            new_data = ", ".join(text_data)
        elif issubclass(data_type, bool):
            new_data = any(total_data)
        elif issubclass(data_type, (int, float, Measurement)):
            new_data = sum(cast(list[int | float | Measurement], total_data))
        elif issubclass(data_type, (datetime, date, time)):
            new_data = max(cast(list[datetime | date | time], total_data))

        return new_data
