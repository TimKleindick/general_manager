"""Grouping bucket implementation for aggregating GeneralManager instances."""

from __future__ import annotations
from typing import (
    Type,
    Generator,
    Any,
)
from general_manager.manager.groupManager import GroupManager
from general_manager.bucket.baseBucket import (
    Bucket,
    GeneralManagerType,
)


class GroupBucket(Bucket[GeneralManagerType]):
    """Bucket variant that groups managers by specified attributes."""

    def __init__(
        self,
        manager_class: Type[GeneralManagerType],
        group_by_keys: tuple[str, ...],
        data: Bucket[GeneralManagerType],
    ) -> None:
        """
        Build a grouping bucket from the provided base data.

        Parameters:
            manager_class (type[GeneralManagerType]): GeneralManager subclass represented by the bucket.
            group_by_keys (tuple[str, ...]): Attribute names used to define each group.
            data (Bucket[GeneralManagerType]): Source bucket whose entries are grouped.

        Returns:
            None

        Raises:
            TypeError: If a group-by key is not a string.
            ValueError: If a group-by key is not a valid manager attribute.
        """
        super().__init__(manager_class)
        self.__checkGroupByArguments(group_by_keys)
        self._group_by_keys = group_by_keys
        self._data: list[GroupManager[GeneralManagerType]] = self.__buildGroupedManager(
            data
        )
        self._basis_data: Bucket[GeneralManagerType] = data

    def __eq__(self, other: object) -> bool:
        """
        Compare two grouping buckets for equality.

        Parameters:
            other (object): Object compared against the current bucket.

        Returns:
            bool: True when grouped data, manager class, and grouping keys match.
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
        Validate the supplied group-by keys.

        Parameters:
            group_by_keys (tuple[str, ...]): Attribute names requested for grouping.

        Returns:
            None

        Raises:
            TypeError: If a key is not a string.
            ValueError: If a key is not an attribute exposed by the manager interface.
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
        Construct grouped manager objects for every unique combination of key values.

        Parameters:
            data (Bucket[GeneralManagerType]): Source bucket that will be partitioned by the configured keys.

        Returns:
            list[GroupManager[GeneralManagerType]]: Group managers covering all key combinations.
        """
        group_by_values: set[tuple[tuple[str, Any], ...]] = set()
        for entry in data:
            key = tuple((arg, getattr(entry, arg)) for arg in self._group_by_keys)
            group_by_values.add(key)

        groups: list[GroupManager[GeneralManagerType]] = []
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
        Combine two grouping buckets produced from the same manager class.

        Parameters:
            other (object): Another grouping bucket to merge.

        Returns:
            GroupBucket[GeneralManagerType]: Bucket representing the union of both inputs.

        Raises:
            ValueError: If `other` is not a compatible GroupBucket instance.
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
        Iterate over the grouped managers produced by this bucket.

        Yields:
            GroupManager[GeneralManagerType]: Individual group manager instances.
        """
        yield from self._data

    def filter(self, **kwargs: Any) -> GroupBucket[GeneralManagerType]:
        """
        Return a grouped bucket filtered by the provided lookups.

        Parameters:
            **kwargs: Field lookups evaluated against the underlying bucket.

        Returns:
            GroupBucket[GeneralManagerType]: Grouped bucket containing only matching records.
        """
        new_basis_data = self._basis_data.filter(**kwargs)
        return GroupBucket(
            self._manager_class,
            self._group_by_keys,
            new_basis_data,
        )

    def exclude(self, **kwargs: Any) -> GroupBucket[GeneralManagerType]:
        """
        Return a grouped bucket that excludes records matching the provided lookups.

        Parameters:
            **kwargs: Field lookups whose matches should be removed from the underlying bucket.

        Returns:
            GroupBucket[GeneralManagerType]: Grouped bucket built from the filtered base data.
        """
        new_basis_data = self._basis_data.exclude(**kwargs)
        return GroupBucket(
            self._manager_class,
            self._group_by_keys,
            new_basis_data,
        )

    def first(self) -> GroupManager[GeneralManagerType] | None:
        """
        Return the first grouped manager in the collection.

        Returns:
            GroupManager[GeneralManagerType] | None: First group when available.
        """
        try:
            return next(iter(self))
        except StopIteration:
            return None

    def last(self) -> GroupManager[GeneralManagerType] | None:
        """
        Return the last grouped manager in the collection.

        Returns:
            GroupManager[GeneralManagerType] | None: Last group when available.
        """
        items = list(self)
        if items:
            return items[-1]
        return None

    def count(self) -> int:
        """
        Count the number of grouped managers in the bucket.

        Returns:
            int: Number of groups.
        """
        return sum(1 for _ in self)

    def all(self) -> Bucket[GeneralManagerType]:
        """
        Return the current grouping bucket.

        Returns:
            Bucket[GeneralManagerType]: This instance.
        """
        return self

    def get(self, **kwargs: Any) -> GroupManager[GeneralManagerType]:
        """
        Retrieve the first grouped manager matching the supplied filters.

        Parameters:
            **kwargs: Field lookups applied to the grouped data.

        Returns:
            GroupManager[GeneralManagerType]: Matching grouped manager.

        Raises:
            ValueError: If no grouped manager matches the filters.
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
        Access a specific group or a slice of groups.

        Parameters:
            item (int | slice): Index or slice describing the desired groups.

        Returns:
            GroupManager[GeneralManagerType] | GroupBucket[GeneralManagerType]:
                Group at the specified index or a new bucket built from the selected groups.

        Raises:
            ValueError: If the requested slice contains no groups.
            TypeError: If the argument is not an integer or slice.
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
        Return the number of grouped managers.

        Returns:
            int: Number of groups.
        """
        return self.count()

    def __contains__(self, item: GeneralManagerType) -> bool:
        """
        Determine whether the given manager instance exists in the underlying data.

        Parameters:
            item (GeneralManagerType): Manager instance checked for membership.

        Returns:
            bool: True if the instance is present in the basis data.
        """
        return item in self._basis_data

    def sort(
        self,
        key: tuple[str, ...] | str,
        reverse: bool = False,
    ) -> Bucket[GeneralManagerType]:
        """
        Return a new GroupBucket sorted by the specified attributes.

        Parameters:
            key (str | tuple[str, ...]): Attribute name(s) used for sorting.
            reverse (bool): Whether to apply descending order.

        Returns:
            Bucket[GeneralManagerType]: Sorted grouping bucket.
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
        Extend the grouping with additional attribute keys.

        Parameters:
            *group_by_keys (str): Attribute names appended to the current grouping.

        Returns:
            GroupBucket[GeneralManagerType]: New bucket grouped by the combined key set.
        """
        return GroupBucket(
            self._manager_class,
            tuple([*self._group_by_keys, *group_by_keys]),
            self._basis_data,
        )

    def none(self) -> GroupBucket[GeneralManagerType]:
        """
        Produce an empty grouping bucket that preserves the current configuration.

        Returns:
            GroupBucket[GeneralManagerType]: Empty grouping bucket with identical manager class and grouping keys.
        """
        return GroupBucket(
            self._manager_class, self._group_by_keys, self._basis_data.none()
        )
