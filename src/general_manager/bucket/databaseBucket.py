from __future__ import annotations
from typing import (
    Type,
    Any,
    Generator,
    TypeVar,
    TYPE_CHECKING,
)
from django.db import models
from general_manager.interface.baseInterface import (
    GeneralManagerType,
)
from general_manager.bucket.baseBucket import Bucket

from general_manager.manager.generalManager import GeneralManager

modelsModel = TypeVar("modelsModel", bound=models.Model)

if TYPE_CHECKING:
    from general_manager.interface.databaseInterface import DatabaseInterface


class DatabaseBucket(Bucket[GeneralManagerType]):

    def __init__(
        self,
        data: models.QuerySet[modelsModel],
        manager_class: Type[GeneralManagerType],
        filter_definitions: dict[str, list[Any]] | None = None,
        exclude_definitions: dict[str, list[Any]] | None = None,
    ):
        """
        Initializes a DatabaseBucket with a queryset, manager class, and optional filter and exclude definitions.

        If no queryset is provided, constructs one using the manager class and the given filters and excludes. Stores the queryset, manager class, and copies of the filter and exclude definitions for further operations.
        """
        if data is None:
            data = manager_class.Interface._model.objects.filter(
                **filter_definitions
            ).exclude(**exclude_definitions)
        self._data = data

        self._manager_class = manager_class
        self.filters = {**(filter_definitions or {})}
        self.excludes = {**(exclude_definitions or {})}

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        """
        Yields manager instances for each item in the underlying queryset.

        Iterates over the queryset, returning a new instance of the manager class for each item's primary key.
        """
        for item in self._data:
            yield self._manager_class(item.pk)

    def __or__(
        self,
        other: (
            Bucket[GeneralManagerType]
            | GeneralManager[GeneralManagerType, DatabaseInterface]
        ),
    ) -> DatabaseBucket[GeneralManagerType]:
        """
        Combines this bucket with another bucket or manager instance using the union operator.

        If `other` is a manager instance of the same class, creates a bucket containing only that instance and combines it. If `other` is a compatible bucket, returns a new bucket containing the union of both buckets' querysets. Raises a `ValueError` if the types or manager classes are incompatible.

        Returns:
            A new `DatabaseBucket` containing the combined items.
        """
        if isinstance(other, GeneralManager) and other.__class__ == self._manager_class:
            return self.__or__(
                self._manager_class.filter(id__in=[other.identification["id"]])
            )
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
        Combines existing filter definitions with additional criteria by appending new values to each key's list.

        Args:
            basis: Existing filter definitions as a dictionary mapping keys to lists of values.
            **kwargs: Additional filter criteria to merge, with each value appended to the corresponding key.

        Returns:
            A dictionary containing all filter keys with lists of values from both the original and new criteria.
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
        Returns a new bucket with manager instances matching the combined filter criteria.

        Additional filter arguments are merged with any existing filters to further restrict the queryset, producing a new DatabaseBucket instance.
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
        Returns a new DatabaseBucket excluding items that match the specified criteria.

        Keyword arguments specify field lookups to exclude from the queryset. The resulting bucket contains only items that do not satisfy these exclusion filters.
        """
        merged_exclude = self.__mergeFilterDefinitions(self.excludes, **kwargs)
        return self.__class__(
            self._data.exclude(**kwargs),
            self._manager_class,
            self.filters,
            merged_exclude,
        )

    def first(self) -> GeneralManagerType | None:
        """
        Returns the first item in the queryset wrapped in the manager class, or None if the queryset is empty.
        """
        first_element = self._data.first()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def last(self) -> GeneralManagerType | None:
        """
        Returns the last item in the queryset wrapped in the manager class, or None if the queryset is empty.
        """
        first_element = self._data.last()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def count(self) -> int:
        """
        Returns the number of items in the bucket.
        """
        return self._data.count()

    def all(self) -> DatabaseBucket:
        """
        Returns a new DatabaseBucket containing all items from the current queryset.
        """
        return self.__class__(self._data.all(), self._manager_class)

    def get(self, **kwargs: Any) -> GeneralManagerType:
        """
        Retrieves a single item matching the given criteria and returns it as a manager instance.

        Args:
                **kwargs: Field lookups to identify the item to retrieve.

        Returns:
                A manager instance wrapping the uniquely matched model object.

        Raises:
                Does not handle exceptions; any exceptions raised by the underlying queryset's `get()` method will propagate.
        """
        element = self._data.get(**kwargs)
        return self._manager_class(element.pk)

    def __getitem__(self, item: int | slice) -> GeneralManagerType | DatabaseBucket:
        """
        Enables indexing and slicing of the bucket.

        If an integer index is provided, returns the manager instance for the corresponding item. If a slice is provided, returns a new bucket containing the sliced queryset.
        """
        if isinstance(item, slice):
            return self.__class__(self._data[item], self._manager_class)
        return self._manager_class(self._data[item].pk)

    def __len__(self) -> int:
        """
        Returns the number of items in the bucket.
        """
        return self._data.count()

    def __repr__(self) -> str:
        """
        Returns a string representation of the bucket, showing the manager class name and the underlying queryset.
        """
        return f"{self._manager_class.__name__}Bucket ({self._data})"

    def __contains__(self, item: GeneralManagerType | models.Model) -> bool:
        """
        Checks if a manager instance or model instance is present in the bucket.

        Args:
            item: A manager instance or Django model instance to check for membership.

        Returns:
            True if the item's primary key exists in the underlying queryset; otherwise, False.
        """
        from general_manager.manager.generalManager import GeneralManager

        if isinstance(item, GeneralManager):
            return item.identification.get("id", None) in self._data.values_list(
                "pk", flat=True
            )
        return item in self._data

    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> DatabaseBucket:
        """
        Returns a new DatabaseBucket sorted by the specified field or fields.

        Args:
            key: A field name or tuple of field names to sort by.
            reverse: If True, sorts in descending order.

        Returns:
            A new DatabaseBucket instance with the sorted queryset.
        """
        if isinstance(key, str):
            key = (key,)
        if reverse:
            sorted_data = self._data.order_by(*[f"-{k}" for k in key])
        else:
            sorted_data = self._data.order_by(*key)
        return self.__class__(sorted_data, self._manager_class)
