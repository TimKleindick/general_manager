from __future__ import annotations
from typing import (
    Type,
    Any,
    Generic,
    get_args,
    cast,
)
from datetime import datetime, date, time
from general_manager.api.graphql import GraphQLProperty
from general_manager.measurement import Measurement
from general_manager.manager.generalManager import GeneralManager
from general_manager.bucket.baseBucket import (
    Bucket,
    GeneralManagerType,
)


class GroupManager(Generic[GeneralManagerType]):
    """
    This class is used to group the data of a GeneralManager.
    It is used to create a new GeneralManager with the grouped data.
    """

    def __init__(
        self,
        manager_class: Type[GeneralManagerType],
        group_by_value: dict[str, Any],
        data: Bucket[GeneralManagerType],
    ):
        self._manager_class = manager_class
        self._group_by_value = group_by_value
        self._data = data
        self._grouped_data: dict[str, Any] = {}

    def __hash__(self) -> int:
        return hash(
            (
                self._manager_class,
                tuple(self._group_by_value.items()),
                frozenset(self._data),
            )
        )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, self.__class__)
            and self._manager_class == other._manager_class
            and self._group_by_value == other._group_by_value
            and frozenset(self._data) == frozenset(other._data)
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._manager_class}, {self._group_by_value}, {self._data})"

    def __iter__(self):
        for attribute in self._manager_class.Interface.getAttributes().keys():
            yield attribute, getattr(self, attribute)
        for attribute, attr_value in self._manager_class.__dict__.items():
            if isinstance(attr_value, GraphQLProperty):
                yield attribute, getattr(self, attribute)

    def __getattr__(self, item: str) -> Any:
        if item in self._group_by_value:
            return self._group_by_value[item]
        if item not in self._grouped_data.keys():
            self._grouped_data[item] = self.combineValue(item)
        return self._grouped_data[item]

    def combineValue(self, item: str) -> Any:
        if item == "id":
            return None

        data_type = (
            self._manager_class.Interface.getAttributeTypes().get(item, {}).get("type")
        )
        if data_type is None and item in self._manager_class.__dict__:
            attr_value = self._manager_class.__dict__[item]
            if isinstance(attr_value, GraphQLProperty):
                type_hints = get_args(attr_value.graphql_type_hint)
                data_type = (
                    type_hints[0]
                    if type_hints
                    else cast(type, attr_value.graphql_type_hint)
                )
        if data_type is None:
            raise AttributeError(f"{self.__class__.__name__} has no attribute {item}")

        total_data = []
        for entry in self._data:
            total_data.append(getattr(entry, item))

        new_data = None
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
