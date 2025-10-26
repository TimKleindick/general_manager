"""Database-backed bucket implementation for GeneralManager collections."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Generator, Type, TypeVar

from django.core.exceptions import FieldError
from django.db import models

from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.filter_parser import create_filter_function

modelsModel = TypeVar("modelsModel", bound=models.Model)
GeneralManagerType = TypeVar("GeneralManagerType", bound=GeneralManager)


class DatabaseBucketTypeMismatchError(TypeError):
    """Raised when attempting to combine buckets of different types."""

    def __init__(self, bucket_type: type, other_type: type) -> None:
        """
        Initialize the error for attempting to combine two incompatible bucket types.

        Parameters:
            bucket_type (type): The bucket type used in the operation.
            other_type (type): The other bucket type that is incompatible with `bucket_type`.
        """
        super().__init__(
            f"Cannot combine {bucket_type.__name__} with {other_type.__name__}."
        )


class DatabaseBucketManagerMismatchError(TypeError):
    """Raised when combining buckets backed by different manager classes."""

    def __init__(self, first_manager: type, second_manager: type) -> None:
        """
        Raised when attempting to combine buckets that are backed by different manager classes.

        Parameters:
            first_manager (type): The first manager class involved in the attempted combination.
            second_manager (type): The second manager class involved in the attempted combination.
        """
        super().__init__(
            f"Cannot combine buckets for {first_manager.__name__} and {second_manager.__name__}."
        )


class NonFilterablePropertyError(ValueError):
    """Raised when attempting to filter on a property without filter support."""

    def __init__(self, property_name: str, manager_name: str) -> None:
        """
        Raised when a filter is requested for a GraphQL property that is not marked as filterable on the given manager.

        Parameters:
            property_name (str): The GraphQL property name that was used for filtering.
            manager_name (str): The name of the manager (or manager class) where the property is not filterable.
        """
        super().__init__(
            f"Property '{property_name}' is not filterable in {manager_name}."
        )


class InvalidQueryAnnotationTypeError(TypeError):
    """Raised when a query annotation callback returns a non-queryset value."""

    def __init__(self) -> None:
        """
        Exception raised when a query annotation callback returns a non-QuerySet.

        The exception carries a standardized message: "Query annotation must return a Django QuerySet."
        """
        super().__init__("Query annotation must return a Django QuerySet.")


class QuerysetFilteringError(ValueError):
    """Raised when applying ORM filters fails."""

    def __init__(self, original: Exception) -> None:
        """
        Initialize a QuerysetFilteringError that wraps an original exception raised during ORM filtering.

        Parameters:
            original (Exception): The original exception encountered while filtering the queryset; its message is included in this error's message.
        """
        super().__init__(f"Error filtering queryset: {original}")


class QuerysetOrderingError(ValueError):
    """Raised when applying ORM ordering fails."""

    def __init__(self, original: Exception) -> None:
        """
        Initialize the QuerysetOrderingError by wrapping the originating exception.

        Parameters:
            original (Exception): The original exception raised while ordering the queryset; retained as the wrapped cause.
        """
        super().__init__(f"Error ordering queryset: {original}")


class NonSortablePropertyError(ValueError):
    """Raised when attempting to sort on a property lacking sort support."""

    def __init__(self, property_name: str, manager_name: str) -> None:
        """
        Initialize an error indicating a property cannot be used for sorting on a manager.

        Parameters:
            property_name (str): The name of the property that was requested for sorting.
            manager_name (str): The name of the manager (or manager class) where the property was queried.
        """
        super().__init__(
            f"Property '{property_name}' is not sortable in {manager_name}."
        )


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
        Produce a new DatabaseBucket representing the union of this bucket with another DatabaseBucket or a GeneralManager instance of the same manager class.

        Parameters:
            other (Bucket[GeneralManagerType] | GeneralManagerType): The bucket or manager instance to merge with this bucket.

        Returns:
            DatabaseBucket[GeneralManagerType]: A new bucket containing the combined items from both operands.

        Raises:
            DatabaseBucketTypeMismatchError: If `other` is not a DatabaseBucket of the same class and not a compatible GeneralManager.
            DatabaseBucketManagerMismatchError: If `other` is a DatabaseBucket but uses a different manager class.
        """
        if isinstance(other, GeneralManager) and other.__class__ == self._manager_class:
            return self.__or__(
                self._manager_class.filter(id__in=[other.identification["id"]])
            )
        if not isinstance(other, self.__class__):
            raise DatabaseBucketTypeMismatchError(self.__class__, type(other))
        if self._manager_class != other._manager_class:
            raise DatabaseBucketManagerMismatchError(
                self._manager_class, other._manager_class
            )
        return self.__class__(
            self._data | other._data,
            self._manager_class,
            {},
        )

    def __merge_filter_definitions(
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

    def __parse_filter_definitions(
        self,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, Any], list[tuple[str, Any, str]]]:
        """
        Split provided filter kwargs into three parts: query annotations required by properties, ORM-compatible lookup mappings, and Python-evaluated filter specifications.

        Parameters:
            **kwargs: Filter lookups supplied to `filter` or `exclude`.

        Returns:
            tuple:
                - annotations (dict[str, Any]): Mapping from property name to its `query_annotation` (callable or annotation object) for properties that require ORM annotations.
                - orm_kwargs (dict[str, list[Any]]): Mapping of ORM lookup strings (e.g., "field__lookup") to their values to be passed to the queryset.
                - python_filters (list[tuple[str, Any, str]]): List of tuples (lookup, value, root_property_name) for properties that must be evaluated in Python.

        Raises:
            NonFilterablePropertyError: If a lookup targets a property that is not allowed to be filtered.
        """
        annotations: dict[str, Any] = {}
        orm_kwargs: dict[str, Any] = {}
        python_filters: list[tuple[str, Any, str]] = []
        properties = self._manager_class.Interface.get_graph_ql_properties()

        for k, v in kwargs.items():
            root = k.split("__")[0]
            if root in properties:
                if not properties[root].filterable:
                    raise NonFilterablePropertyError(root, self._manager_class.__name__)
                prop = properties[root]
                if prop.query_annotation is not None:
                    annotations[root] = prop.query_annotation
                    orm_kwargs[k] = v
                else:
                    python_filters.append((k, v, root))
            else:
                orm_kwargs[k] = v

        return annotations, orm_kwargs, python_filters

    def __parse_python_filters(
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
        Return a new DatabaseBucket refined by the given Django-style lookup expressions.

        Parameters:
            **kwargs (Any): Django-style lookup expressions to apply to the underlying queryset.

        Returns:
            DatabaseBucket[GeneralManagerType]: New bucket containing items matching the existing state combined with the provided lookups.

        Raises:
            NonFilterablePropertyError: If a provided property is not filterable for this manager.
            InvalidQueryAnnotationTypeError: If a query-annotation callback returns a non-QuerySet.
            QuerysetFilteringError: If the ORM rejects the filter arguments or filtering fails.
        """
        annotations, orm_kwargs, python_filters = self.__parse_filter_definitions(
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
                raise InvalidQueryAnnotationTypeError()
            qs = qs.annotate(**other_annotations)
        try:
            qs = qs.filter(**orm_kwargs)
        except (FieldError, TypeError, ValueError) as error:
            raise QuerysetFilteringError(error) from error

        if python_filters:
            ids = self.__parse_python_filters(qs, python_filters)
            qs = qs.filter(pk__in=ids)

        merged_filter = self.__merge_filter_definitions(self.filters, **kwargs)
        return self.__class__(qs, self._manager_class, merged_filter, self.excludes)

    def exclude(self, **kwargs: Any) -> DatabaseBucket[GeneralManagerType]:
        """
        Produce a bucket that excludes rows matching the provided Django-style lookup expressions.

        Accepts ORM lookups, query annotation entries, and Python-only filters; annotation callables will be applied to the underlying queryset as needed.

        Parameters:
            **kwargs (Any): Django-style lookup expressions, annotation entries, or property-based filters used to identify records to exclude.

        Returns:
            DatabaseBucket[GeneralManagerType]: A new bucket whose queryset omits rows matching the provided lookups.

        Raises:
            InvalidQueryAnnotationTypeError: If an annotation callable is applied and does not return a Django QuerySet.
        """
        annotations, orm_kwargs, python_filters = self.__parse_filter_definitions(
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
                raise InvalidQueryAnnotationTypeError()
            qs = qs.annotate(**other_annotations)
        qs = qs.exclude(**orm_kwargs)

        if python_filters:
            ids = self.__parse_python_filters(qs, python_filters)
            qs = qs.exclude(pk__in=ids)

        merged_exclude = self.__merge_filter_definitions(self.excludes, **kwargs)
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
        from general_manager.manager.general_manager import GeneralManager

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
        Return a new DatabaseBucket ordered by the given property name(s).

        Accepts a single property name or a tuple of property names. Properties with ORM annotations are applied at the database level; properties without ORM annotations are evaluated in Python and the resulting records are re-ordered while preserving a queryset result. Stable ordering and preservation of manager wrapping are maintained.

        Parameters:
            key (str | tuple[str, ...]): Property name or sequence of property names to sort by, applied in order of appearance.
            reverse (bool): If True, sort each specified key in descending order.

        Returns:
            DatabaseBucket: A new bucket whose underlying queryset is ordered according to the requested keys.

        Raises:
            NonSortablePropertyError: If any requested property is not marked as sortable on the manager's GraphQL properties.
            InvalidQueryAnnotationTypeError: If a property query annotation callable returns a non-QuerySet value.
            QuerysetOrderingError: If the ORM rejects the constructed ordering (e.g., invalid field or incompatible ordering expression).
        """
        if isinstance(key, str):
            key = (key,)
        properties = self._manager_class.Interface.get_graph_ql_properties()
        annotations: dict[str, Any] = {}
        python_keys: list[str] = []
        qs = self._data
        for k in key:
            if k in properties:
                prop = properties[k]
                if not prop.sortable:
                    raise NonSortablePropertyError(k, self._manager_class.__name__)
                if prop.query_annotation is not None:
                    if callable(prop.query_annotation):
                        qs = prop.query_annotation(qs)
                    else:
                        annotations[k] = prop.query_annotation
                else:
                    python_keys.append(k)
        if not isinstance(qs, models.QuerySet):
            raise InvalidQueryAnnotationTypeError()
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
            except (FieldError, TypeError, ValueError) as error:
                raise QuerysetOrderingError(error) from error

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
