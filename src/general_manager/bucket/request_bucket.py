"""Bucket implementation for request-backed interfaces."""

from __future__ import annotations

from collections.abc import Generator, Mapping
from typing import TYPE_CHECKING, Any, cast

from general_manager.bucket.base_bucket import Bucket, GeneralManagerType
from general_manager.interface.requests import (
    RequestLocalPredicate,
    RequestLocalPaginationUnsupportedError,
    RequestPlan,
    RequestQueryResult,
    RequestSingleItemRequiredError,
    apply_request_lookup,
    lookup_name_from_filter,
    resolve_request_value,
)

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.request import RequestInterface


class RequestBucketTypeMismatchError(TypeError):
    """Raised when attempting to combine request buckets with incompatible types."""

    def __init__(self, bucket_type: type, other_type: type) -> None:
        super().__init__(
            f"Cannot combine {bucket_type.__name__} with {other_type.__name__}."
        )


class RequestBucketManagerMismatchError(TypeError):
    """Raised when combining request buckets backed by different managers."""

    def __init__(self, first_manager: type, second_manager: type) -> None:
        super().__init__(
            f"Cannot combine buckets for {first_manager.__name__} and {second_manager.__name__}."
        )


class RequestBucketSortAttributeError(AttributeError):
    """Raised when sorting a request bucket by an attribute that is missing."""

    def __init__(self, instance: object, attribute: str) -> None:
        super().__init__(f"{instance!r} is missing sort attribute {attribute!r}.")


