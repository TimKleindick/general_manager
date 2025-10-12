"""Database-backed bucket implementation for GeneralManager collections."""

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
    """Bucket implementation backed by Django ORM querysets."""

    def __init__(
        self,
        data: models.QuerySet[modelsModel],
        manager_class: Type[GeneralManagerType],
        filter_definitions: dict[str, list[Any]] | None = None,
        exclude_definitions: dict[str, list[Any]] | None = None,
    ) -> None:
        """
        Instantiate a database-backed bucket with optional filter state.

        Parameters:
            data (models.QuerySet[modelsModel]): Queryset providing the underlying data.
            manager_class (type[GeneralManagerType]): GeneralManager subclass used to wrap rows.
            filter_definitions (dict[str, list[Any]] | None): Pre-existing filter expressions captured from parent buckets.
            exclude_definitions (dict[str, list[Any]] | None): Pre-existing exclusion expressions captured from parent buckets.

        Returns:
            None
        """
        self._data = data
        self._manager_class = manager_class
        self.filters = {**(filter_definitions or {})}
        self.excludes = {**(exclude_definitions or {})}

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        """
        Iterate over manager instances corresponding to the queryset rows.

        Yields:
            GeneralManagerType: Manager instance for each primary key in the queryset.
        """
        for item in self._data:
            yield self._manager_class(item.pk)

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> DatabaseBucket[GeneralManagerType]:
        """
        Merge two database buckets (or bucket and instance) into a single result.

        Parameters:
            other (Bucket[GeneralManagerType] | GeneralManagerType): Bucket or manager instance to merge.

        Returns:
            DatabaseBucket[GeneralManagerType]: New bucket containing the combined queryset.

        Raises:
            ValueError: If the operand is incompatible or uses a different manager class.
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
        Merge stored filter definitions with additional lookup values.

        Parameters:
            basis (dict[str, list[Any]]): Existing lookup definitions copied into the result.
            **kwargs: New lookups whose values are appended to the result mapping.

        Returns:
            dict[str, list[Any]]: Combined mapping of lookups to value lists.
        """
        kwarg_filter: dict[str, list[Any]] = {}
        for key, value in basis.items():
            kwarg_filter[key] = value
        for key, value in kwargs.items():
            if key not in kwarg_filter:
                kwarg_filter[key] = []
            kwarg_filter[key].append(value)
        return kwarg_filter

    def __parseFilterDeifintions(
        self,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, list[Any]], list[tuple[str, Any, str]]]:
        """
        Separate ORM-compatible filters from Python-side property filters.

        Parameters:
            **kwargs: Filter lookups supplied to `filter` or `exclude`.

        Returns:
            tuple[dict[str, Any], dict[str, Any], list[tuple[str, Any, str]]]:
                Query annotations, ORM-compatible lookups, and Python-evaluated filter specifications.
        """
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
        """
        Evaluate Python-only filters and return the primary keys that satisfy them.

        Parameters:
            query_set (models.QuerySet): Queryset to inspect.
            python_filters (list[tuple[str, Any, str]]): Filters requiring Python evaluation, each containing the lookup, value, and property root.

        Returns:
            list[int]: Primary keys of rows that meet all Python-evaluated filters.
        """
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
        Produce a bucket filtered by the supplied lookups in addition to existing state.

        Parameters:
            **kwargs (Any): Django-style lookup expressions applied to the underlying queryset.

        Returns:
            DatabaseBucket[GeneralManagerType]: New bucket representing the refined queryset.

        Raises:
            ValueError: If the ORM rejects the filter arguments.
            TypeError: If a query annotation callback does not return a queryset.
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
        Produce a bucket that excludes rows matching the supplied lookups.

        Parameters:
            **kwargs (Any): Django-style lookup expressions identifying records to omit.

        Returns:
            DatabaseBucket[GeneralManagerType]: New bucket representing the filtered queryset.

        Raises:
            TypeError: If a query annotation callback does not return a queryset.
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
        Return the first row in the queryset as a manager instance.

        Returns:
            GeneralManagerType | None: First manager instance if available.
        """
        first_element = self._data.first()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def last(self) -> GeneralManagerType | None:
        """
        Return the last row in the queryset as a manager instance.

        Returns:
            GeneralManagerType | None: Last manager instance if available.
        """
        first_element = self._data.last()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def count(self) -> int:
        """
        Count the number of rows represented by the bucket.

        Returns:
            int: Number of queryset rows.
        """
        return self._data.count()

    def all(self) -> DatabaseBucket:
        """
        Return a bucket materialising the queryset without further filtering.

        Returns:
            DatabaseBucket: Bucket encapsulating `self._data.all()`.
        """
        return self.__class__(self._data.all(), self._manager_class)

    def get(self, **kwargs: Any) -> GeneralManagerType:
        """
        Retrieve a single manager instance matching the provided lookups.

        Parameters:
            **kwargs (Any): Field lookups resolved via `QuerySet.get`.

        Returns:
            GeneralManagerType: Manager instance wrapping the matched model.

        Raises:
            models.ObjectDoesNotExist: Propagated from the underlying queryset when no row matches.
            models.MultipleObjectsReturned: Propagated when multiple rows satisfy the lookup.
        """
        element = self._data.get(**kwargs)
        return self._manager_class(element.pk)

    def __getitem__(self, item: int | slice) -> GeneralManagerType | DatabaseBucket:
        """
        Access manager instances by index or obtain a sliced bucket.

        Parameters:
            item (int | slice): Index of the desired row or slice object describing a range.

        Returns:
            GeneralManagerType | DatabaseBucket: Manager instance for single indices or bucket wrapping the sliced queryset.
        """
        if isinstance(item, slice):
            return self.__class__(self._data[item], self._manager_class)
        return self._manager_class(self._data[item].pk)

    def __len__(self) -> int:
        """
        Return the number of rows represented by the bucket.

        Returns:
            int: Size of the queryset.
        """
        return self._data.count()

    def __str__(self) -> str:
        """
        Return a user-friendly representation of the bucket.

        Returns:
            str: Human-readable description of the queryset and manager class.
        """
        return f"{self._manager_class.__name__}Bucket {self._data} ({len(self._data)} items)"

    def __repr__(self) -> str:
        """
        Return a debug representation of the bucket.

        Returns:
            str: Detailed description including queryset, manager class, filters, and excludes.
        """
        return f"DatabaseBucket ({self._data}, manager_class={self._manager_class.__name__}, filters={self.filters}, excludes={self.excludes})"

    def __contains__(self, item: GeneralManagerType | models.Model) -> bool:
        """
        Determine whether the provided instance belongs to the bucket.

        Parameters:
            item (GeneralManagerType | models.Model): Manager or model instance whose primary key is checked.

        Returns:
            bool: True when the primary key exists in the queryset.
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
        Return a new bucket ordered by the specified fields.

        Parameters:
            key (str | tuple[str, ...]): Field name(s) used for ordering.
            reverse (bool): Whether to sort in descending order.

        Returns:
            DatabaseBucket: Bucket whose queryset is ordered accordingly.

        Raises:
            ValueError: If sorting by a non-sortable property or when the ORM rejects the ordering.
            TypeError: If a property annotation callback does not return a queryset.
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

            def key_func(obj: models.Model) -> tuple[object, ...]:
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

    def none(self) -> DatabaseBucket[GeneralManagerType]:
        """
        Return an empty bucket sharing the same manager class.

        Returns:
            DatabaseBucket[GeneralManagerType]: Empty bucket retaining filter and exclude state.
        """
        own = self.all()
        own._data = own._data.none()
        return own
