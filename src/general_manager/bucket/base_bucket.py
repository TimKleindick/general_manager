"""Abstract bucket primitives for managing GeneralManager collections."""

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
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.manager.group_manager import GroupManager
    from general_manager.bucket.group_bucket import GroupBucket
    from general_manager.interface.base_interface import InterfaceBase


class Bucket(ABC, Generic[GeneralManagerType]):
    """Abstract interface for lazily evaluated GeneralManager collections."""

    def __init__(self, manager_class: Type[GeneralManagerType]) -> None:
        """
        Create a bucket bound to a specific manager class.

        Parameters:
            manager_class (type[GeneralManagerType]): GeneralManager subclass whose instances this bucket represents.

        Returns:
            None
        """
        self._manager_class = manager_class
        self._data: Any = None
        self.excludes: dict[str, Any] = {}
        self.filters: dict[str, Any] = {}

    def __eq__(self, other: object) -> bool:
        """
        Compare two buckets for equality.

        Parameters:
            other (object): Object tested for equality with this bucket.

        Returns:
            bool: True when the buckets share the same class, manager class, and data payload.
        """
        if not isinstance(other, self.__class__):
            return False
        return self._data == other._data and self._manager_class == other._manager_class

    def __reduce__(self) -> str | tuple[Any, ...]:
        """
        Provide pickling support by returning the constructor and arguments.

        Returns:
            tuple[Any, ...]: Data allowing the bucket to be reconstructed during unpickling.
        """
        return (
            self.__class__,
            (None, self._manager_class, self.filters, self.excludes),
        )

    @abstractmethod
    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> Bucket[GeneralManagerType]:
        """
        Return a bucket containing the union of this bucket and another input.

        Parameters:
            other (Bucket[GeneralManagerType] | GeneralManagerType): Bucket or single manager instance to merge.

        Returns:
            Bucket[GeneralManagerType]: New bucket with the combined contents.
        """
        raise NotImplementedError

    @abstractmethod
    def __iter__(
        self,
    ) -> Generator[GeneralManagerType | GroupManager[GeneralManagerType], None, None]:
        """
        Iterate over items in the bucket.

        Yields:
            GeneralManagerType | GroupManager[GeneralManagerType]: Items stored in the bucket.
        """
        raise NotImplementedError

    @abstractmethod
    def filter(self, **kwargs: Any) -> Bucket[GeneralManagerType]:
        """
        Return a bucket reduced to items matching the provided filters.

        Parameters:
            **kwargs: Field lookups applied to the underlying query.

        Returns:
            Bucket[GeneralManagerType]: Filtered bucket instance.
        """
        raise NotImplementedError

    @abstractmethod
    def exclude(self, **kwargs: Any) -> Bucket[GeneralManagerType]:
        """
        Return a bucket that excludes items matching the provided filters.

        Parameters:
            **kwargs: Field lookups specifying records to remove from the result.

        Returns:
            Bucket[GeneralManagerType]: Bucket with the specified records excluded.
        """
        raise NotImplementedError

    @abstractmethod
    def first(self) -> GeneralManagerType | GroupManager[GeneralManagerType] | None:
        """
        Return the first item contained in the bucket.

        Returns:
            GeneralManagerType | GroupManager[GeneralManagerType] | None: First entry if present, otherwise None.
        """
        raise NotImplementedError

    @abstractmethod
    def last(self) -> GeneralManagerType | GroupManager[GeneralManagerType] | None:
        """
        Return the last item contained in the bucket.

        Returns:
            GeneralManagerType | GroupManager[GeneralManagerType] | None: Last entry if present, otherwise None.
        """
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        """
        Return the number of items represented by the bucket.

        Returns:
            int: Count of items.
        """
        raise NotImplementedError

    @abstractmethod
    def all(self) -> Bucket[GeneralManagerType]:
        """
        Return a bucket encompassing every item managed by this instance.

        Returns:
            Bucket[GeneralManagerType]: Bucket without filters or exclusions.
        """
        raise NotImplementedError

    @abstractmethod
    def get(
        self, **kwargs: Any
    ) -> GeneralManagerType | GroupManager[GeneralManagerType]:
        """
        Retrieve a single item matching the provided criteria.

        Parameters:
            **kwargs: Field lookups identifying the target record.

        Returns:
            GeneralManagerType | GroupManager[GeneralManagerType]: Matching item.
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
        Retrieve an item or slice from the bucket.

        Parameters:
            item (int | slice): Index or slice specifying the desired record(s).

        Returns:
            GeneralManagerType | GroupManager[GeneralManagerType] | Bucket[GeneralManagerType]: Resulting item or bucket slice.
        """
        raise NotImplementedError

    @abstractmethod
    def __len__(self) -> int:
        """
        Return the number of items contained in the bucket.

        Returns:
            int: Count of elements.
        """
        raise NotImplementedError

    @abstractmethod
    def __contains__(self, item: GeneralManagerType) -> bool:
        """
        Checks whether the specified item is present in the bucket.

        Parameters:
            item (GeneralManagerType): Manager instance evaluated for membership.

        Returns:
            bool: True if the bucket contains the provided instance.
        """
        raise NotImplementedError

    @abstractmethod
    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> Bucket[GeneralManagerType]:
        """
        Return a sorted bucket.

        Parameters:
            key (str | tuple[str, ...]): Attribute name(s) used for sorting.
            reverse (bool): Whether to sort in descending order.

        Returns:
            Bucket[GeneralManagerType]: Sorted bucket instance.
        """
        raise NotImplementedError

    def group_by(self, *group_by_keys: str) -> GroupBucket[GeneralManagerType]:
        """
        Materialise a grouped view of the bucket.

        Parameters:
            *group_by_keys (str): Attribute names used to form groups.

        Returns:
            GroupBucket[GeneralManagerType]: Bucket grouping items by the provided keys.
        """
        from general_manager.bucket.group_bucket import GroupBucket

        return GroupBucket(self._manager_class, group_by_keys, self)

    def none(self) -> Bucket[GeneralManagerType]:
        """
        Return an empty bucket instance.

        Returns:
            Bucket[GeneralManagerType]: Empty bucket.

        Raises:
            NotImplementedError: Always raised by the base implementation; subclasses must provide a concrete version.
        """
        raise NotImplementedError(
            "The 'none' method is not implemented in the base Bucket class. "
            "Subclasses should implement this method to return an empty bucket."
        )
