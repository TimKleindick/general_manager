from __future__ import annotations
from typing import (
    Type,
    Generator,
    Any,
)
import json
from general_manager.manager.groupManager import GroupManager
from general_manager.bucket.baseBucket import (
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
        """
        Initializes a GroupBucket by grouping data based on specified attribute keys.

        Args:
            manager_class: The class type of the manager objects to be grouped.
            group_by_keys: Tuple of attribute names to group the data by.
            data: The underlying Bucket containing manager instances to be grouped.

        Raises:
            TypeError: If any group-by key is not a string.
            ValueError: If any group-by key is not a valid attribute of the manager class.
        """
        super().__init__(manager_class)
        self.__checkGroupByArguments(group_by_keys)
        self._group_by_keys = group_by_keys
        self._data = self.__buildGroupedManager(data)
        self._basis_data = data

    def __eq__(self, other: object) -> bool:
        """
        Checks whether this GroupBucket is equal to another by comparing grouped data, manager class, and group-by keys.

        Returns:
            True if both instances have identical grouped data, manager class, and group-by keys; otherwise, False.
        """
        if not isinstance(other, self.__class__):
            return False
        return (
            set(self._data) == set(other._data)
            and self._manager_class == other._manager_class
            and self._group_by_keys == other._group_by_keys
        )

    def __checkGroupByArguments(self, group_by_keys: tuple[str, ...]) -> None:
        """
        Checks that each group-by key is a string and a valid attribute of the manager class.

        Raises:
            TypeError: If any group-by key is not a string.
            ValueError: If any group-by key is not a valid attribute of the manager class.
        """
        if not all(isinstance(arg, str) for arg in group_by_keys):
            raise TypeError("groupBy() arguments must be a strings")
        if not all(
            arg in self._manager_class.Interface.getAttributes()
            for arg in group_by_keys
        ):
            raise ValueError(
                f"groupBy() argument must be a valid attribute of {self._manager_class.__name__}"
            )

    def __buildGroupedManager(
        self,
        data: Bucket[GeneralManagerType],
    ) -> list[GroupManager[GeneralManagerType]]:
        """
        Constructs a list of GroupManager instances, each representing a unique group of entries from the provided data bucket based on the current group-by keys.

        Args:
            data: The bucket of manager instances to be grouped.

        Returns:
            A list of GroupManager objects, each corresponding to a unique combination of group-by attribute values found in the data.
        """
        group_by_values: set[tuple[tuple[str, Any], ...]] = set()
        for entry in data:
            key = tuple((arg, getattr(entry, arg)) for arg in self._group_by_keys)
            group_by_values.add(key)

        groups = []
        for group_by_value in sorted(group_by_values, key=str):
            group_by_dict = {key: value for key, value in group_by_value}
            grouped_manager_objects = data.filter(**group_by_dict)
            groups.append(
                GroupManager(
                    self._manager_class, group_by_dict, grouped_manager_objects
                )
            )
        return groups

    def __or__(self, other: object) -> GroupBucket[GeneralManagerType]:
        """
        Returns a new GroupBucket representing the union of this bucket and another, combining their underlying data.

        Raises:
            ValueError: If the other object is not a GroupBucket of the same type or uses a different manager class.
        """
        if not isinstance(other, self.__class__):
            raise ValueError("Cannot combine different bucket types")
        if self._manager_class != other._manager_class:
            raise ValueError("Cannot combine different manager classes")
        return GroupBucket(
            self._manager_class,
            self._group_by_keys,
            self._basis_data | other._basis_data,
        )

    def __iter__(self) -> Generator[GroupManager[GeneralManagerType], None, None]:
        """
        Yields each grouped manager in the current GroupBucket.

        Returns:
            A generator yielding GroupManager instances representing each group.
        """
        yield from self._data

    def filter(self, **kwargs: Any) -> GroupBucket[GeneralManagerType]:
        """
        Returns a new GroupBucket containing only the entries from the underlying data that match the specified filter criteria.

        Keyword arguments correspond to attribute-value pairs used for filtering.
        """
        new_basis_data = self._basis_data.filter(**kwargs)
        return GroupBucket(
            self._manager_class,
            self._group_by_keys,
            new_basis_data,
        )

    def exclude(self, **kwargs: Any) -> GroupBucket[GeneralManagerType]:
        """
        Returns a new GroupBucket excluding entries from the underlying data that match the given criteria.

        Keyword arguments specify attribute-value pairs to exclude from the basis data. The resulting GroupBucket retains the same grouping keys and manager class.
        """
        new_basis_data = self._basis_data.exclude(**kwargs)
        return GroupBucket(
            self._manager_class,
            self._group_by_keys,
            new_basis_data,
        )

    def first(self) -> GroupManager[GeneralManagerType] | None:
        """
        Returns the first grouped manager in the collection, or None if the collection is empty.
        """
        try:
            return next(iter(self))
        except StopIteration:
            return None

    def last(self) -> GroupManager[GeneralManagerType] | None:
        """
        Returns the last grouped manager in the collection, or None if the collection is empty.
        """
        items = list(self)
        if items:
            return items[-1]
        return None

    def count(self) -> int:
        """
        Returns the number of grouped managers in the bucket.
        """
        return sum(1 for _ in self)

    def all(self) -> Bucket[GeneralManagerType]:
        """
        Returns the current GroupBucket instance.

        This method provides compatibility with interfaces expecting an `all()` method to retrieve the full collection.
        """
        return self

    def get(self, **kwargs: Any) -> GroupManager[GeneralManagerType]:
        """
        Returns the first grouped manager matching the specified filter criteria.

        Args:
            **kwargs: Attribute-value pairs to filter grouped managers.

        Returns:
            The first GroupManager instance matching the filter criteria.

        Raises:
            ValueError: If no grouped manager matches the provided criteria.
        """
        first_value = self.filter(**kwargs).first()
        if first_value is None:
            raise ValueError(
                f"Cannot find {self._manager_class.__name__} with {kwargs}"
            )
        return first_value

    def __getitem__(
        self, item: int | slice
    ) -> GroupManager[GeneralManagerType] | GroupBucket[GeneralManagerType]:
        """
        Returns a grouped manager by index or a new GroupBucket by slice.

        If an integer index is provided, returns the corresponding GroupManager. If a slice is provided, returns a new GroupBucket containing the union of the basis data from the selected groups.

        Raises:
            ValueError: If slicing results in no groups.
            TypeError: If the argument is not an int or slice.
        """
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
        """
        Returns the number of grouped managers in the GroupBucket.
        """
        return self.count()

    def __contains__(self, item: GeneralManagerType) -> bool:
        """
        Checks if the given manager instance is present in the underlying basis data.

        Args:
            item: The manager instance to check for membership.

        Returns:
            True if the item exists in the basis data; otherwise, False.
        """
        return item in self._basis_data

    def sort(
        self,
        key: tuple[str, ...] | str,
        reverse: bool = False,
    ) -> Bucket[GeneralManagerType]:
        """
        Returns a new GroupBucket with grouped managers sorted by the specified attribute keys.

        Args:
            key: A string or tuple of strings specifying the attribute(s) to sort by.
            reverse: If True, sorts in descending order. Defaults to False.

        Returns:
            A new GroupBucket instance with grouped managers sorted by the given keys.
        """
        if isinstance(key, str):
            key = (key,)
        if reverse:
            sorted_data = sorted(
                self._data,
                key=lambda x: tuple(getattr(x, k) for k in key),
                reverse=True,
            )
        else:
            sorted_data = sorted(
                self._data, key=lambda x: tuple(getattr(x, k) for k in key)
            )

        new_bucket = GroupBucket(
            self._manager_class, self._group_by_keys, self._basis_data
        )
        new_bucket._data = sorted_data
        return new_bucket

    def group_by(self, *group_by_keys: str) -> GroupBucket[GeneralManagerType]:
        """
        Returns a new GroupBucket grouped by the current and additional specified attribute keys.

        Additional group-by keys are appended to the existing grouping, and the new GroupBucket is constructed from the same underlying data.
        """
        return GroupBucket(
            self._manager_class,
            tuple([*self._group_by_keys, *group_by_keys]),
            self._basis_data,
        )
