"""Bucket implementation for Excel-backed interfaces."""

from __future__ import annotations

from collections.abc import Generator, Iterable, Mapping
from typing import Any, Protocol, cast

from general_manager.bucket.base_bucket import Bucket, GeneralManagerType
from general_manager.bucket._ordering import normalize_ordering, sort_items
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.cache.dependency_matching import lookup_spec_from_key
from general_manager.interface.excel import ExcelMeta
from general_manager.interface.excel_store import DEFAULT_EXCEL_STORE
from general_manager.utils.filter_parser import create_filter_function

SUPPORTED_EXCEL_LOOKUPS = frozenset(
    {
        "exact",
        "lt",
        "lte",
        "gt",
        "gte",
        "contains",
        "startswith",
        "endswith",
        "in",
    }
)
type ExcelConstraints = tuple[tuple[str, Any], ...]
type ExcelConstraintGroups = tuple[ExcelConstraints, ...]


class ExcelSingleItemRequiredError(ValueError):
    def __init__(self) -> None:
        super().__init__("get() requires exactly one Excel row.")


class ExcelBucketLookupError(ValueError):
    def __init__(self, manager_class: type, lookup: str) -> None:
        super().__init__(
            f"Unknown Excel field lookup {lookup!r} for {manager_class.__name__}."
        )


class ExcelBucketUnionError(TypeError):
    def __init__(
        self,
        manager_class: type,
        other: object,
        *,
        other_manager_class: type | None = None,
    ) -> None:
        if other_manager_class is not None:
            message = (
                f"Cannot union ExcelBucket for {manager_class.__name__} with "
                f"ExcelBucket for {other_manager_class.__name__}."
            )
        else:
            message = (
                f"Cannot union ExcelBucket for {manager_class.__name__} with "
                f"{type(other).__name__}; expected ExcelBucket for "
                f"{manager_class.__name__} or {manager_class.__name__} instance."
            )
        super().__init__(message)


