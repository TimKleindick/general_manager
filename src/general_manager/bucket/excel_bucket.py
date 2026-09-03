"""Bucket implementation for Excel-backed interfaces."""

from __future__ import annotations

from collections.abc import Generator, Iterable, Mapping
from typing import Any, Protocol, cast

from general_manager.bucket.base_bucket import Bucket, GeneralManagerType
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import serialize_dependency_identifier
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


class ExcelBucket(Bucket[GeneralManagerType]):
    def __init__(
        self,
        manager_class: type[GeneralManagerType],
        interface_cls: _ExcelInterfaceType,
        *,
        filters: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
        excludes: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
        keys: tuple[Any, ...] | None = None,
    ) -> None:
        super().__init__(manager_class)
        self._interface_cls = interface_cls
        self._filters = self._normalize_reserved_constraints(filters)
        self._excludes = self._normalize_reserved_constraints(excludes)
        self.filters = self._dependency_payload(self._filters)
        self.excludes = self._dependency_payload(self._excludes)
        self._keys = keys

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
        repeated: dict[str, list[Any]] = {}
        for lookup, expected in constraints:
            if lookup in payload:
                repeated.setdefault(lookup, [payload[lookup]]).append(expected)
            elif lookup in repeated:
                repeated[lookup].append(expected)
            else:
                payload[lookup] = expected
        for lookup, values in repeated.items():
            payload[lookup] = tuple(values)
        return payload

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        for key in self._matching_keys():
            yield self._manager_class(**{self._interface_cls.excel_meta.key: key})

    def filter(self, **kwargs: Any) -> ExcelBucket[GeneralManagerType]:
        constraints = self._normalize_reserved_constraints(kwargs)
        self._validate_lookups(lookup for lookup, _expected in constraints)
        return ExcelBucket(
            self._manager_class,
            self._interface_cls,
            filters=(*self._filters, *constraints),
            excludes=self._excludes,
            keys=self._keys,
        )

    def exclude(self, **kwargs: Any) -> ExcelBucket[GeneralManagerType]:
        constraints = self._normalize_reserved_constraints(kwargs)
        self._validate_lookups(lookup for lookup, _expected in constraints)
        return ExcelBucket(
            self._manager_class,
            self._interface_cls,
            filters=self._filters,
            excludes=(*self._excludes, *constraints),
            keys=self._keys,
        )

    def all(self) -> ExcelBucket[GeneralManagerType]:
        return ExcelBucket(
            self._manager_class,
            self._interface_cls,
            filters=self._filters,
            excludes=self._excludes,
            keys=self._keys,
        )

    def count(self) -> int:
        return len(self._matching_keys())

    def first(self) -> GeneralManagerType | None:
        items = tuple(self)
        return items[0] if items else None

    def last(self) -> GeneralManagerType | None:
        items = tuple(self)
        return items[-1] if items else None

    def get(self, **kwargs: Any) -> GeneralManagerType:
        bucket = self.filter(**kwargs) if kwargs else self
        items = tuple(bucket)
        if len(items) != 1:
            raise ExcelSingleItemRequiredError()
        return items[0]

    def __getitem__(
        self,
        item: int | slice,
    ) -> GeneralManagerType | ExcelBucket[GeneralManagerType]:
        keys = self._matching_keys()
        if isinstance(item, slice):
            return ExcelBucket(
                self._manager_class,
                self._interface_cls,
                keys=keys[item],
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
    ) -> ExcelBucket[GeneralManagerType]:
        keys = list(self._matching_keys())
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
            keys.extend(other._matching_keys())
        elif isinstance(other, self._manager_class):
            keys.append(other.identification[self._interface_cls.excel_meta.key])
        else:
            raise ExcelBucketUnionError(self._manager_class, other)
        return ExcelBucket(
            self._manager_class,
            self._interface_cls,
            keys=tuple(dict.fromkeys(keys)),
        )

    def sort(
        self,
        key: tuple[str, ...] | str,
        reverse: bool = False,
    ) -> ExcelBucket[GeneralManagerType]:
        key_names = (key,) if isinstance(key, str) else key
        self._validate_field_names(key_names)
        rows = self._matching_rows()
        rows.sort(
            key=lambda row: tuple(row.values[name] for name in key_names),
            reverse=reverse,
        )
        return ExcelBucket(
            self._manager_class,
            self._interface_cls,
            keys=tuple(row.key for row in rows),
        )

    def none(self) -> ExcelBucket[GeneralManagerType]:
        return ExcelBucket(self._manager_class, self._interface_cls, keys=tuple())

    def _matching_rows(self) -> list[Any]:
        self._validate_constraints()
        self._track_dependencies()
        self._interface_cls.sync_from_excel()
        mirror = DEFAULT_EXCEL_STORE.mirror_for(cast(type, self._interface_cls))
        rows = list(mirror.rows.values())
        if self._keys is not None:
            rows = [mirror.rows[key] for key in self._keys if key in mirror.rows]
        for lookup, expected in self._filters:
            matcher = create_filter_function(lookup, expected)
            rows = [row for row in rows if matcher(_ExcelRowView(row.values))]
        for lookup, expected in self._excludes:
            matcher = create_filter_function(lookup, expected)
            rows = [row for row in rows if not matcher(_ExcelRowView(row.values))]
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
            DependencyTracker.track(
                manager_name,
                "filter",
                serialize_dependency_identifier(self.filters),
            )
        else:
            DependencyTracker.track(manager_name, "all", "")
        if self._excludes:
            DependencyTracker.track(
                manager_name,
                "exclude",
                serialize_dependency_identifier(self.excludes),
            )
