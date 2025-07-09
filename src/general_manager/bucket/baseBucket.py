from __future__ import annotations
from abc import ABC, abstractmethod
from typing import (
    Type,
    Generator,
    TYPE_CHECKING,
    Any,
    Generic,
    TypeVar,
)

GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager
    from general_manager.manager.groupManager import GroupManager
    from general_manager.bucket.groupBucket import GroupBucket
    from general_manager.interface.baseInterface import InterfaceBase


class Bucket(ABC, Generic[GeneralManagerType]):

    def __init__(self, manager_class: Type[GeneralManagerType]):
        """
        Initializes the Bucket with a specified manager class.

        Args:
            manager_class: The class of manager objects this bucket will manage.
        """
        self._manager_class = manager_class
        self._data = None
        self.excludes = {}
        self.filters = {}

    def __eq__(self, other: object) -> bool:
        """
        Checks if this Bucket is equal to another by comparing class, data, and manager class.

        Returns:
            True if both objects are of the same class and have equal internal data and manager class; otherwise, False.
        """
        if not isinstance(other, self.__class__):
            return False
        return self._data == other._data and self._manager_class == other._manager_class

    def __reduce__(self) -> str | tuple[Any, ...]:
        """
        Prepares the object for pickling by returning the class and initialization arguments.

        Returns:
            A tuple containing the class and a tuple of arguments needed to reconstruct the object during unpickling.
        """
        return (
            self.__class__,
            (None, self._manager_class, self.filters, self.excludes),
        )

    @abstractmethod
    def __or__(
        self,
        other: (
            Bucket[GeneralManagerType]
            | GeneralManager[GeneralManagerType, InterfaceBase]
        ),
    ) -> Bucket[GeneralManagerType]:
        """
        Returns a new bucket representing the union of this bucket and another bucket or manager instance.

        Args:
            other: Another bucket or a single manager instance to combine with this bucket.

        Returns:
            A new bucket containing all unique items from both sources.
        """
        raise NotImplementedError

    @abstractmethod
    def __iter__(
        self,
    ) -> Generator[GeneralManagerType | GroupManager[GeneralManagerType], None, None]:
        """
        Returns an iterator over the items in the bucket.

        Yields:
            Instances of the managed type or group manager contained in the bucket.
        """
        raise NotImplementedError

    @abstractmethod
    def filter(self, **kwargs: Any) -> Bucket[GeneralManagerType]:
        """
        Returns a new bucket containing only items that match the specified filter criteria.

        Args:
            **kwargs: Field-value pairs used to filter items in the bucket.

        Returns:
            A new Bucket instance with items matching the given criteria.
        """
        raise NotImplementedError

    @abstractmethod
    def exclude(self, **kwargs: Any) -> Bucket[GeneralManagerType]:
        """
        Returns a new Bucket excluding items that match the specified criteria.

        Args:
            **kwargs: Field-value pairs specifying the exclusion criteria.

        Returns:
            A new Bucket instance with items matching the criteria excluded.
        """
        raise NotImplementedError

    @abstractmethod
    def first(self) -> GeneralManagerType | GroupManager[GeneralManagerType] | None:
        """
        Returns the first item in the bucket, or None if the bucket is empty.

        Returns:
            The first GeneralManager or GroupManager instance, or None if no items exist.
        """
        raise NotImplementedError

    @abstractmethod
    def last(self) -> GeneralManagerType | GroupManager[GeneralManagerType] | None:
        """
        Returns the last item in the bucket, or None if the bucket is empty.

        Returns:
            The last GeneralManager or GroupManager instance, or None if no items exist.
        """
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        """
        Returns the number of items in the bucket.

        Subclasses must implement this method to provide the count of contained elements.
        """
        raise NotImplementedError

    @abstractmethod
    def all(self) -> Bucket[GeneralManagerType]:
        """
        Returns a bucket containing all items managed by this instance.

        Subclasses must implement this method to provide access to the complete collection without filters or exclusions applied.
        """
        raise NotImplementedError

    @abstractmethod
    def get(
        self, **kwargs: Any
    ) -> GeneralManagerType | GroupManager[GeneralManagerType]:
        """
        Retrieves a single item matching the specified criteria.

        Args:
            **kwargs: Field-value pairs used to identify the item.

        Returns:
            The matching GeneralManager or GroupManager instance.

        Raises:
            NotImplementedError: If the method is not implemented by a subclass.
        """
        raise NotImplementedError

    @abstractmethod
    def __getitem__(
        self, item: int | slice
    ) -> (
        GeneralManagerType
        | GroupManager[GeneralManagerType]
        | Bucket[GeneralManagerType]
    ):
        """
        Retrieves an item or a slice from the bucket.

        Args:
            item: An integer index to retrieve a single element, or a slice to retrieve a subset.

        Returns:
            A single manager instance if an integer is provided, or a new Bucket containing the sliced elements if a slice is provided.

        Raises:
            NotImplementedError: This method must be implemented by subclasses.
        """
        raise NotImplementedError

    @abstractmethod
    def __len__(self) -> int:
        """
        Returns the number of items in the bucket.

        Subclasses must implement this method to provide the count of contained elements.
        """
        raise NotImplementedError

    @abstractmethod
    def __contains__(self, item: GeneralManagerType) -> bool:
        """
        Checks whether the specified item is present in the bucket.

        Args:
                item: The manager instance to check for membership.

        Returns:
                True if the item is contained in the bucket, otherwise False.
        """
        raise NotImplementedError

    @abstractmethod
    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> Bucket[GeneralManagerType]:
        """
        Returns a new Bucket with items sorted by the specified key or keys.

        Args:
            key: A string or tuple of strings specifying the attribute(s) to sort by.
            reverse: If True, sorts in descending order. Defaults to False.

        Returns:
            A new Bucket instance with items sorted according to the given key(s).
        """
        raise NotImplementedError

    def group_by(self, *group_by_keys: str) -> GroupBucket[GeneralManagerType]:
        """
        Groups the bucket's data by one or more specified keys.

        Args:
            *group_by_keys: One or more attribute names to group the data by.

        Returns:
            A GroupBucket instance containing the grouped data.
        """
        from general_manager.bucket.groupBucket import GroupBucket

        return GroupBucket(self._manager_class, group_by_keys, self)
