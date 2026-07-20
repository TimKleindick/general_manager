"""Bucket implementation for request-backed interfaces."""

from __future__ import annotations

from collections.abc import Generator, Hashable, Mapping
from typing import TYPE_CHECKING, Protocol, cast

from general_manager.bucket.base_bucket import Bucket, GeneralManagerType
from general_manager.as_of import ensure_as_of_read_supported
from general_manager.bucket.indexing import freeze_bucket_index_value
from general_manager.interface.requests import (
    RequestLocalPredicate,
    RequestLocalPaginationUnsupportedError,
    RequestPayload,
    RequestPlan,
    RequestQueryResult,
    RequestSingleItemRequiredError,
    apply_request_lookup,
    lookup_name_from_filter,
    resolve_request_value,
)

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.request import RequestInterface

RequestLookupValues = tuple[object, ...]
RequestLookupMap = Mapping[str, RequestLookupValues]
RequestLookupDict = dict[str, RequestLookupValues]
RequestBucketState = dict[str, object]


class RequestQueryBucketCapability(Protocol[GeneralManagerType]):
    """Protocol for the request query capability methods used by buckets."""

    def build_bucket(
        self,
        interface_cls: type["RequestInterface"],
        *,
        operation_name: str | None = None,
        filters: RequestLookupMap | None = None,
        excludes: RequestLookupMap | None = None,
    ) -> "RequestBucket[GeneralManagerType]":
        """Build a typed request bucket for the compiled lookup maps."""
        ...

    def execute_plan(
        self,
        interface_cls: type["RequestInterface"],
        request_plan: RequestPlan,
    ) -> RequestQueryResult:
        """Execute a compiled request plan and return normalized items."""
        ...

    def validate_lookups(
        self,
        interface_cls: type["RequestInterface"],
        *,
        operation_name: str | None = None,
        filters: RequestLookupMap | None = None,
        excludes: RequestLookupMap | None = None,
    ) -> None:
        """Validate lookup maps without executing the request."""
        ...


class RequestPayloadCacheInterface(Protocol):
    """Interface instance hook used to attach the source request payload."""

    def set_request_payload_cache(self, payload: RequestPayload) -> None:
        """Store the raw request payload on a hydrated manager interface."""
        ...


