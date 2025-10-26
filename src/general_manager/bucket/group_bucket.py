"""Grouping bucket implementation for aggregating GeneralManager instances."""

from __future__ import annotations
from typing import Any, Generator, Type
from general_manager.manager.group_manager import GroupManager
from general_manager.bucket.base_bucket import Bucket, GeneralManagerType


class InvalidGroupByKeyTypeError(TypeError):
    """Raised when a non-string value is provided as a group-by key."""

    def __init__(self) -> None:
        """
        Error raised when a non-string group-by key is provided.

        Initializes the exception with the message "groupBy() arguments must be strings."
        """
        super().__init__("groupBy() arguments must be strings.")


class UnknownGroupByKeyError(ValueError):
    """Raised when a group-by key does not exist on the manager interface."""

    def __init__(self, manager_name: str) -> None:
        """
        Create an UnknownGroupByKeyError indicating a missing attribute on a manager.

        Parameters:
            manager_name (str): Name of the manager whose attributes were expected; used to format the error message.
        """
        super().__init__(f"groupBy() arguments must be attributes of {manager_name}.")


class GroupBucketTypeMismatchError(TypeError):
    """Raised when attempting to merge grouping buckets of different types."""

    def __init__(self, first_type: type, second_type: type) -> None:
        """
        Initialize the error for attempting to combine two incompatible bucket types.

        Parameters:
            first_type (type): The first type involved in the attempted combination.
            second_type (type): The second type involved in the attempted combination.

        Notes:
            The exception message is formatted as "Cannot combine {first_type.__name__} with {second_type.__name__}."
        """
        super().__init__(
            f"Cannot combine {first_type.__name__} with {second_type.__name__}."
        )


class GroupBucketManagerMismatchError(ValueError):
    """Raised when grouping buckets track different manager classes."""

    def __init__(self, first_manager: type, second_manager: type) -> None:
        """
        Initialize the exception indicating two group buckets track different manager classes.

        Parameters:
            first_manager (type): The first manager class involved in the mismatch.
            second_manager (type): The second manager class involved in the mismatch.
        """
        super().__init__(
            f"Cannot combine buckets for {first_manager.__name__} and {second_manager.__name__}."
        )


class GroupItemNotFoundError(ValueError):
    """Raised when a grouped manager matching the provided criteria cannot be found."""

    def __init__(self, manager_name: str, criteria: dict[str, Any]) -> None:
        """
        Initialize an error indicating a grouped manager matching the provided lookup criteria could not be found.

        Parameters:
            manager_name (str): Name of the manager type searched for.
            criteria (dict[str, Any]): Lookup criteria used to locate the manager; included in the error message.
        """
        super().__init__(f"Cannot find {manager_name} with {criteria}.")


class EmptyGroupBucketSliceError(ValueError):
    """Raised when slicing a group bucket yields no results."""

    def __init__(self) -> None:
        """
        Initialize the EmptyGroupBucketSliceError indicating that slicing a GroupBucket produced no results.

        The exception carries the message "Cannot slice an empty GroupBucket."
        """
        super().__init__("Cannot slice an empty GroupBucket.")


class InvalidGroupBucketIndexError(TypeError):
    """Raised when a group bucket is indexed with an unsupported type."""

    def __init__(self, received_type: type) -> None:
        """
        Initialize the exception for an unsupported GroupBucket index argument type.

        Parameters:
            received_type (type): The actual type that was passed as the index; used to construct the error message.
        """
        super().__init__(
            f"Invalid argument type: {received_type}. Expected int or slice."
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
        self.__check_group_by_arguments(group_by_keys)
        self._group_by_keys = group_by_keys
        self._data: list[GroupManager[GeneralManagerType]] = (
            self.__build_grouped_manager(data)
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

    def __check_group_by_arguments(self, group_by_keys: tuple[str, ...]) -> None:
        """
        Validate that each provided group-by key is a string and is exposed by the manager interface.

        Parameters:
            group_by_keys (tuple[str, ...]): Attribute names to use for grouping.

        Raises:
            InvalidGroupByKeyTypeError: If any element of `group_by_keys` is not a string.
            UnknownGroupByKeyError: If any key is not listed in the manager class's interface attributes.
        """
        if not all(isinstance(arg, str) for arg in group_by_keys):
            raise InvalidGroupByKeyTypeError()
        if not all(
            arg in self._manager_class.Interface.get_attributes()
            for arg in group_by_keys
        ):
            raise UnknownGroupByKeyError(self._manager_class.__name__)

    def __build_grouped_manager(
        self,
        data: Bucket[GeneralManagerType],
    ) -> list[GroupManager[GeneralManagerType]]:
        """
        Builds a GroupManager for each distinct combination of configured group-by attribute values.

        Parameters:
            data (Bucket[GeneralManagerType]): Source bucket whose entries are partitioned by the bucket's configured group-by keys.

        Returns:
            list[GroupManager[GeneralManagerType]]: A list of GroupManager objects, one per unique tuple of group-by key values; groups are produced in order sorted by the string representation of their key tuples.
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
        Return a new GroupBucket representing the union of this bucket and another compatible GroupBucket.

        Parameters:
            other (GroupBucket): The grouping bucket to merge with this one.

        Returns:
            GroupBucket[GeneralManagerType]: A GroupBucket with the same manager class and grouping keys whose basis data is the union of both inputs.

        Raises:
            GroupBucketTypeMismatchError: If `other` is not a GroupBucket of the same class.
            GroupBucketManagerMismatchError: If `other` tracks a different manager class.
        """
        if not isinstance(other, self.__class__):
            raise GroupBucketTypeMismatchError(self.__class__, type(other))
        if self._manager_class != other._manager_class:
            raise GroupBucketManagerMismatchError(
                self._manager_class, other._manager_class
            )
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
        Retrieve the first GroupManager matching the provided lookups.

        Parameters:
            **kwargs: Field lookups used to filter the grouped managers.

        Returns:
            The first matching GroupManager.

        Raises:
            GroupItemNotFoundError: If no grouped manager matches the filters.
        """
        first_value = self.filter(**kwargs).first()
        if first_value is None:
            raise GroupItemNotFoundError(self._manager_class.__name__, kwargs)
        return first_value

    def __getitem__(
        self, item: int | slice
    ) -> GroupManager[GeneralManagerType] | GroupBucket[GeneralManagerType]:
        """
        Retrieve a single grouped manager by index or construct a new GroupBucket from a slice of groups.

        Parameters:
            item (int | slice): Integer index to select a single GroupManager, or a slice to select a subsequence of groups.

        Returns:
            GroupManager[GeneralManagerType] if `item` is an int, otherwise a GroupBucket[GeneralManagerType] built from the selected groups.

        Raises:
            EmptyGroupBucketSliceError: If the slice selects no groups.
            InvalidGroupBucketIndexError: If `item` is not an int or slice.
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
                raise EmptyGroupBucketSliceError()
            return GroupBucket(self._manager_class, self._group_by_keys, new_base_data)
        raise InvalidGroupBucketIndexError(type(item))

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
