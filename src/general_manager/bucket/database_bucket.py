"""Database-backed bucket implementation for GeneralManager collections."""

from __future__ import annotations
from collections.abc import Callable, Hashable, Mapping
from datetime import date, datetime
from typing import Generator, TypeVar, cast

from django.core.exceptions import EmptyResultSet, FieldError
from django.db import models
from django.db.models.sql.query import Query

from general_manager.bucket.base_bucket import Bucket
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import (
    Dependency,
    serialize_dependency_identifier,
)
from general_manager.cache.run_context import current_calculation_run_context
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.filter_parser import create_filter_function

GeneralManagerType = TypeVar("GeneralManagerType", bound=GeneralManager)
LookupValue = object
FilterDefinitions = dict[str, list[LookupValue]]
LookupMapping = dict[str, LookupValue]
PythonFilterDefinition = tuple[str, LookupValue, str]
QueryAnnotation = object
QueryAnnotationCallable = Callable[
    [models.QuerySet[models.Model]],
    models.QuerySet[models.Model],
]
MAX_RUN_SCOPED_BUCKET_RESULT_ROWS = 1000
_QUERY_SIGNATURE_NOT_COMPUTED = object()
_FIRST_ROW_CACHE_MISS = object()
_FIRST_ROW_CACHE_NONE = object()


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


