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


class Bucket(ABC, Generic[GeneralManagerType]):

    def __init__(self, manager_class: Type[GeneralManagerType]):
        self._manager_class = manager_class
        self._data = None
        self.excludes = {}
        self.filters = {}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, self.__class__):
            return False
        return self._data == other._data and self._manager_class == other._manager_class

    def __reduce__(self) -> str | tuple[Any, ...]:
        return (
            self.__class__,
            (None, self._manager_class, self.filters, self.excludes),
        )

    @abstractmethod
    def __or__(
        self, other: Bucket[GeneralManagerType] | GeneralManager[GeneralManagerType]
    ) -> Bucket[GeneralManagerType]:
        raise NotImplementedError

    @abstractmethod
    def __iter__(
        self,
    ) -> Generator[GeneralManagerType | GroupManager[GeneralManagerType]]:
        raise NotImplementedError

    @abstractmethod
    def filter(self, **kwargs: Any) -> Bucket[GeneralManagerType]:
        raise NotImplementedError

    @abstractmethod
    def exclude(self, **kwargs: Any) -> Bucket[GeneralManagerType]:
        raise NotImplementedError

    @abstractmethod
    def first(self) -> GeneralManagerType | GroupManager[GeneralManagerType] | None:
        raise NotImplementedError

    @abstractmethod
    def last(self) -> GeneralManagerType | GroupManager[GeneralManagerType] | None:
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def all(self) -> Bucket[GeneralManagerType]:
        raise NotImplementedError

    @abstractmethod
    def get(
        self, **kwargs: Any
    ) -> GeneralManagerType | GroupManager[GeneralManagerType]:
        raise NotImplementedError

    @abstractmethod
    def __getitem__(
        self, item: int | slice
    ) -> (
        GeneralManagerType
        | GroupManager[GeneralManagerType]
        | Bucket[GeneralManagerType]
    ):
        raise NotImplementedError

    @abstractmethod
    def __len__(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def __contains__(self, item: GeneralManagerType) -> bool:
        raise NotImplementedError

    @abstractmethod
    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> Bucket[GeneralManagerType]:
        raise NotImplementedError

    def group_by(self, *group_by_keys: str) -> GroupBucket[GeneralManagerType]:
        """
        This method groups the data by the given arguments.
        It returns a GroupBucket with the grouped data.
        """
        from general_manager.bucket.groupBucket import GroupBucket

        return GroupBucket(self._manager_class, group_by_keys, self)
