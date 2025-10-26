"""Utility manager that aggregates grouped GeneralManager data."""

from __future__ import annotations
from typing import Any, Generic, Iterator, Type, cast, get_args
from datetime import datetime, date, time
from general_manager.api.property import GraphQLProperty
from general_manager.measurement import Measurement
from general_manager.manager.general_manager import GeneralManager
from general_manager.bucket.base_bucket import (
    Bucket,
    GeneralManagerType,
)


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
        manager_class: Type[GeneralManagerType],
        group_by_value: dict[str, Any],
        data: Bucket[GeneralManagerType],
    ) -> None:
        """
        Initialise a grouped manager with the underlying bucket and grouping keys.

        Parameters:
            manager_class (type[GeneralManagerType]): Manager subclass whose records were grouped.
            group_by_value (dict[str, Any]): Key values describing this group.
            data (Bucket[GeneralManagerType]): Bucket of records belonging to the group.

        Returns:
            None
        """
        self._manager_class = manager_class
        self._group_by_value = group_by_value
        self._data = data
        self._grouped_data: dict[str, Any] = {}

    def __hash__(self) -> int:
        """
        Return a stable hash based on the manager class, keys, and grouped data.

        Returns:
            int: Hash value combining class, keys, and data.
        """
        return hash(
            (
                self._manager_class,
                tuple(self._group_by_value.items()),
                frozenset(self._data),
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
            and frozenset(self._data) == frozenset(other._data)
        )

    def __repr__(self) -> str:
        """
        Return a debug representation showing grouped keys and data.

        Returns:
            str: Debug string summarising the grouped manager.
        """
        return f"{self.__class__.__name__}({self._manager_class}, {self._group_by_value}, {self._data})"

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        """
        Iterate over attribute names and their aggregated values.

        Yields:
            tuple[str, Any]: Attribute name and aggregated value pairs.
        """
        for attribute in self._manager_class.Interface.get_attributes().keys():
            yield attribute, getattr(self, attribute)
        for attribute, attr_value in self._manager_class.__dict__.items():
            if isinstance(attr_value, GraphQLProperty):
                yield attribute, getattr(self, attribute)

    def __getattr__(self, item: str) -> Any:
        """
        Lazily compute aggregated attribute values when accessed.

        Parameters:
            item (str): Attribute name requested by the caller.

        Returns:
            Any: Aggregated value stored for the given attribute.

        Raises:
            AttributeError: If the attribute cannot be resolved from group data.
        """
        if item in self._group_by_value:
            return self._group_by_value[item]
        if item not in self._grouped_data.keys():
            self._grouped_data[item] = self.combine_value(item)
        return self._grouped_data[item]

    def combine_value(self, item: str) -> Any:
        """
        Aggregate the values of a named attribute across all records in the group.

        Parameters:
            item (str): Attribute name to aggregate from each grouped record.

        Returns:
            Any: The aggregated value for `item` according to its type (e.g., merged Bucket/GeneralManager, concatenated list, merged dict, deduplicated comma-separated string, boolean OR, numeric sum, or latest datetime). Returns `None` if all values are `None` or if `item` is `"id"`.

        Raises:
            MissingGroupAttributeError: If the attribute does not exist or its type cannot be determined on the manager.
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

        total_data = []
        for entry in self._data:
            total_data.append(getattr(entry, item))

        new_data: Any = None
        if all([i is None for i in total_data]):
            return new_data
        total_data = [i for i in total_data if i is not None]

        if issubclass(data_type, (Bucket, GeneralManager)):
            for entry in total_data:
                if new_data is None:
                    new_data = entry
                else:
                    new_data = entry | new_data
        elif issubclass(data_type, list):
            new_data = []
            for entry in total_data:
                new_data.extend(entry)
        elif issubclass(data_type, dict):
            new_data = {}
            for entry in total_data:
                new_data.update(entry)
        elif issubclass(data_type, str):
            temp_data = []
            for entry in total_data:
                if entry not in temp_data:
                    temp_data.append(str(entry))
            new_data = ", ".join(temp_data)
        elif issubclass(data_type, bool):
            new_data = any(total_data)
        elif issubclass(data_type, (int, float, Measurement)):
            new_data = sum(total_data)
        elif issubclass(data_type, (datetime, date, time)):
            new_data = max(total_data)

        return new_data
