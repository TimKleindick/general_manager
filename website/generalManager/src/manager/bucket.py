from __future__ import annotations
from abc import ABC, abstractmethod
from django.db import models
from typing import (
    Type,
    Generator,
    TYPE_CHECKING,
    Any,
    TypeVar,
    Generic,
)

if TYPE_CHECKING:
    from generalManager.src.manager.generalManager import GeneralManager
    from generalManager.src.calculation.input import Input


T = TypeVar("T")


class Bucket(ABC, Generic[T]):

    def __init__(self, manager_class: Type[GeneralManager]):
        self._manager_class = manager_class
        self._data = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, self.__class__):
            return False
        return self._data == other._data and self._manager_class == other._manager_class

    def __iter__(self) -> Generator[GeneralManager]:
        raise NotImplementedError

    @abstractmethod
    def filter(self, **kwargs: Any) -> Bucket[T]:
        raise NotImplementedError

    @abstractmethod
    def exclude(self, **kwargs: Any) -> Bucket[T]:
        raise NotImplementedError

    @abstractmethod
    def first(self) -> T | None:
        raise NotImplementedError

    @abstractmethod
    def last(self) -> T | None:
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def all(self) -> Bucket[T]:
        raise NotImplementedError

    @abstractmethod
    def get(self, **kwargs: Any) -> T:
        raise NotImplementedError

    @abstractmethod
    def __getitem__(self, item: int | slice) -> T | Bucket[T]:
        raise NotImplementedError

    @abstractmethod
    def __len__(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def __contains__(self, item: T) -> bool:
        raise NotImplementedError