class DatabaseBucketSearchDateMismatchError(ValueError):
    """Raised when combining buckets with different search dates."""

    def __init__(
        self,
        search_date: datetime | date | None,
        other_search_date: datetime | date | None,
    ) -> None:
        """
        Raised when attempting to combine buckets with different search dates.

        Parameters:
            search_date (datetime | date | None): The search date on the first bucket.
            other_search_date (datetime | date | None): The search date on the second bucket.
        """
        super().__init__(
            "Cannot combine buckets with different search_date values: "
            f"{search_date!r} vs {other_search_date!r}."
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


class DuplicateDatabaseBucketSnapshotError(ValueError):
    """Raised when a database bucket snapshot contains duplicate primary keys."""

    def __init__(self) -> None:
        super().__init__(
            "DatabaseBucket snapshots cannot contain duplicate primary keys."
        )


def _ensure_unique_primary_keys(primary_keys: tuple[LookupValue, ...]) -> None:
    if len(primary_keys) != len(set(primary_keys)):
        raise DuplicateDatabaseBucketSnapshotError()


def _restore_database_bucket_from_primary_keys(
    model: type[models.Model],
    manager_class: type[GeneralManagerType],
    primary_keys: tuple[LookupValue, ...],
    filter_definitions: FilterDefinitions,
    exclude_definitions: FilterDefinitions,
    database_alias: str | None,
    search_date: datetime | date | None,
    sort_keys: tuple[str, ...] | None,
    sort_reverse: bool,
) -> DatabaseBucket[GeneralManagerType]:
    _ensure_unique_primary_keys(primary_keys)
    manager = model._default_manager
    if database_alias is not None:
        manager = manager.db_manager(database_alias)
    if not primary_keys:
        queryset = manager.none()
    else:
        preserved_order = models.Case(
            *(
                models.When(pk=primary_key, then=position)
                for position, primary_key in enumerate(primary_keys)
            ),
            output_field=models.IntegerField(),
        )
        queryset = manager.filter(pk__in=primary_keys).order_by(preserved_order)
    return DatabaseBucket(
        queryset,
        manager_class,
        filter_definitions,
        exclude_definitions,
        search_date=search_date,
        sort_keys=sort_keys,
        sort_reverse=sort_reverse,
    )


class DatabaseBucket(Bucket[GeneralManagerType]):
    """Bucket implementation backed by Django ORM querysets."""

    def __init__(
        self,
        data: models.QuerySet[models.Model],
        manager_class: type[GeneralManagerType],
        filter_definitions: FilterDefinitions | None = None,
        exclude_definitions: FilterDefinitions | None = None,
        *,
        search_date: datetime | date | None = None,
        sort_keys: tuple[str, ...] | None = None,
        sort_reverse: bool = False,
        run_scoped_cacheable: bool = True,
    ) -> None:
        """
        Instantiate a database-backed bucket with optional filter state.

        Filter and exclude definitions are copied into bucket-owned dictionaries,
        so later mutations to caller-provided mappings do not change this
        bucket. Pickle reconstruction restores through primary-key snapshots
        rather than by serializing the queryset object.

        Parameters:
            data (models.QuerySet[models.Model]): Queryset providing the underlying data.
            manager_class (type[GeneralManagerType]): GeneralManager subclass used to wrap rows.
            filter_definitions (FilterDefinitions | None): Pre-existing filter expressions captured from parent buckets.
            exclude_definitions (FilterDefinitions | None): Pre-existing exclusion expressions captured from parent buckets.
            search_date (datetime | date | None): Optional timestamp applied when instantiating manager instances.
            sort_keys (tuple[str, ...] | None): Property names used by a previous bucket sort operation.
            sort_reverse (bool): Whether sorted keys should be interpreted in descending order for dependency tracking.
            run_scoped_cacheable (bool): Whether terminal results from this bucket are safe to reuse inside the active calculation run.

        Returns:
            None
        """
        self._data: models.QuerySet[models.Model] = data
        self._manager_class = manager_class
        self.filters: FilterDefinitions = self._copy_filter_definitions(
            filter_definitions
        )
        self.excludes: FilterDefinitions = self._copy_filter_definitions(
            exclude_definitions
        )
        self._search_date = search_date
        self._sort_keys = sort_keys
        self._sort_reverse = sort_reverse
        self._run_scoped_cacheable = run_scoped_cacheable
        self._query_signature_cache: tuple[Hashable, ...] | None | object = (
            _QUERY_SIGNATURE_NOT_COMPUTED
        )
        self._trusted_query_signature: Hashable | None = None

    def _set_trusted_query_signature(self, signature: Hashable | None) -> None:
        """Set an internal non-SQL signature for framework-built querysets."""
        self._trusted_query_signature = signature
        self._query_signature_cache = _QUERY_SIGNATURE_NOT_COMPUTED

    def _copy_for_run_context_reuse(self) -> DatabaseBucket[GeneralManagerType]:
        """Return an unexposed bucket copy that reuses the same trusted queryset."""

        bucket = self.__class__(
            self._data,
            self._manager_class,
            self.filters,
            self.excludes,
            search_date=self._search_date,
            sort_keys=self._sort_keys,
            sort_reverse=self._sort_reverse,
            run_scoped_cacheable=self._run_scoped_cacheable,
        )
        bucket._set_trusted_query_signature(self._trusted_query_signature)
        return bucket

    @staticmethod
    def _freeze_trusted_signature_payload(value: object) -> Hashable:
        if isinstance(value, Mapping):
            return tuple(
                (
                    str(key),
                    DatabaseBucket._freeze_trusted_signature_payload(item_value),
                )
                for key, item_value in sorted(
                    value.items(),
                    key=lambda item: str(item[0]),
                )
            )
        if isinstance(value, (list, tuple)):
            return tuple(
                DatabaseBucket._freeze_trusted_signature_payload(item) for item in value
            )
        if isinstance(value, (set, frozenset)):
            return tuple(
                sorted(
                    (
                        DatabaseBucket._freeze_trusted_signature_payload(item)
                        for item in value
                    ),
                    key=repr,
                )
            )
        if isinstance(value, models.Model):
            return ("model", value.__class__, value.pk)
        try:
            hash(value)
        except TypeError:
            return ("serialized", serialize_dependency_identifier(value))
        return value

    def _trusted_query_signature_with(
        self,
        operation: str,
        kwargs: Mapping[str, object],
    ) -> Hashable | None:
        if self._trusted_query_signature is None:
            return None
        return (
            self._trusted_query_signature,
            operation,
            self._freeze_trusted_signature_payload(kwargs),
        )

    def __reduce__(self) -> str | tuple[object, ...]:
        """
        Preserve a result snapshot without serializing the queryset object.

        Reducing a bucket records the effective filter/exclude dependencies,
        materializes primary keys in queryset order, rejects duplicate primary
        keys with `DuplicateDatabaseBucketSnapshotError`, and restores through
        the original model default manager on the original database alias by
        filtering those primary keys and preserving the snapshot order.
        """
        self._track_effective_dependencies()
        primary_keys = tuple(self._data.values_list("pk", flat=True))
        _ensure_unique_primary_keys(primary_keys)
        return (
            _restore_database_bucket_from_primary_keys,
            (
                self._data.model,
                self._manager_class,
                primary_keys,
                self.filters,
                self.excludes,
                self._data.db,
                self._search_date,
                self._sort_keys,
                self._sort_reverse,
            ),
        )

    def _build_manager_from_primary_key(self, pk: object) -> GeneralManagerType:
        if self._search_date is None:
            return self._manager_class(pk)
        return self._manager_class(pk, search_date=self._search_date)

    def _can_trust_orm_instance(self, instance: models.Model) -> bool:
        interface_model = getattr(self._manager_class.Interface, "_model", None)
        if isinstance(interface_model, type) and issubclass(
            interface_model, models.Model
        ):
            if not isinstance(instance, interface_model):
                return False
        get_deferred_fields = getattr(instance, "get_deferred_fields", None)
        if callable(get_deferred_fields) and get_deferred_fields():
            return False
        if self._search_date is not None and not (
            hasattr(instance, "_history") or hasattr(instance, "history_date")
        ):
            return False
        return True

    def _build_manager_from_instance(
        self,
        instance: models.Model,
    ) -> GeneralManagerType:
        interface_hydrate = getattr(
            self._manager_class.Interface, "_from_trusted_orm_instance", None
        )
        manager_hydrate = self._manager_class._from_trusted_orm_instance
        if callable(interface_hydrate) and self._can_trust_orm_instance(instance):
            return manager_hydrate(instance, search_date=self._search_date)
        return self._build_manager_from_primary_key(instance.pk)

    def _build_manager(self, value: LookupValue) -> GeneralManagerType:
        if isinstance(value, models.Model):
            return self._build_manager_from_instance(value)
        return self._build_manager_from_primary_key(value)

    @staticmethod
    def _copy_filter_definitions(
        definitions: Mapping[
            str, LookupValue | list[LookupValue] | tuple[LookupValue, ...]
        ]
        | None,
    ) -> FilterDefinitions:
        """
        Return a copy of filter/exclude definitions without sharing nested lists.
        """
        copied: FilterDefinitions = {}
        for key, values in (definitions or {}).items():
            if isinstance(values, list):
                copied[key] = list(values)
            elif isinstance(values, tuple):
                copied[key] = list(values)
            else:
                copied[key] = [values]
        return copied

    @staticmethod
    def _normalize_dependency_mapping(
        definitions: Mapping[str, LookupValue],
    ) -> LookupMapping:
        return {
            key: values[0]
            if isinstance(values, (list, tuple)) and len(values) == 1
            else values
            for key, values in definitions.items()
        }

    def _query_signature(self) -> tuple[Hashable, ...] | None:
        """
        Return a conservative run-scoped cache signature for this queryset.

        The signature is only produced for queryset shapes whose SQL, database
        alias, manager class, model, search date, and sort state can safely
        distinguish equivalent terminal results. Unsupported or risky query
        forms return ``None`` so callers fall back to normal ORM evaluation.
        Bypass cases include select-for-update queries, combined queries,
        distinct queries, prefetch-related lookups, deferred field loading, and
        SQL generation errors from empty, invalid, or incompatible querysets.

        Returns:
            tuple[Hashable, ...] | None: Cache key components for equivalent
            queryset results, or ``None`` when reuse should be bypassed.
        """
        if self._query_signature_cache is not _QUERY_SIGNATURE_NOT_COMPUTED:
            return cast(tuple[Hashable, ...] | None, self._query_signature_cache)
        if not self._run_scoped_cacheable:
            self._query_signature_cache = None
            return None
        if self._trusted_query_signature is not None:
            signature = (
                self._manager_class,
                self._data.model,
                self._data.db,
                "trusted",
                self._trusted_query_signature,
                self._search_date,
                self._sort_keys,
                self._sort_reverse,
            )
            self._query_signature_cache = signature
            return signature
        query = self._data.query
        if not isinstance(query, Query):
            self._query_signature_cache = None
            return None
        if query.select_for_update:
            self._query_signature_cache = None
            return None
        if query.combinator:
            self._query_signature_cache = None
            return None
        if query.distinct:
            self._query_signature_cache = None
            return None
        if getattr(self._data, "_prefetch_related_lookups", ()):
            self._query_signature_cache = None
            return None
        deferred_loading = getattr(query, "deferred_loading", None)
        if isinstance(deferred_loading, tuple) and deferred_loading[0]:
            self._query_signature_cache = None
            return None
        try:
            sql, params = self._data.query.sql_with_params()
        except (EmptyResultSet, FieldError, TypeError, ValueError):
            self._query_signature_cache = None
            return None
        signature = (
            self._manager_class,
            self._data.model,
            self._data.db,
            sql,
            tuple(params),
            self._search_date,
            self._sort_keys,
            self._sort_reverse,
        )
        self._query_signature_cache = signature
        return signature

    def _bucket_index_source_signature(self) -> Hashable:
        """Return a queryset signature when safe, otherwise use object identity."""
        query_signature = self._query_signature()
        if query_signature is None:
            return super()._bucket_index_source_signature()
        return ("database", query_signature)

    def _get_run_scoped_primary_keys(self) -> tuple[LookupValue, ...] | None:
        """
        Load or reuse a bounded primary-key snapshot for this bucket.

        The method materializes at most ``MAX_RUN_SCOPED_BUCKET_RESULT_ROWS``
        primary keys and stores them in the active
        :class:`CalculationRunContext`. Buckets without an active context,
        without a safe signature, or above the materialization guardrail return
        ``None`` so terminal operations continue through the database.

        Returns:
            tuple[object, ...] | None: Cached or newly materialized primary keys,
            or ``None`` when run-scoped reuse is unavailable.
        """
        context = current_calculation_run_context()
        if context is None:
            return None
        signature = self._query_signature()
        if signature is None:
            return None
        cached = context.get_orm_bucket_result(signature)
        if cached is not None:
            return cast(tuple[LookupValue, ...], cached)

        if self._data.ordered:
            primary_keys = tuple(
                self._data.values_list("pk", flat=True)[
                    : MAX_RUN_SCOPED_BUCKET_RESULT_ROWS + 1
                ]
            )
        else:
            # Preserve the order normal queryset iteration would expose.
            primary_keys = tuple(
                row.pk for row in self._data[: MAX_RUN_SCOPED_BUCKET_RESULT_ROWS + 1]
            )
        if len(primary_keys) > MAX_RUN_SCOPED_BUCKET_RESULT_ROWS:
            return None
        context.set_orm_bucket_result(signature, primary_keys)
        return primary_keys

    def _get_run_scoped_rows(self) -> tuple[models.Model, ...] | None:
        """
        Load or reuse a bounded model-row snapshot for trusted hydration.
        """
        context = current_calculation_run_context()
        if context is None:
            return None
        signature = self._query_signature()
        if signature is None:
            return None
        cached = context.get_orm_bucket_rows(signature)
        if cached is not None:
            return cast(tuple[models.Model, ...], cached)

        rows = tuple(self._data[: MAX_RUN_SCOPED_BUCKET_RESULT_ROWS + 1])
        if len(rows) > MAX_RUN_SCOPED_BUCKET_RESULT_ROWS:
            return None
        primary_keys = tuple(row.pk for row in rows)
        context.set_orm_bucket_rows(signature, rows)
        context.set_orm_bucket_result(signature, primary_keys)
        return rows

    def _peek_run_scoped_primary_keys(self) -> tuple[LookupValue, ...] | None:
        """
        Return an already cached primary-key snapshot without evaluating the ORM.

        Terminal operations such as ``count()`` and membership checks call this
        helper so they can reuse a snapshot created by iteration without forcing
        materialization on their own.

        Returns:
            tuple[object, ...] | None: Cached primary keys for this bucket, or
            ``None`` when no snapshot exists.
        """
        context = current_calculation_run_context()
        if context is None:
            return None
        signature = self._query_signature()
        if signature is None:
            return None
        cached = context.get_orm_bucket_result(signature)
        if cached is None:
            return None
        return cast(tuple[LookupValue, ...], cached)

    def _peek_run_scoped_rows(self) -> tuple[models.Model, ...] | None:
        """Return cached model rows without evaluating the ORM."""
        context = current_calculation_run_context()
        if context is None:
            return None
        signature = self._query_signature()
        if signature is None:
            return None
        cached = context.get_orm_bucket_rows(signature)
        if cached is None:
            return None
        return cast(tuple[models.Model, ...], cached)

    def _can_cache_run_scoped_managers(self) -> bool:
        if self._manager_class.__init__ is not GeneralManager.__init__:
            return False
        return callable(
            getattr(self._manager_class.Interface, "_from_trusted_orm_instance", None)
        )

    def _manager_identification_dependencies(
        self,
        managers: tuple[GeneralManagerType, ...],
    ) -> frozenset[Dependency]:
        manager_name = type.__getattribute__(self._manager_class, "__name__")
        return frozenset(
            (
                manager_name,
                "identification",
                serialize_dependency_identifier(manager.identification),
            )
            for manager in managers
        )

    def _get_run_scoped_managers(self) -> tuple[GeneralManagerType, ...] | None:
        """Load or reuse manager wrappers for a trusted row snapshot."""
        if not self._can_cache_run_scoped_managers():
            return None
        context = current_calculation_run_context()
        if context is None:
            return None
        signature = self._query_signature()
        if signature is None:
            return None
        cached = context.get_orm_bucket_managers(signature)
        if cached is not None:
            managers = cast(tuple[GeneralManagerType, ...], cached)
            dependencies = context.get_orm_bucket_manager_dependencies(signature)
            if dependencies is not None:
                DependencyTracker._track_many_validated(dependencies)
            else:
                for manager in managers:
                    self._manager_class._track_identification_dependency(
                        manager.identification
                    )
            return managers
        rows = self._get_run_scoped_rows()
        if rows is None:
            return None
        managers = tuple(self._build_manager_from_instance(row) for row in rows)
        context.set_orm_bucket_managers(
            signature,
            managers,
            self._manager_identification_dependencies(managers),
        )
        return managers

    @staticmethod
    def _snapshot_get_primary_key(
        kwargs: Mapping[str, LookupValue],
    ) -> LookupValue | None:
        """
        Extract the primary key from a snapshot-safe ``get()`` lookup.

        Only single-key ``pk`` and ``id`` lookups can be answered from the
        cached primary-key tuple while preserving Django's ``QuerySet.get()``
        semantics. All other lookup shapes return ``None`` and use the normal
        ORM path.

        Parameters:
            kwargs (Mapping[str, object]): Lookup arguments passed to ``get()``.

        Returns:
            object | None: Requested primary key when the lookup is snapshot-safe,
            otherwise ``None``.
        """
        if len(kwargs) != 1:
            return None
        if "pk" in kwargs:
            return kwargs["pk"]
        if "id" in kwargs:
            return kwargs["id"]
        return None

    def _can_materialize_count_snapshot(self) -> bool:
        """Return whether count/len may populate a bounded run-scoped snapshot."""
        context = current_calculation_run_context()
        if context is None:
            return False
        query = getattr(self._data, "query", None)
        if not isinstance(query, Query):
            return False
        where = getattr(query, "where", None)
        children = getattr(where, "children", None)
        return bool(children)

    def _track_effective_dependencies(self) -> None:
        """Record the bucket's effective filter/exclude state when it is evaluated."""
        manager_name = type.__getattribute__(self._manager_class, "__name__")
        needs_sort_payload = bool(self._sort_keys)
        normalized_filters = (
            self._normalize_dependency_mapping(self.filters)
            if self.filters or needs_sort_payload
            else {}
        )
        normalized_excludes = (
            self._normalize_dependency_mapping(self.excludes)
            if self.excludes or needs_sort_payload
            else {}
        )
        if self.filters:
            DependencyTracker._track_validated(
                manager_name,
                "filter",
                serialize_dependency_identifier(normalized_filters),
            )
        else:
            DependencyTracker._track_validated(manager_name, "all", "")
        if self.excludes:
            DependencyTracker._track_validated(
                manager_name,
                "exclude",
                serialize_dependency_identifier(normalized_excludes),
            )
        if self._sort_keys:
            for sort_key in self._sort_keys:
                payload = {
                    "filters": normalized_filters,
                    "excludes": normalized_excludes,
                    "reverse": self._sort_reverse,
                }
                DependencyTracker._track_validated(
                    manager_name,
                    "filter",
                    serialize_dependency_identifier({f"__sort__{sort_key}": payload}),
                )

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        """
        Iterate over manager instances corresponding to the queryset rows.

        Safe run-scoped row snapshots are reused when present. Otherwise cached
        primary-key snapshots are reused when available, and the queryset is
        iterated as trusted ORM rows when possible. Rows are trusted only when
        they match the interface model, are not deferred, and historical buckets
        expose history state. Untrusted rows fall back to primary-key manager
        construction. `search_date` is passed through when managers are built
        from primary keys or trusted rows.

        Yields:
            GeneralManagerType: Manager instance for each primary key in the queryset.
        """
        self._track_effective_dependencies()
        managers = self._get_run_scoped_managers()
        if managers is not None:
            yield from managers
            return
        rows = self._get_run_scoped_rows()
        if rows is not None:
            for row in rows:
                yield self._build_manager_from_instance(row)
            return
        primary_keys = self._peek_run_scoped_primary_keys()
        if primary_keys is not None:
            for primary_key in primary_keys:
                yield self._build_manager_from_primary_key(primary_key)
            return
        for item in self._data:
            yield self._build_manager_from_instance(item)

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
            DatabaseBucketSearchDateMismatchError: If both buckets target the same manager but have different search dates.
        """
        if isinstance(other, GeneralManager) and other.__class__ == self._manager_class:
            return self.__or__(
                self._manager_class.filter(
                    id__in=[other.identification["id"]],
                    search_date=self._search_date,
                )
            )
        if not isinstance(other, self.__class__):
            raise DatabaseBucketTypeMismatchError(self.__class__, type(other))
        if self._manager_class != other._manager_class:
            raise DatabaseBucketManagerMismatchError(
                self._manager_class, other._manager_class
            )
        if self._search_date != other._search_date:
            raise DatabaseBucketSearchDateMismatchError(
                self._search_date, other._search_date
            )
        filters = self._copy_filter_definitions(self.filters)
        for key, values in other.filters.items():
            filters.setdefault(key, []).extend(values)
        excludes = self._copy_filter_definitions(self.excludes)
        for key, values in other.excludes.items():
            excludes.setdefault(key, []).extend(values)
        return self.__class__(
            self._data | other._data,
            self._manager_class,
            filters,
            excludes,
            search_date=self._search_date,
            sort_keys=self._sort_keys,
            sort_reverse=self._sort_reverse,
            run_scoped_cacheable=False,
        )

    def __merge_filter_definitions(
        self, basis: FilterDefinitions, **kwargs: LookupValue
    ) -> FilterDefinitions:
        """
        Merge stored filter definitions with additional lookup values.

        Parameters:
            basis (FilterDefinitions): Existing lookup definitions copied into the result.
            **kwargs: New lookups whose values are appended to the result mapping.

        Returns:
            FilterDefinitions: Combined mapping of lookups to value lists.
        """
        kwarg_filter = self._copy_filter_definitions(basis)
        for key, value in kwargs.items():
            if key not in kwarg_filter:
                kwarg_filter[key] = []
            kwarg_filter[key].append(value)
        return kwarg_filter

    def __parse_filter_definitions(
        self,
        **kwargs: LookupValue,
    ) -> tuple[dict[str, QueryAnnotation], LookupMapping, list[PythonFilterDefinition]]:
        """
        Split provided filter kwargs into three parts: query annotations required by properties, ORM-compatible lookup mappings, and Python-evaluated filter specifications.

        Parameters:
            **kwargs: Filter lookups supplied to `filter` or `exclude`.

        Returns:
            tuple:
                - annotations (dict[str, object]): Mapping from property name to its `query_annotation` (callable or annotation object) for properties that require ORM annotations.
                - orm_kwargs (dict[str, object]): Mapping of ORM lookup strings (e.g., "field__lookup") to their values to be passed to the queryset.
                - python_filters (list[tuple[str, object, str]]): List of tuples (lookup, value, root_property_name) for properties that must be evaluated in Python.

        Raises:
            NonFilterablePropertyError: If a lookup targets a property that is not allowed to be filtered.
        """
        annotations: dict[str, QueryAnnotation] = {}
        orm_kwargs: LookupMapping = {}
        python_filters: list[PythonFilterDefinition] = []
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
        self,
        query_set: models.QuerySet[models.Model],
        python_filters: list[PythonFilterDefinition],
    ) -> list[object]:
        """
        Evaluate Python-only filters and return the primary keys that satisfy them.

        Parameters:
            query_set (models.QuerySet[models.Model]): Queryset to inspect.
            python_filters (list[tuple[str, object, str]]): Filters requiring Python evaluation, each containing the lookup, value, and property root.

        Returns:
            list[object]: Primary keys of rows that meet all Python-evaluated filters.
        """
        ids: list[object] = []
        for obj in query_set:
            inst = self._build_manager_from_instance(obj)
            keep = True
            for k, val, root in python_filters:
                lookup = k.split("__", 1)[1] if "__" in k else ""
                func = create_filter_function(lookup, val)
                if not func(getattr(inst, root)):
                    keep = False
                    break
            if keep:
                ids.append(cast(object, obj.pk))
        return ids

    def filter(self, **kwargs: LookupValue) -> DatabaseBucket[GeneralManagerType]:
        """
        Return a new DatabaseBucket refined by the given Django-style lookup expressions.

        ``search_date`` is a reserved kwarg consumed by the bucket and preserved
        on the returned bucket. Remaining kwargs are split between ORM lookups,
        query-annotated properties, and Python-only property filters. Python-only
        property filters evaluate the candidate rows in Python and narrow the
        queryset by the matching primary keys. Stored filter definitions are
        copied before the new lookups are appended, so parent and child buckets
        do not share nested lookup lists.

        Parameters:
            **kwargs (object): Django-style lookup expressions to apply to the underlying queryset, plus optional reserved `search_date`.

        Returns:
            DatabaseBucket[GeneralManagerType]: New bucket containing items matching the existing state combined with the provided lookups.

        Raises:
            NonFilterablePropertyError: If a provided property is not filterable for this manager.
            InvalidQueryAnnotationTypeError: If a query-annotation callback returns a non-QuerySet.
            QuerysetFilteringError: If Django raises `FieldError`, `TypeError`, or `ValueError` while applying ORM filters.
        """
        search_date = cast(
            datetime | date | None,
            kwargs.pop("search_date", self._search_date),
        )
        annotations, orm_kwargs, python_filters = self.__parse_filter_definitions(
            **kwargs
        )
        qs = self._data
        if annotations:
            other_annotations: dict[str, QueryAnnotation] = {}
            for key, value in annotations.items():
                if not callable(value):
                    other_annotations[key] = value
                    continue
                query_annotation = cast(QueryAnnotationCallable, value)
                qs = query_annotation(qs)
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
        bucket = self.__class__(
            qs,
            self._manager_class,
            merged_filter,
            self.excludes,
            search_date=search_date,
            sort_keys=self._sort_keys,
            sort_reverse=self._sort_reverse,
            run_scoped_cacheable=self._run_scoped_cacheable,
        )
        if not annotations and not python_filters:
            bucket._set_trusted_query_signature(
                self._trusted_query_signature_with("filter", orm_kwargs)
            )
        return bucket

    def exclude(self, **kwargs: LookupValue) -> DatabaseBucket[GeneralManagerType]:
        """
        Produce a bucket that excludes rows matching the provided Django-style lookup expressions.

        Accepts ORM lookups, query annotation entries, and Python-only filters; annotation callables will be applied to the underlying queryset as needed.
        ``search_date`` is a reserved kwarg consumed by the bucket and preserved on the returned bucket.
        Python-only property filters evaluate candidate rows in Python and then exclude the matching primary keys.
        Stored exclude definitions are copied before new lookups are appended,
        so parent and child buckets do not share nested lookup lists.

        Parameters:
            **kwargs (object): Django-style lookup expressions, annotation entries, or property-based filters used to identify records to exclude.

        Returns:
            DatabaseBucket[GeneralManagerType]: A new bucket whose queryset omits rows matching the provided lookups.

        Raises:
            NonFilterablePropertyError: If a provided property is not filterable for this manager.
            InvalidQueryAnnotationTypeError: If an annotation callable is applied and does not return a Django QuerySet.
            QuerysetFilteringError: If Django raises `FieldError`, `TypeError`, or `ValueError` while applying ORM excludes.
        """
        search_date = cast(
            datetime | date | None,
            kwargs.pop("search_date", self._search_date),
        )
        annotations, orm_kwargs, python_filters = self.__parse_filter_definitions(
            **kwargs
        )
        qs = self._data
        if annotations:
            other_annotations: dict[str, QueryAnnotation] = {}
            for key, value in annotations.items():
                if not callable(value):
                    other_annotations[key] = value
                    continue
                query_annotation = cast(QueryAnnotationCallable, value)
                qs = query_annotation(qs)
            if not isinstance(qs, models.QuerySet):
                raise InvalidQueryAnnotationTypeError()
            qs = qs.annotate(**other_annotations)
        try:
            qs = qs.exclude(**orm_kwargs)
        except (FieldError, TypeError, ValueError) as error:
            raise QuerysetFilteringError(error) from error

        if python_filters:
            ids = self.__parse_python_filters(qs, python_filters)
            qs = qs.exclude(pk__in=ids)

        merged_exclude = self.__merge_filter_definitions(self.excludes, **kwargs)
        bucket = self.__class__(
            qs,
            self._manager_class,
            self.filters,
            merged_exclude,
            search_date=search_date,
            sort_keys=self._sort_keys,
            sort_reverse=self._sort_reverse,
            run_scoped_cacheable=self._run_scoped_cacheable,
        )
        if not annotations and not python_filters:
            bucket._set_trusted_query_signature(
                self._trusted_query_signature_with("exclude", orm_kwargs)
            )
        return bucket

    def first(self) -> GeneralManagerType | None:
        """
        Return the first row in the queryset as a manager instance.

        Reuses safe cached row snapshots or primary-key snapshots when present;
        otherwise delegates to queryset `first()`.

        Returns:
            GeneralManagerType | None: First manager instance if available.
        """
        self._track_effective_dependencies()
        rows = self._peek_run_scoped_rows()
        if rows is not None:
            if not rows:
                return None
            return self._build_manager_from_instance(rows[0])
        primary_keys = self._peek_run_scoped_primary_keys()
        if primary_keys is not None:
            if not primary_keys:
                return None
            return self._build_manager_from_primary_key(primary_keys[0])
        context = current_calculation_run_context()
        signature = self._query_signature() if context is not None else None
        if context is not None and signature is not None:
            cached = context.get_orm_bucket_first_row(
                signature,
                _FIRST_ROW_CACHE_MISS,
            )
            if cached is _FIRST_ROW_CACHE_NONE:
                return None
            if cached is not _FIRST_ROW_CACHE_MISS:
                return self._build_manager_from_instance(cast(models.Model, cached))
        first_element = self._data.first()
        if context is not None and signature is not None:
            context.set_orm_bucket_first_row(
                signature,
                _FIRST_ROW_CACHE_NONE if first_element is None else first_element,
            )
        if first_element is None:
            return None
        return self._build_manager_from_instance(first_element)

    def last(self) -> GeneralManagerType | None:
        """
        Return the last row in the queryset as a manager instance.

        Reuses safe cached row snapshots or primary-key snapshots when present;
        otherwise delegates to queryset `last()`.

        Returns:
            GeneralManagerType | None: Last manager instance if available.
        """
        self._track_effective_dependencies()
        rows = self._peek_run_scoped_rows()
        if rows is not None:
            if not rows:
                return None
            return self._build_manager_from_instance(rows[-1])
        primary_keys = self._peek_run_scoped_primary_keys()
        if primary_keys is not None:
            if not primary_keys:
                return None
            return self._build_manager_from_primary_key(primary_keys[-1])
        first_element = self._data.last()
        if first_element is None:
            return None
        return self._build_manager_from_instance(first_element)

    def count(self) -> int:
        """
        Count the number of rows represented by the bucket.

        A safe run-scoped primary-key snapshot is reused when present; otherwise
        this delegates to the queryset count.

        Returns:
            int: Number of represented rows.
        """
        self._track_effective_dependencies()
        if self._can_materialize_count_snapshot():
            rows = self._get_run_scoped_rows()
            if rows is not None:
                return len(rows)
        primary_keys = self._peek_run_scoped_primary_keys()
        if primary_keys is not None:
            return len(primary_keys)
        return int(self._data.count())

    def all(self) -> DatabaseBucket[GeneralManagerType]:
        """
        Return a new lazy bucket wrapping ``self._data.all()``.

        Filter definitions, exclusion definitions, search date, sort metadata,
        and run-scoped cacheability are preserved on the returned bucket.

        Returns:
            DatabaseBucket[GeneralManagerType]: Bucket encapsulating `self._data.all()`.
        """
        bucket = self.__class__(
            self._data.all(),
            self._manager_class,
            self.filters,
            self.excludes,
            search_date=self._search_date,
            sort_keys=self._sort_keys,
            sort_reverse=self._sort_reverse,
            run_scoped_cacheable=self._run_scoped_cacheable,
        )
        bucket._set_trusted_query_signature(self._trusted_query_signature)
        return bucket

    def get(self, **kwargs: LookupValue) -> GeneralManagerType:
        """
        Retrieve a single manager instance matching the provided lookups.

        When a run-scoped row or primary-key snapshot is already available and
        the lookup is exactly one `pk` or `id` value, the snapshot is used before
        falling back to queryset ``get()``. Snapshot lookups preserve the
        queryset's `DoesNotExist` and `MultipleObjectsReturned` behavior.

        Parameters:
            **kwargs (object): Field lookups resolved via `QuerySet.get`.

        Returns:
            GeneralManagerType: Manager instance wrapping the matched model.

        Raises:
            models.ObjectDoesNotExist: Propagated from the underlying queryset when no row matches.
            models.MultipleObjectsReturned: Propagated when multiple rows satisfy the lookup.
        """
        self._track_effective_dependencies()
        rows = self._peek_run_scoped_rows()
        if rows is not None:
            requested_primary_key = self._snapshot_get_primary_key(kwargs)
            if requested_primary_key is not None:
                matches = [row for row in rows if row.pk == requested_primary_key]
                if len(matches) == 1:
                    return self._build_manager_from_instance(matches[0])
                if len(matches) > 1:
                    raise self._data.model.MultipleObjectsReturned
                raise self._data.model.DoesNotExist
        primary_keys = self._peek_run_scoped_primary_keys()
        if primary_keys is not None:
            requested_primary_key = self._snapshot_get_primary_key(kwargs)
            if requested_primary_key is not None:
                match_count = primary_keys.count(requested_primary_key)
                if match_count == 1:
                    return self._build_manager_from_primary_key(requested_primary_key)
                if match_count > 1:
                    raise self._data.model.MultipleObjectsReturned
                raise self._data.model.DoesNotExist
        element = self._data.get(**kwargs)
        return self._build_manager_from_instance(element)

    def __getitem__(
        self, item: int | slice
    ) -> GeneralManagerType | DatabaseBucket[GeneralManagerType]:
        """
        Access manager instances by index or obtain a sliced bucket.

        Slices return a lazy DatabaseBucket. Scalar indexes return a manager
        instance; when a run-scoped snapshot path is used, negative scalar
        indexes raise ValueError. Otherwise normal Django queryset indexing
        exceptions propagate.

        Parameters:
            item (int | slice): Index of the desired row or slice object describing a range.

        Returns:
            GeneralManagerType | DatabaseBucket[GeneralManagerType]: Manager instance for single indices or bucket wrapping the sliced queryset.
        """
        if isinstance(item, slice):
            return self.__class__(
                self._data[item],
                self._manager_class,
                self.filters,
                self.excludes,
                search_date=self._search_date,
                sort_keys=self._sort_keys,
                sort_reverse=self._sort_reverse,
                run_scoped_cacheable=self._run_scoped_cacheable,
            )
        self._track_effective_dependencies()
        rows = self._peek_run_scoped_rows()
        if rows is not None:
            if item < 0:
                raise ValueError
            return self._build_manager_from_instance(rows[item])
        primary_keys = self._peek_run_scoped_primary_keys()
        if primary_keys is not None:
            if item < 0:
                raise ValueError
            return self._build_manager_from_primary_key(primary_keys[item])
        return self._build_manager_from_instance(self._data[item])

    def __bool__(self) -> bool:
        """
        Return whether the bucket contains at least one row without counting all rows.

        Truthiness is an existence check, not a cardinality request. Reuse any
        active run-context snapshot first, then cache a queryset ``exists()``
        result for equivalent trusted querysets in the same calculation run.
        """
        self._track_effective_dependencies()
        rows = self._peek_run_scoped_rows()
        if rows is not None:
            return bool(rows)
        primary_keys = self._peek_run_scoped_primary_keys()
        if primary_keys is not None:
            return bool(primary_keys)
        context = current_calculation_run_context()
        signature = self._query_signature() if context is not None else None
        if context is not None and signature is not None:
            cached = context.get_orm_bucket_exists(signature)
            if cached is not None:
                return bool(cached)
        exists = bool(self._data.exists())
        if context is not None and signature is not None:
            context.set_orm_bucket_exists(signature, exists)
        return exists

    def __len__(self) -> int:
        """
        Return the number of rows represented by the bucket.

        Delegates to `count()` so dependency tracking, run-scoped snapshots, and
        queryset fallback behavior stay aligned between both cardinality paths.

        Returns:
            int: Number of represented rows.
        """
        return self.count()

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

        Manager instances are matched by ``identification["id"]`` and Django
        models by ``pk``. Missing identifiers return False. A safe run-scoped
        primary-key snapshot is used when present; otherwise the bucket performs
        a targeted queryset ``exists()`` lookup.

        Parameters:
            item (GeneralManagerType | models.Model): Manager or model instance whose primary key is checked.

        Returns:
            bool: True when the primary key exists in the queryset.
        """
        from general_manager.manager.general_manager import GeneralManager

        self._track_effective_dependencies()
        if isinstance(item, GeneralManager):
            pk = item.identification.get("id", None)
        else:
            pk = item.pk
        if pk is None:
            return False
        primary_keys = self._peek_run_scoped_primary_keys()
        if primary_keys is not None:
            return pk in primary_keys
        return bool(self._data.filter(pk=pk).exists())

    def sort(
        self,
        key: tuple[str, ...] | str,
        reverse: bool = False,
    ) -> DatabaseBucket[GeneralManagerType]:
        """
        Return a new DatabaseBucket ordered by the given property name(s).

        Accepts a single property name or a tuple of property names. Properties with ORM annotations are applied at the database level; properties without ORM annotations are evaluated in Python and the resulting records are re-ordered while preserving a queryset result. Python-only sorts materialize candidate rows, sort them in memory, then preserve that materialized order with a Django `Case` annotation. Stable ordering and preservation of manager wrapping are maintained.

        Parameters:
            key (str | tuple[str, ...]): Property name or sequence of property names to sort by, applied in order of appearance.
            reverse (bool): If True, sort each specified key in descending order.

        Returns:
            DatabaseBucket[GeneralManagerType]: A new bucket whose underlying queryset is ordered according to the requested keys.

        Raises:
            NonSortablePropertyError: If any requested property is not marked as sortable on the manager's GraphQL properties.
            InvalidQueryAnnotationTypeError: If a property query annotation callable returns a non-QuerySet value.
            QuerysetOrderingError: If the ORM rejects the constructed ordering (e.g., invalid field or incompatible ordering expression).
        """
        if isinstance(key, str):
            key = (key,)
        properties = self._manager_class.Interface.get_graph_ql_properties()
        annotations: dict[str, QueryAnnotation] = {}
        python_keys: list[str] = []
        qs = self._data
        for k in key:
            if k in properties:
                prop = properties[k]
                if not prop.sortable:
                    raise NonSortablePropertyError(k, self._manager_class.__name__)
                if prop.query_annotation is not None:
                    if callable(prop.query_annotation):
                        query_annotation = cast(
                            QueryAnnotationCallable,
                            prop.query_annotation,
                        )
                        qs = query_annotation(qs)
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
                inst = self._build_manager_from_instance(obj)
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

        bucket = self.__class__(
            qs,
            self._manager_class,
            self.filters,
            self.excludes,
            search_date=self._search_date,
            sort_keys=key,
            sort_reverse=reverse,
            run_scoped_cacheable=self._run_scoped_cacheable,
        )
        if not annotations and not python_keys:
            bucket._set_trusted_query_signature(self._trusted_query_signature)
        return bucket

    def none(self) -> DatabaseBucket[GeneralManagerType]:
        """
        Return an empty bucket sharing the same manager class.

        Preserves copied filter/exclude state, search date, sort metadata, and
        run-scoped cacheability from `all()` before replacing the queryset with
        `QuerySet.none()`.

        Returns:
            DatabaseBucket[GeneralManagerType]: Empty bucket retaining manager and query context.
        """
        own = self.all()
        own._data = own._data.none()
        own._query_signature_cache = _QUERY_SIGNATURE_NOT_COMPUTED
        own._trusted_query_signature = None
        return own
