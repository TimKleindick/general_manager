"""Private exact-subset bucket used after collection membership is fixed."""

from __future__ import annotations

from collections.abc import Generator, Iterable
from typing import TYPE_CHECKING, cast

from general_manager.bucket.base_bucket import Bucket, GeneralManagerType
from general_manager.bucket.indexing import freeze_bucket_index_value
from general_manager.bucket._ordering import (
    normalize_ordering,
    sort_items,
    validate_ordering_fields,
)
from general_manager.utils.filter_parser import create_filter_function

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager


_SNAPSHOT_UNSPECIFIED = object()


class MaterializedBucketTypeMismatchError(TypeError):
    """Raised when an exact subset receives an incompatible bucket or manager."""

    def __init__(self, manager_class: type, other: object) -> None:
        super().__init__(
            f"Cannot create a subset for {manager_class.__name__} from "
            f"{type(other).__name__}."
        )


class MaterializedBucketSnapshotMismatchError(ValueError):
    """Raised when exact subsets are combined from different snapshots."""

    def __init__(self) -> None:
        super().__init__("Cannot combine buckets from different snapshots.")


class MaterializedBucketSingleItemRequiredError(ValueError):
    """Raised when a materialized ``get`` does not match exactly one item."""

    def __init__(self) -> None:
        super().__init__("get() requires exactly one matching item.")


class MaterializedBucket(Bucket[GeneralManagerType]):
    """Persistent ordered manager instances with explicit snapshot provenance."""

    def __init__(
        self,
        manager_class: type[GeneralManagerType],
        items: Iterable[GeneralManagerType],
        snapshot: object = _SNAPSHOT_UNSPECIFIED,
    ) -> None:
        super().__init__(manager_class)
        self._snapshot = snapshot
        self._data = tuple(items)
        for item in self._items:
            if item.__class__ is not manager_class:
                raise MaterializedBucketTypeMismatchError(manager_class, item)
            item_snapshot = getattr(
                item, "_effective_search_date", _SNAPSHOT_UNSPECIFIED
            )
            if (
                snapshot is not _SNAPSHOT_UNSPECIFIED
                and item_snapshot is not _SNAPSHOT_UNSPECIFIED
                and item_snapshot != snapshot
            ):
                raise MaterializedBucketSnapshotMismatchError

    @property
    def _items(self) -> tuple[GeneralManagerType, ...]:
        return cast(tuple[GeneralManagerType, ...], self._data)

    def _new(
        self, items: Iterable[GeneralManagerType]
    ) -> "MaterializedBucket[GeneralManagerType]":
        return MaterializedBucket(self._manager_class, items, snapshot=self._snapshot)

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        if self._snapshot is _SNAPSHOT_UNSPECIFIED:
            return (self.__class__, (self._manager_class, self._items))
        return (self.__class__, (self._manager_class, self._items, self._snapshot))

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        yield from self._items

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> "MaterializedBucket[GeneralManagerType]":
        if isinstance(other, MaterializedBucket):
            if other._manager_class is not self._manager_class:
                raise MaterializedBucketTypeMismatchError(self._manager_class, other)
            if other._snapshot != self._snapshot:
                raise MaterializedBucketSnapshotMismatchError
            candidates = (*self._items, *other._items)
        elif isinstance(other, self._manager_class):
            candidates = (*self._items, other)
        elif isinstance(other, Bucket):
            if other._manager_class is not self._manager_class:
                raise MaterializedBucketTypeMismatchError(self._manager_class, other)
            if _bucket_snapshot(other) != self._snapshot:
                raise MaterializedBucketSnapshotMismatchError
            candidates = (*self._items, *tuple(other))
        else:
            raise MaterializedBucketTypeMismatchError(self._manager_class, other)

        seen: set[object] = set()
        items: list[GeneralManagerType] = []
        for candidate in candidates:
            identity = _manager_identity(candidate)
            if identity not in seen:
                seen.add(identity)
                items.append(candidate)
        return self._new(items)

    def filter(self, **kwargs: object) -> "MaterializedBucket[GeneralManagerType]":
        if not kwargs:
            return self.all()
        matchers = tuple(
            create_filter_function(key, value) for key, value in kwargs.items()
        )
        return self._new(
            item for item in self._items if all(matcher(item) for matcher in matchers)
        )

    def exclude(self, **kwargs: object) -> "MaterializedBucket[GeneralManagerType]":
        if not kwargs:
            return self.all()
        matchers = tuple(
            create_filter_function(key, value) for key, value in kwargs.items()
        )
        return self._new(
            item
            for item in self._items
            if not all(matcher(item) for matcher in matchers)
        )

    def first(self) -> GeneralManagerType | None:
        return self._items[0] if self._items else None

    def last(self) -> GeneralManagerType | None:
        return self._items[-1] if self._items else None

    def count(self) -> int:
        return len(self._items)

    def all(self) -> "MaterializedBucket[GeneralManagerType]":
        return self._new(self._items)

    def get(self, **kwargs: object) -> GeneralManagerType:
        items = tuple(self.filter(**kwargs)) if kwargs else self._items
        if len(items) != 1:
            raise MaterializedBucketSingleItemRequiredError
        return items[0]

    def __getitem__(
        self, item: int | slice
    ) -> GeneralManagerType | "MaterializedBucket[GeneralManagerType]":
        if isinstance(item, slice):
            return self._new(self._items[item])
        return self._items[item]

    def __len__(self) -> int:
        return self.count()

    def __contains__(self, item: GeneralManagerType) -> bool:
        if item.__class__ is not self._manager_class:
            return False
        identity = _manager_identity(item)
        return any(
            _manager_identity(candidate) == identity for candidate in self._items
        )

    def sort(
        self,
        *fields: str,
    ) -> "MaterializedBucket[GeneralManagerType]":
        terms = normalize_ordering(fields)
        if not terms:
            return self._new(self._items)
        validate_ordering_fields(self._manager_class, terms)
        return self._new(sort_items(self._items, terms))

    def none(self) -> "MaterializedBucket[GeneralManagerType]":
        return self._new(())

    def with_instances(
        self, instances: Iterable[GeneralManagerType]
    ) -> "MaterializedBucket[GeneralManagerType]":
        return self._new(tuple(instances))


def _manager_identity(item: "GeneralManager") -> object:
    return (
        item.__class__,
        freeze_bucket_index_value(item.identification),
        getattr(item, "_effective_search_date", None),
    )


def _bucket_snapshot(bucket: Bucket[GeneralManagerType]) -> object:
    """Return explicit snapshot provenance when a native bucket exposes it."""
    if hasattr(bucket, "_search_date"):
        return bucket._search_date
    if hasattr(bucket, "_effective_search_date"):
        return bucket._effective_search_date
    return _SNAPSHOT_UNSPECIFIED
