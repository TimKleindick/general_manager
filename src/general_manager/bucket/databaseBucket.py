from __future__ import annotations
from typing import Type, Any, Generator, TypeVar, TYPE_CHECKING
from django.db import models
from general_manager.interface.baseInterface import (
    GeneralManagerType,
)
from general_manager.utils.filterParser import create_filter_function
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
        Initialize a DatabaseBucket with a Django queryset, a manager class, and optional filter and exclude definitions.

        Parameters:
            data (QuerySet): The Django queryset containing model instances to be managed.
            manager_class (Type[GeneralManagerType]): The manager class used to wrap model instances.
            filter_definitions (dict[str, list[Any]], optional): Initial filter criteria for the queryset.
            exclude_definitions (dict[str, list[Any]], optional): Initial exclude criteria for the queryset.
        """
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
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> DatabaseBucket[GeneralManagerType]:
        """
        Return a new bucket containing the union of this bucket and another bucket or manager instance.

        If `other` is a manager instance of the same class, it is converted to a bucket before combining. If `other` is a compatible bucket, the resulting bucket contains all unique items from both. Raises a `ValueError` if the types or manager classes are incompatible.

        Returns:
            DatabaseBucket[GeneralManagerType]: A new bucket with the combined items.
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

    def __parseFilterDeifintions(self, **kwargs: Any):
        annotations: dict[str, Any] = {}
        orm_kwargs: dict[str, list[Any]] = {}
        python_filters: list[tuple[str, Any, str]] = []
        properties = self._manager_class.Interface.getGraphQLProperties()

        for k, v in kwargs.items():
            root = k.split("__")[0]
            if root in properties:
                if not properties[root].filterable:
                    raise ValueError(
                        f"Property '{root}' is not filterable in {self._manager_class.__name__}"
                    )
                prop = properties[root]
                if prop.query_annotation is not None:
                    annotations[root] = prop.query_annotation
                    orm_kwargs[k] = v
                else:
                    python_filters.append((k, v, root))
            else:
                orm_kwargs[k] = v

        return annotations, orm_kwargs, python_filters

    def __parsePythonFilters(
        self, query_set: models.QuerySet, python_filters: list[tuple[str, Any, str]]
    ) -> list[int]:
        ids: list[int] = []
        for obj in query_set:
            inst = self._manager_class(obj.pk)
            keep = True
            for k, val, root in python_filters:
                lookup = k.split("__", 1)[1] if "__" in k else ""
                func = create_filter_function(lookup, val)
                if not func(getattr(inst, root)):
                    keep = False
                    break
            if keep:
                ids.append(obj.pk)
        return ids

    def filter(self, **kwargs: Any) -> DatabaseBucket[GeneralManagerType]:
        """
        Returns a new bucket with manager instances matching the combined filter criteria.

        Additional filter arguments are merged with any existing filters to further restrict the queryset, producing a new DatabaseBucket instance.
        """
        annotations, orm_kwargs, python_filters = self.__parseFilterDeifintions(
            **kwargs
        )
        qs = self._data
        if annotations:
            other_annotations: dict[str, Any] = {}
            for key, value in annotations.items():
                if not callable(value):
                    other_annotations[key] = value
                    continue
                qs = value(qs)
            if not isinstance(qs, models.QuerySet):
                raise TypeError("Query annotation must return a Django QuerySet")
            qs = qs.annotate(**other_annotations)
        try:
            qs = qs.filter(**orm_kwargs)
        except Exception as e:
            raise ValueError(f"Error filtering queryset: {e}")

        if python_filters:
            ids = self.__parsePythonFilters(qs, python_filters)
            qs = qs.filter(pk__in=ids)

        merged_filter = self.__mergeFilterDefinitions(self.filters, **kwargs)
        return self.__class__(qs, self._manager_class, merged_filter, self.excludes)

    def exclude(self, **kwargs: Any) -> DatabaseBucket[GeneralManagerType]:
        """
        Returns a new DatabaseBucket excluding items that match the specified criteria.

        Keyword arguments specify field lookups to exclude from the queryset. The resulting bucket contains only items that do not satisfy these exclusion filters.
        """
        annotations, orm_kwargs, python_filters = self.__parseFilterDeifintions(
            **kwargs
        )
        qs = self._data
        if annotations:
            other_annotations: dict[str, Any] = {}
            for key, value in annotations.items():
                if not callable(value):
                    other_annotations[key] = value
                    continue
                qs = value(qs)
            if not isinstance(qs, models.QuerySet):
                raise TypeError("Query annotation must return a Django QuerySet")
            qs = qs.annotate(**other_annotations)
        qs = qs.exclude(**orm_kwargs)

        if python_filters:
            ids = self.__parsePythonFilters(qs, python_filters)
            qs = qs.exclude(pk__in=ids)

        merged_exclude = self.__mergeFilterDefinitions(self.excludes, **kwargs)
        return self.__class__(qs, self._manager_class, self.filters, merged_exclude)

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

    def __str__(self) -> str:
        """
        Returns a string representation of the bucket, showing the manager class name and the underlying queryset.
        """
        return f"{self._manager_class.__name__}Bucket {self._data} ({len(self._data)} items)"

    def __repr__(self) -> str:
        """
        Returns a string representation of the bucket, showing the manager class name and the underlying queryset.
        """
        return f"DatabaseBucket ({self._data}, manager_class={self._manager_class.__name__}, filters={self.filters}, excludes={self.excludes})"

    def __contains__(self, item: GeneralManagerType | models.Model) -> bool:
        """
        Determine whether a manager instance or Django model instance is present in the bucket.

        Returns:
            True if the item's primary key exists in the underlying queryset; otherwise, False.
        """
        from general_manager.manager.generalManager import GeneralManager

        if isinstance(item, GeneralManager):
            return item.identification.get("id", None) in self._data.values_list(
                "pk", flat=True
            )
        return item.pk in self._data.values_list("pk", flat=True)

    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> DatabaseBucket:
        """
        Return a new DatabaseBucket sorted by the specified field or fields.

        Parameters:
            key (str or tuple of str): Field name or tuple of field names to sort by.
            reverse (bool): If True, sort in descending order.

        Returns:
            DatabaseBucket: A new bucket instance containing the sorted queryset.
        """
        if isinstance(key, str):
            key = (key,)
        properties = self._manager_class.Interface.getGraphQLProperties()
        annotations: dict[str, Any] = {}
        python_keys: list[str] = []
        qs = self._data
        for k in key:
            if k in properties:
                prop = properties[k]
                if not prop.sortable:
                    raise ValueError(
                        f"Property '{k}' is not sortable in {self._manager_class.__name__}"
                    )
                if prop.query_annotation is not None:
                    if callable(prop.query_annotation):
                        qs = prop.query_annotation(qs)
                    else:
                        annotations[k] = prop.query_annotation
                else:
                    python_keys.append(k)
        if not isinstance(qs, models.QuerySet):
            raise TypeError("Query annotation must return a Django QuerySet")
        if annotations:
            qs = qs.annotate(**annotations)

        if python_keys:
            objs = list(qs)

            def key_func(obj):
                inst = self._manager_class(obj.pk)
                values = []
                for k in key:
                    if k in properties:
                        if k in python_keys:
                            values.append(getattr(inst, k))
                        else:
                            values.append(getattr(obj, k))
                    else:
                        values.append(getattr(obj, k))
                return tuple(values)

            objs.sort(key=key_func, reverse=reverse)
            ordered_ids = [obj.pk for obj in objs]
            case = models.Case(
                *[models.When(pk=pk, then=pos) for pos, pk in enumerate(ordered_ids)],
                output_field=models.IntegerField(),
            )
            qs = qs.filter(pk__in=ordered_ids).annotate(_order=case).order_by("_order")
        else:
            order_fields = [f"-{k}" if reverse else k for k in key]
            try:
                qs = qs.order_by(*order_fields)
            except Exception as e:
                raise ValueError(f"Error ordering queryset: {e}")

        return self.__class__(qs, self._manager_class)
