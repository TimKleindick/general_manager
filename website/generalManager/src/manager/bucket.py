from __future__ import annotations
from abc import ABC, abstractmethod
from django.db import models
from typing import Type, Generator, TYPE_CHECKING, Any, TypeVar, Generic

T = TypeVar("T")

if TYPE_CHECKING:
    from generalManager.src.manager.generalManager import GeneralManager


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


T1 = TypeVar("T1", bound=models.Model)


class DatabaseBucket(Bucket["GeneralManager"]):

    def __init__(
        self,
        data: models.QuerySet[T1],
        manager_class: Type[GeneralManager],
        filter_definitions: dict[str, list[Any]] = {},
    ):
        self._data = data
        self._manager_class = manager_class
        self._filter_definitions = {**filter_definitions}

    def __iter__(self) -> Generator[GeneralManager]:
        for item in self._data:
            yield self._manager_class(item.pk)

    def __mergeFilterDefinitions(self, **kwargs: Any) -> dict[str, list[Any]]:
        kwarg_filter: dict[str, list[Any]] = {}
        for key, value in self._filter_definitions.items():
            kwarg_filter[key] = value
        for key, value in kwargs.items():
            if key not in kwarg_filter:
                kwarg_filter[key] = []
            kwarg_filter[key].append(value)
        return kwarg_filter

    def filter(self, **kwargs: Any) -> DatabaseBucket:
        merged_filter = self.__mergeFilterDefinitions(**kwargs)
        return self.__class__(
            self._data.filter(**kwargs), self._manager_class, merged_filter
        )

    def exclude(self, **kwargs: Any) -> DatabaseBucket:
        merged_filter = self.__mergeFilterDefinitions(**kwargs)
        return self.__class__(
            self._data.exclude(**kwargs), self._manager_class, merged_filter
        )

    def first(self) -> GeneralManager | None:
        first_element = self._data.first()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def last(self) -> GeneralManager | None:
        first_element = self._data.last()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def count(self) -> int:
        return self._data.count()

    def all(self) -> DatabaseBucket:
        return self.__class__(self._data.all(), self._manager_class)

    def get(self, **kwargs: Any) -> GeneralManager:
        element = self._data.get(**kwargs)
        return self._manager_class(element.pk)

    def __getitem__(self, item: int | slice) -> GeneralManager | DatabaseBucket:
        if isinstance(item, slice):
            return self.__class__(self._data[item], self._manager_class)
        return self._manager_class(self._data[item].pk)

    def __len__(self) -> int:
        return self._data.count()

    def __repr__(self) -> str:
        return f"{self._manager_class.__name__}Bucket ({self._data})"

    def __contains__(self, item: GeneralManager | models.Model) -> bool:
        from generalManager.src.manager.generalManager import GeneralManager

        if isinstance(item, GeneralManager):
            return item.id in self._data.values_list("pk", flat=True)
        return item in self._data