class RequestBucket(Bucket[GeneralManagerType]):
    """Lazy bucket backed by a compiled request query plan.

    Pickling preserves the compiled request plan and any serialized items, but
    unpickling does not re-run network requests. Buckets restored from pickle
    are marked materialized with whatever items were serialized, so callers
    should only pickle materialized buckets if they expect to preserve results.
    """

    def __init__(
        self,
        manager_class: type[GeneralManagerType],
        interface_cls: type["RequestInterface"],
        *,
        operation_name: str = "list",
        request_plan: RequestPlan | None = None,
        filters: Mapping[str, Any] | None = None,
        excludes: Mapping[str, Any] | None = None,
        items: tuple[GeneralManagerType, ...] | None = None,
        raw_items: tuple[Mapping[str, Any], ...] | None = None,
        count_override: int | None = None,
    ) -> None:
        super().__init__(manager_class)
        self._interface_cls = interface_cls
        self._operation_name = operation_name
        self.request_plan = request_plan
        self.filters = dict(filters or {})
        self.excludes = dict(excludes or {})
        self._data = tuple(items or ())
        self._raw_items = tuple(raw_items or ())
        self._count_override = count_override
        self._materialized = items is not None or request_plan is None

    def __reduce__(self) -> str | tuple[Any, ...]:
        return (
            self.__class__,
            (
                self._manager_class,
                self._interface_cls,
            ),
            {
                "operation_name": self._operation_name,
                "request_plan": self.request_plan,
                "filters": self.filters,
                "excludes": self.excludes,
                "items": self._data,
                "raw_items": self._raw_items,
                "count_override": self._count_override,
            },
        )

    def __setstate__(self, state: dict[str, Any]) -> None:
        self._operation_name = state["operation_name"]
        self.request_plan = state["request_plan"]
        self.filters = dict(state["filters"])
        self.excludes = dict(state["excludes"])
        self._raw_items = tuple(state["raw_items"])
        if self._raw_items:
            self._data = tuple(
                self._manager_class(
                    **self._interface_cls.extract_identification(payload)
                )
                for payload in self._raw_items
            )
            for manager, payload in zip(self._data, self._raw_items, strict=False):
                manager._interface.set_request_payload_cache(payload)
        else:
            self._data = tuple(state["items"])
        self._count_override = state["count_override"]
        self._materialized = True

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> Bucket[GeneralManagerType]:
        if isinstance(other, RequestBucket):
            if self._manager_class != other._manager_class:
                raise RequestBucketManagerMismatchError(
                    self._manager_class,
                    other._manager_class,
                )
            return self._from_items((*self._ensure_items(), *other._ensure_items()))
        if isinstance(other, self._manager_class):
            return self._from_items((*self._ensure_items(), other))
        raise RequestBucketTypeMismatchError(self.__class__, type(other))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RequestBucket):
            return False
        if self._manager_class != other._manager_class:
            return False
        if self._operation_name != other._operation_name:
            return False
        if self.request_plan is not None and other.request_plan is not None:
            return (
                self.request_plan == other.request_plan
                and self.filters == other.filters
                and self.excludes == other.excludes
            )
        return tuple(item.identification for item in self._ensure_items()) == tuple(
            item.identification for item in other._ensure_items()
        )

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        yield from self._ensure_items()

    def filter(self, **kwargs: Any) -> Bucket[GeneralManagerType]:
        if self.request_plan is None:
            self._validate_materialized_filters(kwargs)
            return self._from_items(
                tuple(
                    item
                    for item in self._ensure_items()
                    if all(
                        _matches_manager_lookup(item, key, value)
                        for key, value in kwargs.items()
                    )
                )
            )
        handler = cast(Any, self._interface_cls.require_capability("query"))
        return handler.build_bucket(  # type: ignore[return-value]
            self._interface_cls,
            operation_name=self._operation_name,
            filters={**self.filters, **kwargs},
            excludes=self.excludes,
        )

    def exclude(self, **kwargs: Any) -> Bucket[GeneralManagerType]:
        if self.request_plan is None:
            self._validate_materialized_excludes(kwargs)
            return self._from_items(
                tuple(
                    item
                    for item in self._ensure_items()
                    if not any(
                        _matches_manager_lookup(item, key, value)
                        for key, value in kwargs.items()
                    )
                )
            )
        handler = cast(Any, self._interface_cls.require_capability("query"))
        return handler.build_bucket(  # type: ignore[return-value]
            self._interface_cls,
            operation_name=self._operation_name,
            filters=self.filters,
            excludes={**self.excludes, **kwargs},
        )

    def first(self) -> GeneralManagerType | None:
        items = self._ensure_items()
        return items[0] if items else None

    def last(self) -> GeneralManagerType | None:
        items = self._ensure_items()
        return items[-1] if items else None

    def count(self) -> int:
        self._ensure_items()
        if self._count_override is not None:
            return self._count_override
        return len(self._data)

    def all(self) -> Bucket[GeneralManagerType]:
        if self.request_plan is None:
            return self._from_items(self._ensure_items())
        handler = cast(Any, self._interface_cls.require_capability("query"))
        return handler.build_bucket(  # type: ignore[return-value]
            self._interface_cls,
            operation_name=self._operation_name,
        )

    def get(self, **kwargs: Any) -> GeneralManagerType:
        bucket = self.filter(**kwargs) if kwargs else self
        items = tuple(bucket)
        if len(items) != 1:
            raise RequestSingleItemRequiredError()
        return items[0]

    def __getitem__(
        self,
        item: int | slice,
    ) -> GeneralManagerType | Bucket[GeneralManagerType]:
        items = self._ensure_items()
        if isinstance(item, slice):
            return self._from_items(items[item])
        return items[item]

    def __len__(self) -> int:
        return len(self._ensure_items())

    def __contains__(self, item: GeneralManagerType) -> bool:
        return item in self._ensure_items()

    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> Bucket[GeneralManagerType]:
        items = list(self._ensure_items())
        key_names = (key,) if isinstance(key, str) else key

        def _sort_key(instance: GeneralManagerType) -> tuple[Any, ...]:
            values: list[Any] = []
            for part in key_names:
                try:
                    values.append(getattr(instance, part))
                except AttributeError as error:
                    raise RequestBucketSortAttributeError(instance, part) from error
            return tuple(values)

        items.sort(key=_sort_key, reverse=reverse)
        return self._from_items(tuple(items))

    def none(self) -> Bucket[GeneralManagerType]:
        return self._from_items(tuple())

    @property
    def operation_name(self) -> str:
        return self._operation_name

    def _ensure_items(self) -> tuple[GeneralManagerType, ...]:
        if self._data:
            return cast(tuple[GeneralManagerType, ...], self._data)
        if self._materialized:
            return cast(tuple[GeneralManagerType, ...], self._data)
        if self.request_plan is None:
            self._materialized = True
            return cast(tuple[GeneralManagerType, ...], self._data)

        handler = cast(Any, self._interface_cls.require_capability("query"))
        result = cast(
            RequestQueryResult,
            handler.execute_plan(self._interface_cls, self.request_plan),
        )
        raw_items = tuple(
            payload
            for payload in result.items
            if _matches_local_predicates(payload, self.request_plan.local_predicates)
        )
        if (
            self.request_plan.local_predicates
            and result.total_count is not None
            and result.total_count != len(result.items)
        ):
            raise RequestLocalPaginationUnsupportedError(
                self._operation_name,
                result.total_count,
                len(result.items),
            )
        if self.request_plan.local_predicates:
            self._count_override = len(raw_items)
        else:
            self._count_override = result.total_count
        self._raw_items = raw_items
        self._data = tuple(
            self._manager_class(**self._interface_cls.extract_identification(payload))
            for payload in raw_items
        )
        for manager, payload in zip(self._data, raw_items, strict=False):
            manager._interface.set_request_payload_cache(payload)
        self._materialized = True
        return cast(tuple[GeneralManagerType, ...], self._data)

    def _from_items(
        self,
        items: tuple[GeneralManagerType, ...],
    ) -> "RequestBucket[GeneralManagerType]":
        return RequestBucket(
            self._manager_class,
            self._interface_cls,
            operation_name=self._operation_name,
            items=items,
            count_override=len(items),
        )

    def _validate_materialized_filters(self, kwargs: Mapping[str, Any]) -> None:
        handler = cast(Any, self._interface_cls.require_capability("query"))
        handler.validate_lookups(
            self._interface_cls,
            operation_name=self._operation_name,
            filters={key: (value,) for key, value in kwargs.items()},
        )

    def _validate_materialized_excludes(self, kwargs: Mapping[str, Any]) -> None:
        handler = cast(Any, self._interface_cls.require_capability("query"))
        handler.validate_lookups(
            self._interface_cls,
            operation_name=self._operation_name,
            excludes={key: (value,) for key, value in kwargs.items()},
        )


