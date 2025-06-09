from __future__ import annotations
from typing import (
    Type,
    Any,
    Generator,
    TypeVar,
)
from django.db import models
from general_manager.interface.baseInterface import (
    GeneralManagerType,
)
from general_manager.bucket.baseBucket import Bucket

from general_manager.manager.generalManager import GeneralManager

modelsModel = TypeVar("modelsModel", bound=models.Model)


class DatabaseBucket(Bucket[GeneralManagerType]):

    def __init__(
        self,
        data: models.QuerySet[modelsModel],
        manager_class: Type[GeneralManagerType],
        filter_definitions: dict[str, list[Any]] = {},
        exclude_definitions: dict[str, list[Any]] = {},
    ):
        if data is None:
            data = manager_class.filter(**filter_definitions).exclude(
                **exclude_definitions
            )
        self._data = data

        self._manager_class = manager_class
        self.filters = {**filter_definitions}
        self.excludes = {**exclude_definitions}

    def __iter__(self) -> Generator[GeneralManagerType]:
        for item in self._data:
            yield self._manager_class(item.pk)

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManager[GeneralManagerType],
    ) -> DatabaseBucket[GeneralManagerType]:
        if isinstance(other, GeneralManager) and other.__class__ == self._manager_class:
            return self.__or__(self.filter(id__in=[getattr(other, "id")]))
        if not isinstance(other, self.__class__):
            raise ValueError("Cannot combine different bucket types")
        if self._manager_class != other._manager_class:
            raise ValueError("Cannot combine different bucket managers")
        return self.__class__(
            self._data | other._data,
            self._manager_class,
            {},
        )

    def __mergeFilterDefinitions(
        self, basis: dict[str, list[Any]], **kwargs: Any
    ) -> dict[str, list[Any]]:
        """
        Merges filter definitions by appending values from keyword arguments to the corresponding lists in the basis dictionary.

        Args:
            basis: A dictionary mapping filter keys to lists of values. Existing filter criteria.
            **kwargs: Additional filter criteria to be merged, where each value is appended to the corresponding key's list.

        Returns:
            A dictionary with keys mapping to lists containing all values from both the original basis and the new keyword arguments.
        """
        kwarg_filter: dict[str, list[Any]] = {}
        for key, value in basis.items():
            kwarg_filter[key] = value
        for key, value in kwargs.items():
            if key not in kwarg_filter:
                kwarg_filter[key] = []
            kwarg_filter[key].append(value)
        return kwarg_filter

    def filter(self, **kwargs: Any) -> DatabaseBucket[GeneralManagerType]:
        """
        Returns a new bucket containing manager instances matching the given filter criteria.

        Additional filter keyword arguments are merged with existing filters to further restrict the queryset.
        """
        merged_filter = self.__mergeFilterDefinitions(self.filters, **kwargs)
        return self.__class__(
            self._data.filter(**kwargs),
            self._manager_class,
            merged_filter,
            self.excludes,
        )

    def exclude(self, **kwargs: Any) -> DatabaseBucket[GeneralManagerType]:
        """
        Returns a new DatabaseBucket excluding items matching the given criteria.

        Keyword arguments define field lookups to exclude from the queryset. The returned bucket contains only items that do not match these filters.
        """
        merged_exclude = self.__mergeFilterDefinitions(self.excludes, **kwargs)
        return self.__class__(
            self._data.exclude(**kwargs),
            self._manager_class,
            self.filters,
            merged_exclude,
        )

    def first(self) -> GeneralManagerType | None:
        first_element = self._data.first()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def last(self) -> GeneralManagerType | None:
        first_element = self._data.last()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def count(self) -> int:
        return self._data.count()

    def all(self) -> DatabaseBucket:
        return self.__class__(self._data.all(), self._manager_class)

    def get(self, **kwargs: Any) -> GeneralManagerType:
        element = self._data.get(**kwargs)
        return self._manager_class(element.pk)

    def __getitem__(self, item: int | slice) -> GeneralManagerType | DatabaseBucket:
        if isinstance(item, slice):
            return self.__class__(self._data[item], self._manager_class)
        return self._manager_class(self._data[item].pk)

    def __len__(self) -> int:
        return self._data.count()

    def __repr__(self) -> str:
        return f"{self._manager_class.__name__}Bucket ({self._data})"

    def __contains__(self, item: GeneralManagerType | models.Model) -> bool:
        from general_manager.manager.generalManager import GeneralManager

        if isinstance(item, GeneralManager):
            return getattr(item, "id") in self._data.values_list("pk", flat=True)
        return item in self._data

    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> DatabaseBucket:
        if isinstance(key, str):
            key = (key,)
        if reverse:
            sorted_data = self._data.order_by(*[f"-{k}" for k in key])
        else:
            sorted_data = self._data.order_by(*key)
        return self.__class__(sorted_data, self._manager_class)