class _ExcelRowView:
    """Attribute view over a parsed Excel row for filter evaluation."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = values

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError as error:
            raise AttributeError(name) from error


class _ExcelInterfaceType(Protocol):
    excel_meta: ExcelMeta
    excel_fields: dict[str, Any]

    @classmethod
    def sync_from_excel(cls, *, force: bool = False) -> object: ...


def _restore_excel_bucket(
    manager_class: type[GeneralManagerType],
    interface_cls: _ExcelInterfaceType,
    filters: ExcelConstraints,
    excludes: ExcelConstraints,
    keys: tuple[Any, ...] | None,
    filter_groups: ExcelConstraintGroups,
    exclude_groups: ExcelConstraintGroups,
) -> ExcelBucket[GeneralManagerType]:
    return ExcelBucket(
        manager_class,
        interface_cls,
        filters=filters,
        excludes=excludes,
        keys=keys,
        filter_groups=filter_groups,
        exclude_groups=exclude_groups,
    )


class ExcelBucket(Bucket[GeneralManagerType]):
    def __init__(
        self,
        manager_class: type[GeneralManagerType],
        interface_cls: _ExcelInterfaceType,
        *,
        filters: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
        excludes: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
        keys: tuple[Any, ...] | None = None,
        filter_groups: ExcelConstraintGroups | None = None,
        exclude_groups: ExcelConstraintGroups | None = None,
    ) -> None:
        super().__init__(manager_class)
        self._interface_cls = interface_cls
        self._filters = self._normalize_reserved_constraints(filters)
        self._excludes = self._normalize_reserved_constraints(excludes)
        self._filter_groups = (
            filter_groups
            if filter_groups is not None
            else ((self._filters,) if self._filters else ())
        )
        self._exclude_groups = (
            exclude_groups
            if exclude_groups is not None
            else ((self._excludes,) if self._excludes else ())
        )
        self.filters = self._dependency_payload(self._filters)
        self.excludes = self._dependency_payload(self._excludes)
        self._keys = keys

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        return (
            _restore_excel_bucket,
            (
                self._manager_class,
                self._interface_cls,
                self._filters,
                self._excludes,
                self._keys,
                self._filter_groups,
                self._exclude_groups,
            ),
        )

    @staticmethod
    def _normalize_constraints(
        constraints: Mapping[str, Any] | Iterable[tuple[str, Any]] | None,
    ) -> tuple[tuple[str, Any], ...]:
        if constraints is None:
            return ()
        if isinstance(constraints, Mapping):
            return tuple(constraints.items())
        return tuple(constraints)

    @staticmethod
    def _dependency_payload(
        constraints: tuple[tuple[str, Any], ...],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for lookup, expected in constraints:
            dependency_lookup = (
                lookup.removesuffix("__exact") if lookup.endswith("__exact") else lookup
            )
            spec = lookup_spec_from_key(dependency_lookup)
            normalized_lookup = "__".join(spec.attr_path)
            if spec.operator != "eq":
                normalized_lookup = f"{normalized_lookup}__{spec.operator}"
            payload[normalized_lookup] = expected
        return payload

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        for key in self._matching_keys():
            yield self._manager_class(**{self._interface_cls.excel_meta.key: key})

    def filter(self, **kwargs: Any) -> ExcelBucket[GeneralManagerType]:
        """Return a bucket narrowed to rows matching every lookup.

        An empty call returns an independent equivalent bucket.
        """
        if not kwargs:
            return self.all()
        constraints = self._normalize_reserved_constraints(kwargs)
        self._validate_lookups(lookup for lookup, _expected in constraints)
        return ExcelBucket(
            self._manager_class,
            self._interface_cls,
            filters=(*self._filters, *constraints),
            excludes=self._excludes,
            keys=self._keys,
            filter_groups=(*self._filter_groups, constraints),
            exclude_groups=self._exclude_groups,
        )

    def exclude(self, **kwargs: Any) -> ExcelBucket[GeneralManagerType]:
        """Return a bucket without rows matching all lookups from one call.

        An empty call returns an independent equivalent bucket.
        """
        if not kwargs:
            return self.all()
        constraints = self._normalize_reserved_constraints(kwargs)
        self._validate_lookups(lookup for lookup, _expected in constraints)
        return ExcelBucket(
            self._manager_class,
            self._interface_cls,
            filters=self._filters,
            excludes=(*self._excludes, *constraints),
            keys=self._keys,
            filter_groups=self._filter_groups,
            exclude_groups=(*self._exclude_groups, constraints),
        )

    def all(self) -> ExcelBucket[GeneralManagerType]:
        """Return an independent bucket preserving the current query."""
        return ExcelBucket(
            self._manager_class,
            self._interface_cls,
            filters=self._filters,
            excludes=self._excludes,
            keys=self._keys,
            filter_groups=self._filter_groups,
            exclude_groups=self._exclude_groups,
        )

    def count(self) -> int:
        """Return the number of rows in the current query."""
        return len(self._matching_keys())

    def first(self) -> GeneralManagerType | None:
        """Return the first matching manager, or ``None`` when empty."""
        items = tuple(self)
        return items[0] if items else None

    def last(self) -> GeneralManagerType | None:
        """Return the last matching manager, or ``None`` when empty."""
        items = tuple(self)
        return items[-1] if items else None

    def get(self, **kwargs: Any) -> GeneralManagerType:
        """Return the single matching manager or raise when the count differs."""
        bucket = self.filter(**kwargs) if kwargs else self
        items = tuple(bucket)
        if len(items) != 1:
            raise ExcelSingleItemRequiredError()
        return items[0]

    def __getitem__(
        self,
        item: int | slice,
    ) -> GeneralManagerType | Bucket[GeneralManagerType]:
        keys = self._matching_keys()
        if isinstance(item, slice):
            from general_manager.bucket._materialized_bucket import MaterializedBucket

            return MaterializedBucket(
                self._manager_class,
                tuple(
                    self._manager_class(**{self._interface_cls.excel_meta.key: key})
                    for key in keys[item]
                ),
            )
        return self._manager_class(**{self._interface_cls.excel_meta.key: keys[item]})

    def __len__(self) -> int:
        return self.count()

    def __contains__(self, item: GeneralManagerType) -> bool:
        key = item.identification[self._interface_cls.excel_meta.key]
        return key in set(self._matching_keys())

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> Bucket[GeneralManagerType]:
        from general_manager.bucket._materialized_bucket import MaterializedBucket

        items = tuple(self)
        if isinstance(other, MaterializedBucket):
            if other._manager_class is not self._manager_class:
                raise ExcelBucketUnionError(
                    self._manager_class,
                    other,
                    other_manager_class=other._manager_class,
                )
            return MaterializedBucket(self._manager_class, items) | other
        if isinstance(other, ExcelBucket):
            if (
                other._manager_class is not self._manager_class
                or other._interface_cls is not self._interface_cls
            ):
                raise ExcelBucketUnionError(
                    self._manager_class,
                    other,
                    other_manager_class=other._manager_class,
                )
            return MaterializedBucket(self._manager_class, items) | MaterializedBucket(
                self._manager_class, tuple(other)
            )
        elif isinstance(other, self._manager_class):
            return MaterializedBucket(self._manager_class, items) | other
        else:
            raise ExcelBucketUnionError(self._manager_class, other)

    def sort(
        self,
        *fields: str,
    ) -> ExcelBucket[GeneralManagerType]:
        """Return a bucket ordered by one or more Excel field names."""
        terms = normalize_ordering(fields)
        if not terms:
            return self
        self._validate_field_names(tuple(term.field for term in terms))
        rows = self._matching_rows()
        rows = sort_items(
            rows,
            terms,
            value_for=lambda row, field: row.values[field],
            identity_for=lambda row: row.key,
        )
        return ExcelBucket(
            self._manager_class,
            self._interface_cls,
            keys=tuple(row.key for row in rows),
        )

    def none(self) -> ExcelBucket[GeneralManagerType]:
        """Return an empty bucket for this manager and interface."""
        return ExcelBucket(self._manager_class, self._interface_cls, keys=tuple())

    def _matching_rows(self) -> list[Any]:
        self._validate_constraints()
        self._track_dependencies()
        self._interface_cls.sync_from_excel()
        mirror = DEFAULT_EXCEL_STORE.mirror_for(cast(type, self._interface_cls))
        rows = list(mirror.rows.values())
        if self._keys is not None:
            rows = [mirror.rows[key] for key in self._keys if key in mirror.rows]
        for group in self._filter_groups:
            matchers = tuple(
                create_filter_function(lookup, expected) for lookup, expected in group
            )
            rows = [
                row
                for row in rows
                if all(matcher(_ExcelRowView(row.values)) for matcher in matchers)
            ]
        for group in self._exclude_groups:
            matchers = tuple(
                create_filter_function(lookup, expected) for lookup, expected in group
            )
            rows = [
                row
                for row in rows
                if not all(matcher(_ExcelRowView(row.values)) for matcher in matchers)
            ]
        return rows

    def _matching_keys(self) -> tuple[Any, ...]:
        return tuple(row.key for row in self._matching_rows())

    def _validate_constraints(self) -> None:
        self._validate_lookups(
            lookup for lookup, _expected in (*self._filters, *self._excludes)
        )

    def _normalize_reserved_constraints(
        self,
        constraints: Mapping[str, Any] | Iterable[tuple[str, Any]] | None,
    ) -> tuple[tuple[str, Any], ...]:
        key = self._interface_cls.excel_meta.key
        normalized: list[tuple[str, Any]] = []
        for lookup, expected in self._normalize_constraints(constraints):
            if lookup == "id__in":
                lookup = f"{key}__in"
                expected = [
                    item[key] if isinstance(item, Mapping) else item
                    for item in expected
                ]
            normalized.append((lookup, expected))
        return tuple(normalized)

    def _validate_lookups(self, lookups: Iterable[str]) -> None:
        for lookup in lookups:
            field_name = self._field_name_for_lookup(lookup)
            if field_name not in self._interface_cls.excel_fields:
                raise ExcelBucketLookupError(self._manager_class, lookup)

    def _validate_field_names(self, field_names: Iterable[str]) -> None:
        for field_name in field_names:
            if field_name not in self._interface_cls.excel_fields:
                raise ExcelBucketLookupError(self._manager_class, field_name)

    def _field_name_for_lookup(self, lookup: str) -> str:
        parts = lookup.split("__")
        if len(parts) == 1:
            return lookup
        if len(parts) == 2 and parts[-1] in SUPPORTED_EXCEL_LOOKUPS:
            return parts[0]
        raise ExcelBucketLookupError(self._manager_class, lookup)

    def _track_dependencies(self) -> None:
        manager_name = self._manager_class.__name__
        if self._filters:
            for constraint in self._filters:
                DependencyTracker.track(
                    manager_name,
                    "filter",
                    serialize_dependency_identifier(
                        self._dependency_payload((constraint,))
                    ),
                )
        else:
            DependencyTracker.track(manager_name, "all", "")
        if self._excludes:
            for constraint in self._excludes:
                DependencyTracker.track(
                    manager_name,
                    "exclude",
                    serialize_dependency_identifier(
                        self._dependency_payload((constraint,))
                    ),
                )
