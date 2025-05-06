from __future__ import annotations
from typing import (
    Type,
    Generator,
    Any,
    Generic,
    get_args,
    cast,
)
import json
from datetime import datetime, date, time
from general_manager.api.graphql import GraphQLProperty
from general_manager.measurement import Measurement
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.baseInterface import (
    Bucket,
    GeneralManagerType,
)


class GroupBucket(Bucket[GeneralManagerType]):

    def __init__(
        self,
        manager_class: Type[GeneralManagerType],
        group_by_keys: tuple[str, ...],
        data: Bucket[GeneralManagerType],
    ):
        super().__init__(manager_class)
        self.__checkGroupByArguments(group_by_keys)
        self._group_by_keys = group_by_keys
        self._data = self.__buildGroupedManager(data)
        self._basis_data = data

    def __checkGroupByArguments(self, group_by_keys: tuple[str, ...]) -> None:
        """
        This method checks if the given arguments are valid for the groupBy method.
        It raises a TypeError if the arguments are not valid.
        """
        if not all(isinstance(arg, str) for arg in group_by_keys):
            raise TypeError("groupBy() argument must be a string")
        if not all(
            arg in self._manager_class.Interface.getAttributes().keys()
            for arg in group_by_keys
        ):
            raise TypeError(
                f"groupBy() argument must be a valid attribute of {self._manager_class.__name__}"
            )

    def __buildGroupedManager(
        self,
        data: Bucket[GeneralManagerType],
    ) -> list[GroupedManager[GeneralManagerType]]:
        """
        This method builds the grouped manager.
        It returns a GroupBucket with the grouped data.
        """
        group_by_values = set()
        for entry in data:
            group_by_value = {}
            for arg in self._group_by_keys:
                group_by_value[arg] = getattr(entry, arg)
            group_by_values.add(json.dumps(group_by_value))

        groups = []
        for group_by_value in group_by_values:
            group_by_value = json.loads(group_by_value)
            grouped_manager_objects = data.filter(**group_by_value)
            groups.append(
                GroupedManager(
                    self._manager_class, group_by_value, grouped_manager_objects
                )
            )
        return groups

    def __or__(self, other: object) -> GroupBucket[GeneralManagerType]:
        if not isinstance(other, self.__class__):
            raise ValueError("Cannot combine different bucket types")
        if self._manager_class != other._manager_class:
            raise ValueError("Cannot combine different manager classes")
        return GroupBucket(
            self._manager_class,
            self._group_by_keys,
            self._basis_data | other._basis_data,
        )

    def __iter__(self) -> Generator[GroupedManager[GeneralManagerType]]:
        for grouped_manager in self._data:
            yield grouped_manager

    def filter(self, **kwargs: Any) -> GroupBucket[GeneralManagerType]:
        new_basis_data = self._basis_data.filter(**kwargs)
        return GroupBucket(
            self._manager_class,
            self._group_by_keys,
            new_basis_data,
        )

    def exclude(self, **kwargs: Any) -> GroupBucket[GeneralManagerType]:
        new_basis_data = self._basis_data.exclude(**kwargs)
        return GroupBucket(
            self._manager_class,
            self._group_by_keys,
            new_basis_data,
        )

    def first(self) -> GroupedManager[GeneralManagerType] | None:
        try:
            return next(iter(self))
        except StopIteration:
            return None

    def last(self) -> GroupedManager[GeneralManagerType] | None:
        items = list(self)
        if items:
            return items[-1]
        return None

    def count(self) -> int:
        return sum(1 for _ in self)

    def all(self) -> Bucket[GeneralManagerType]:
        return self

    def get(self, **kwargs: Any) -> GroupedManager[GeneralManagerType]:
        first_value = self.filter(**kwargs).first()
        if first_value is None:
            raise ValueError(
                f"Cannot find {self._manager_class.__name__} with {kwargs}"
            )
        return first_value

    def __getitem__(
        self, item: int | slice
    ) -> GroupedManager[GeneralManagerType] | GroupBucket[GeneralManagerType]:
        if isinstance(item, int):
            return self._data[item]
        elif isinstance(item, slice):
            new_data = self._data[item]
            new_base_data = None
            for manager in new_data:
                if new_base_data is None:
                    new_base_data = manager._data
                else:
                    new_base_data = new_base_data | manager._data
            if new_base_data is None:
                raise ValueError("Cannot slice an empty GroupBucket")
            return GroupBucket(self._manager_class, self._group_by_keys, new_base_data)
        raise TypeError(f"Invalid argument type: {type(item)}. Expected int or slice.")

    def __len__(self) -> int:
        return self.count()

    def __contains__(self, item: GeneralManagerType) -> bool:
        return item in self._basis_data

    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> Bucket[GeneralManagerType]:
        if isinstance(key, str):
            key = (key,)
        if reverse:
            sorted_data = self._data.sort(
                key=lambda x: tuple([-getattr(x, k) for k in key])
            )
        else:
            sorted_data = self._data.sort(
                key=lambda x: tuple([getattr(x, k) for k in key])
            )

        new_bucket = GroupBucket(
            self._manager_class, self._group_by_keys, self._basis_data
        )
        new_bucket._data = sorted_data
        return new_bucket

    def group_by(self, *group_by_keys: str) -> GroupBucket[GeneralManagerType]:
        """
        This method groups the data by the given arguments.
        It returns a GroupBucket with the grouped data.
        """
        return GroupBucket(
            self._manager_class, tuple([*self._group_by_keys, *group_by_keys]), self
        )


class GroupedManager(Generic[GeneralManagerType]):
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

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, self.__class__):
            return False
        return (
            self._data == other._data
            and self._manager_class == other._manager_class
            and self._group_by_value == other._group_by_value
        )

    def __hash__(self) -> int:
        return hash(
            (self._manager_class, frozenset(self._group_by_value.items()), self._data)
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
            new_data = " ".join(total_data)
        elif issubclass(data_type, (int, float, Measurement)):
            new_data = sum(total_data)
        elif issubclass(data_type, (datetime, date, time)):
            new_data = max(total_data)
        elif issubclass(data_type, bool):
            new_data = any(total_data)

        return new_data