def _matches_manager_lookup(item: object, lookup_key: str, expected: Any) -> bool:
    path, operator = _split_lookup(lookup_key)
    current = item
    for part in path:
        if not hasattr(current, part):
            return False
        current = getattr(current, part)
    return _apply_lookup(current, operator, expected)


def _matches_local_predicates(
    payload: Mapping[str, Any],
    predicates: tuple[RequestLocalPredicate, ...],
) -> bool:
    for predicate in predicates:
        matched = _matches_payload_lookup(
            payload, predicate.lookup_key, predicate.value
        )
        if predicate.action == "filter" and not matched:
            return False
        if predicate.action == "exclude" and matched:
            return False
    return True


def _matches_payload_lookup(
    payload: Mapping[str, Any],
    lookup_key: str,
    expected: Any,
) -> bool:
    path, operator = _split_lookup(lookup_key)
    try:
        current = resolve_request_value(payload, path)
    except KeyError:
        return False
    return _apply_lookup(current, operator, expected)


def _split_lookup(lookup_key: str) -> tuple[tuple[str, ...], str]:
    parts = lookup_key.split("__")
    lookup = lookup_name_from_filter(lookup_key)
    if parts and parts[-1] == lookup and lookup != "exact":
        return tuple(parts[:-1]), parts[-1]
    return tuple(parts), "exact"


def _apply_lookup(value: Any, operator: str, expected: Any) -> bool:
    return apply_request_lookup(value, operator, expected)