def _set_request_payload_cache(
    manager: GeneralManagerType,
    payload: RequestPayload,
) -> None:
    cast(RequestPayloadCacheInterface, manager._interface).set_request_payload_cache(
        payload
    )


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
    unpickling does not immediately re-run network requests. Restored buckets
    keep their operation name and request plan metadata for equality and
    follow-up query compilation, but are marked as already materialized for
    iteration. Callers should only pickle buckets with serialized items if they
    expect iteration after unpickling to preserve results.
    """

    def __init__(
        self,
        manager_class: type[GeneralManagerType],
        interface_cls: type["RequestInterface"],
        *,
        operation_name: str = "list",
        request_plan: RequestPlan | None = None,
        filters: RequestLookupMap | None = None,
        excludes: RequestLookupMap | None = None,
        items: tuple[GeneralManagerType, ...] | None = None,
        raw_items: tuple[RequestPayload, ...] | None = None,
        count_override: int | None = None,
    ) -> None:
        """Create a lazy request-plan bucket or a materialized item bucket.

        `request_plan` creates a lazy bucket unless serialized `items` or
        `raw_items` are supplied. `items` are already-built managers. `raw_items`
        are request payloads used to reconstruct managers and reinstall payload
        caches, including during pickle restoration. Filter and exclude lookup
        maps are copied into bucket-owned dictionaries so later caller
        mutations do not affect this bucket. `count_override` is the count
        returned after materialization.
        """
        super().__init__(manager_class)
        self._interface_cls = interface_cls
        self._operation_name = operation_name
        self.request_plan = request_plan
        self.filters: RequestLookupDict = dict(filters or {})
        self.excludes: RequestLookupDict = dict(excludes or {})
        self._raw_items = tuple(raw_items or ())
        if items is not None:
            self._data: tuple[GeneralManagerType, ...] = tuple(items)
        elif self._raw_items:
            self._data = tuple(
                self._manager_class(
                    **self._interface_cls.extract_identification(payload)
                )
                for payload in self._raw_items
            )
            for manager, payload in zip(self._data, self._raw_items, strict=False):
                _set_request_payload_cache(manager, payload)
        else:
            self._data = tuple()
        self._count_override = count_override
        self._materialized = (
            items is not None or bool(self._raw_items) or request_plan is None
        )

    def __reduce__(self) -> str | tuple[object, ...]:
        """Return pickle reconstruction data without executing a request."""
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

    def __setstate__(self, state: RequestBucketState) -> None:
        """Restore pickle state without executing a request.

        Serialized raw payloads rebuild manager instances and reinstall their
        request payload caches. If no raw payloads were serialized, the restored
        bucket uses the serialized manager items.
        """
        self._operation_name = cast(str, state["operation_name"])
        self.request_plan = cast(RequestPlan | None, state["request_plan"])
        self.filters = dict(cast(RequestLookupMap, state["filters"]))
        self.excludes = dict(cast(RequestLookupMap, state["excludes"]))
        self._raw_items = tuple(cast(tuple[RequestPayload, ...], state["raw_items"]))
        if self._raw_items:
            self._data = tuple(
                self._manager_class(
                    **self._interface_cls.extract_identification(payload)
                )
                for payload in self._raw_items
            )
        else:
            self._data = tuple(cast(tuple[GeneralManagerType, ...], state["items"]))
        for manager, payload in zip(self._data, self._raw_items, strict=False):
            _set_request_payload_cache(manager, payload)
        self._count_override = cast(int | None, state["count_override"])
        self._materialized = True

    @staticmethod
    def _normalize_lookup_kwargs(
        kwargs: Mapping[str, object],
    ) -> RequestLookupDict:
        return {
            key: value if isinstance(value, tuple) else (value,)
            for key, value in kwargs.items()
        }

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> "RequestBucket[GeneralManagerType]":
        """Return a concrete item bucket containing both operands' items.

        Raises:
            RequestBucketManagerMismatchError: If another request bucket is
                backed by a different manager class.
            RequestBucketTypeMismatchError: If ``other`` is neither a
                compatible request bucket nor one manager instance.
        """
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
        """Compare request buckets by manager, operation, plan, or item identities."""
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

    def _bucket_index_source_signature(self) -> Hashable:
        """Return a stable request signature, or object identity for materialized data."""
        if self.request_plan is not None:
            restore_func, restore_args = self.request_plan.__reduce__()
            return (
                "request",
                self._manager_class,
                self._interface_cls,
                self._operation_name,
                restore_func,
                freeze_bucket_index_value(restore_args),
                freeze_bucket_index_value(self.filters),
                freeze_bucket_index_value(self.excludes),
            )
        return super()._bucket_index_source_signature()

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        """Yield materialized items, executing the request plan at most once."""
        yield from self._ensure_items()

    def filter(self, **kwargs: object) -> "RequestBucket[GeneralManagerType]":
        """Return a bucket restricted by the supplied request or local lookups.

        Lazy request-plan buckets merge the lookups into the compiled request
        plan and validate them through the query capability, even after
        iteration caches fetched items. Concrete item buckets created by
        slicing, unioning, or ``none()`` have no request plan; they validate the
        same lookup vocabulary and then filter contained manager instances in
        memory. Materialized lookups are ANDed across keys. Missing attributes
        do not match.

        Raises:
            Request-interface lookup validation errors: Propagated from query
                capability when a lookup is unknown, unsupported, requires an
                unavailable local fallback, conflicts in the request plan, or
                targets an unsupported request location.
        """
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
        handler = self._query_handler()
        return handler.build_bucket(
            self._interface_cls,
            operation_name=self._operation_name,
            filters={**self.filters, **self._normalize_lookup_kwargs(kwargs)},
            excludes=self.excludes,
        )

    def exclude(self, **kwargs: object) -> "RequestBucket[GeneralManagerType]":
        """Return a bucket excluding items that match the supplied lookups.

        Lazy request-plan buckets compile exclude lookups into the request plan.
        Concrete item buckets created by slicing, unioning, or ``none()`` first
        validate exclude support and then remove matching manager instances in
        memory. Missing attributes do not match and therefore are not excluded.
        Unsupported exclude lookups raise the request-interface
        validation errors produced by the query capability.

        Raises:
            RequestExcludeNotSupportedError: If exclude is requested for a
                lookup without remote exclude support or local fallback.
            Request-interface lookup validation errors: Propagated from query
                capability when a lookup is unknown, unsupported, conflicts in
                the request plan, or targets an unsupported request location.
        """
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
        handler = self._query_handler()
        return handler.build_bucket(
            self._interface_cls,
            operation_name=self._operation_name,
            filters=self.filters,
            excludes={**self.excludes, **self._normalize_lookup_kwargs(kwargs)},
        )

    def first(self) -> GeneralManagerType | None:
        """Return the first materialized item, or ``None`` when the bucket is empty."""
        items = self._ensure_items()
        return items[0] if items else None

    def last(self) -> GeneralManagerType | None:
        """Return the last materialized item, or ``None`` when the bucket is empty."""
        items = self._ensure_items()
        return items[-1] if items else None

    def count(self) -> int:
        """Materialize, then return the current count override or item count.

        Lazy materialization stores the upstream ``total_count`` as the count
        override when available, stores the local fallback result count when
        local predicates are applied, and otherwise falls back to the
        materialized item count. Concrete buckets keep the override installed by
        slicing, unioning, or ``none()``.
        """
        self._ensure_items()
        if self._count_override is not None:
            return self._count_override
        return len(self._data)

    def all(self) -> "RequestBucket[GeneralManagerType]":
        """Return a new request bucket for the same query plan or concrete items."""
        if self.request_plan is None:
            return self._from_items(self._ensure_items())
        handler = self._query_handler()
        return handler.build_bucket(
            self._interface_cls,
            operation_name=self._operation_name,
            filters=self.filters,
            excludes=self.excludes,
        )

    def get(self, **kwargs: object) -> GeneralManagerType:
        """Return exactly one item, optionally after applying additional filters.

        Raises:
            RequestSingleItemRequiredError: If the resulting bucket does not
                contain exactly one item.
        """
        bucket = self.filter(**kwargs) if kwargs else self
        items = tuple(bucket)
        if len(items) != 1:
            raise RequestSingleItemRequiredError()
        return items[0]

    def __getitem__(
        self,
        item: int | slice,
    ) -> GeneralManagerType | "RequestBucket[GeneralManagerType]":
        """Return one materialized item or a materialized bucket slice."""
        items = self._ensure_items()
        if isinstance(item, slice):
            return self._from_items(items[item])
        return items[item]

    def __len__(self) -> int:
        """Return the number of materialized items."""
        return len(self._ensure_items())

    def __contains__(self, item: GeneralManagerType) -> bool:
        """Return whether an equal manager instance is present."""
        return item in self._ensure_items()

    def sort(
        self,
        key: tuple[str, ...] | str,
        reverse: bool = False,
    ) -> "RequestBucket[GeneralManagerType]":
        """Return a materialized bucket sorted by one or more manager attributes.

        Raises:
            RequestBucketSortAttributeError: If any item lacks a requested sort
                attribute.
            TypeError: Propagated when Python cannot compare the resolved sort
                values, such as mixed unrelated value types.
        """
        items = list(self._ensure_items())
        key_names = (key,) if isinstance(key, str) else key

        def _sort_key(instance: GeneralManagerType) -> tuple[object, ...]:
            values: list[object] = []
            for part in key_names:
                try:
                    values.append(getattr(instance, part))
                except AttributeError as error:
                    raise RequestBucketSortAttributeError(instance, part) from error
            return tuple(values)

        items.sort(key=_sort_key, reverse=reverse)
        return self._from_items(tuple(items))

    def none(self) -> "RequestBucket[GeneralManagerType]":
        """Return an empty materialized bucket preserving the operation name."""
        return self._from_items(tuple())

    @property
    def operation_name(self) -> str:
        """Return the query operation name preserved on this bucket.

        Concrete item buckets created from slices, unions, or ``none()`` keep
        the source operation name for observability and equality context even
        though they no longer have a request plan.
        """
        return self._operation_name

    def _ensure_items(self) -> tuple[GeneralManagerType, ...]:
        ensure_as_of_read_supported(self._interface_cls)
        if self._data:
            self._materialized = True
            return self._data
        if self._materialized:
            return self._data
        if self.request_plan is None:
            self._materialized = True
            return self._data

        handler = self._query_handler()
        result = handler.execute_plan(self._interface_cls, self.request_plan)
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
            _set_request_payload_cache(manager, payload)
        self._materialized = True
        return self._data

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

    def _validate_materialized_filters(self, kwargs: Mapping[str, object]) -> None:
        handler = self._query_handler()
        handler.validate_lookups(
            self._interface_cls,
            operation_name=self._operation_name,
            filters=self._normalize_lookup_kwargs(kwargs),
        )

    def _validate_materialized_excludes(self, kwargs: Mapping[str, object]) -> None:
        handler = self._query_handler()
        handler.validate_lookups(
            self._interface_cls,
            operation_name=self._operation_name,
            excludes=self._normalize_lookup_kwargs(kwargs),
        )

    def _query_handler(self) -> RequestQueryBucketCapability[GeneralManagerType]:
        return cast(
            RequestQueryBucketCapability[GeneralManagerType],
            self._interface_cls.require_capability("query"),
        )


def _matches_manager_lookup(item: object, lookup_key: str, expected: object) -> bool:
    path, operator = _split_lookup(lookup_key)
    current = item
    for part in path:
        if not hasattr(current, part):
            return False
        current = getattr(current, part)
    return _apply_lookup(current, operator, expected)


def _matches_local_predicates(
    payload: RequestPayload,
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
    payload: RequestPayload,
    lookup_key: str,
    expected: object,
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


def _apply_lookup(value: object, operator: str, expected: object) -> bool:
    return apply_request_lookup(value, operator, expected)
