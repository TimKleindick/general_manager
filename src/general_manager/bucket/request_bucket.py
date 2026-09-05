"""Bucket implementation for request-backed interfaces."""

from __future__ import annotations

from collections.abc import Generator, Hashable, Iterable, Mapping
from typing import TYPE_CHECKING, Literal, Protocol, cast

from general_manager.bucket._materialized_bucket import _manager_identity
from general_manager.bucket.base_bucket import Bucket, GeneralManagerType
from general_manager.bucket._ordering import (
    normalize_ordering,
    sort_items,
    validate_ordering_fields,
)
from general_manager.bucket.projection import ProjectionRows
from general_manager.as_of import ensure_as_of_read_supported
from general_manager.bucket.indexing import freeze_bucket_index_value
from general_manager.interface.requests import (
    RequestLocalPredicate,
    RequestLocalPaginationUnsupportedError,
    RequestPayload,
    RequestPlan,
    RequestQueryResult,
    RequestIncompleteResultError,
    RequestSingleItemRequiredError,
    MissingRequestPayloadFieldError,
    apply_request_lookup,
    lookup_name_from_filter,
    resolve_request_value,
)

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.request import RequestInterface

RequestLookupValues = tuple[object, ...]
RequestLookupMap = Mapping[str, RequestLookupValues]
RequestLookupDict = dict[str, RequestLookupValues]
RequestLookupGroups = tuple[RequestLookupDict, ...]
RequestBucketState = dict[str, object]
RequestBucketSourceKind = Literal["manager", "plan", "snapshot"]

# Keep source provenance separate from payload truthiness: an executed request
# can legitimately have an empty raw snapshot, while serialized manager items
# can retain the request plan as metadata.
_MANAGER_SOURCE: RequestBucketSourceKind = "manager"
_PLAN_SOURCE: RequestBucketSourceKind = "plan"
_SNAPSHOT_SOURCE: RequestBucketSourceKind = "snapshot"


class RequestQueryBucketCapability(Protocol[GeneralManagerType]):
    """Protocol for the request query capability methods used by buckets."""

    def build_bucket(
        self,
        interface_cls: type["RequestInterface"],
        *,
        operation_name: str | None = None,
        filters: RequestLookupMap | None = None,
        excludes: RequestLookupMap | None = None,
        filter_call_groups: RequestLookupGroups | None = None,
        exclude_call_groups: RequestLookupGroups | None = None,
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
        filter_call_groups: RequestLookupGroups | None = None,
        exclude_call_groups: RequestLookupGroups | None = None,
        items: tuple[GeneralManagerType, ...] | None = None,
        raw_items: tuple[RequestPayload, ...] | None = None,
        count_override: int | None = None,
        total_count: int | None = None,
        response_is_complete: bool | None = None,
        upstream_page: int | None = None,
        upstream_page_size: int | None = None,
    ) -> None:
        """Create a lazy request-plan bucket or a materialized item bucket.

        `request_plan` creates a lazy bucket unless serialized `items` or
        `raw_items` are supplied. `items` are already-built managers. `raw_items`
        are request payloads used to reconstruct managers and reinstall payload
        caches, including during pickle restoration. Filter and exclude lookup
        maps are copied into bucket-owned dictionaries so later caller
        mutations do not affect this bucket. ``total_count`` is upstream response
        metadata and never changes the number of represented bucket rows.
        """
        super().__init__(manager_class)
        self._interface_cls = interface_cls
        self._operation_name = operation_name
        self.request_plan = request_plan
        self.filters: RequestLookupDict = dict(filters or {})
        self.excludes: RequestLookupDict = dict(excludes or {})
        self._filter_call_groups = self._copy_lookup_groups(
            filter_call_groups
            if filter_call_groups is not None
            else request_plan.filter_call_groups
            if request_plan is not None
            else ()
        )
        self._exclude_call_groups = self._copy_lookup_groups(
            exclude_call_groups
            if exclude_call_groups is not None
            else request_plan.exclude_call_groups
            if request_plan is not None
            else ()
        )
        if items is not None:
            self._raw_source_kind = _MANAGER_SOURCE
        elif raw_items is not None:
            self._raw_source_kind = _SNAPSHOT_SOURCE
        elif request_plan is not None:
            self._raw_source_kind = _PLAN_SOURCE
        else:
            self._raw_source_kind = _MANAGER_SOURCE
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
            for manager, payload in zip(self._data, self._raw_items, strict=True):
                _set_request_payload_cache(manager, payload)
        else:
            self._data = tuple()
        self._count_override = count_override
        self._total_count = total_count
        self._upstream_page = upstream_page
        self._upstream_page_size = upstream_page_size
        self._response_is_complete = (
            response_is_complete
            if response_is_complete is not None
            else items is not None or raw_items is not None or request_plan is None
        )
        self._materialized = (
            items is not None or raw_items is not None or request_plan is None
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
                "filter_call_groups": self._filter_call_groups,
                "exclude_call_groups": self._exclude_call_groups,
                "items": self._data,
                "raw_items": self._raw_items,
                "count_override": self._count_override,
                "total_count": self._total_count,
                "upstream_page": self._upstream_page,
                "upstream_page_size": self._upstream_page_size,
                "response_is_complete": self._response_is_complete,
                "raw_source_kind": (
                    _MANAGER_SOURCE
                    if self._raw_source_kind == _PLAN_SOURCE
                    else self._raw_source_kind
                ),
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
        self._filter_call_groups = self._copy_lookup_groups(
            cast(RequestLookupGroups, state.get("filter_call_groups", ()))
        )
        self._exclude_call_groups = self._copy_lookup_groups(
            cast(RequestLookupGroups, state.get("exclude_call_groups", ()))
        )
        self._raw_items = tuple(cast(tuple[RequestPayload, ...], state["raw_items"]))
        raw_source_kind = state.get("raw_source_kind")
        if raw_source_kind not in (
            _MANAGER_SOURCE,
            _PLAN_SOURCE,
            _SNAPSHOT_SOURCE,
        ):
            raw_source_kind = _SNAPSHOT_SOURCE if self._raw_items else _MANAGER_SOURCE
        self._raw_source_kind = raw_source_kind
        if self._raw_items:
            self._data = tuple(
                self._manager_class(
                    **self._interface_cls.extract_identification(payload)
                )
                for payload in self._raw_items
            )
            for manager, payload in zip(self._data, self._raw_items, strict=True):
                _set_request_payload_cache(manager, payload)
        else:
            self._data = tuple(cast(tuple[GeneralManagerType, ...], state["items"]))
        self._count_override = cast(int | None, state["count_override"])
        self._total_count = cast(int | None, state.get("total_count"))
        self._upstream_page = cast(int | None, state.get("upstream_page"))
        self._upstream_page_size = cast(int | None, state.get("upstream_page_size"))
        self._response_is_complete = bool(state.get("response_is_complete", False))
        self._materialized = True

    @staticmethod
    def _normalize_lookup_kwargs(
        kwargs: Mapping[str, object],
    ) -> RequestLookupDict:
        return {key: (value,) for key, value in kwargs.items()}

    @staticmethod
    def _copy_lookup_groups(
        groups: tuple[Mapping[str, RequestLookupValues], ...],
    ) -> RequestLookupGroups:
        return tuple(
            {key: tuple(values) for key, values in group.items()}
            for group in groups
            if group
        )

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> Bucket[GeneralManagerType]:
        """Return a concrete item bucket containing both operands' items.

        Raises:
            RequestBucketManagerMismatchError: If another request bucket is
                backed by a different manager class.
            RequestBucketTypeMismatchError: If ``other`` is neither a
                compatible request bucket nor one manager instance.
        """
        from general_manager.bucket._materialized_bucket import MaterializedBucket

        if isinstance(other, RequestBucket):
            if self._manager_class != other._manager_class:
                raise RequestBucketManagerMismatchError(
                    self._manager_class,
                    other._manager_class,
                )
            deduplicated = MaterializedBucket(
                self._manager_class, self._ensure_items()
            ) | MaterializedBucket(self._manager_class, other._ensure_items())
            return self._from_items(tuple(deduplicated))
        if isinstance(other, self._manager_class):
            deduplicated = (
                MaterializedBucket(self._manager_class, self._ensure_items()) | other
            )
            return self._from_items(tuple(deduplicated))
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
        if self._raw_source_kind != _MANAGER_SOURCE and self.request_plan is not None:
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

    def _project_rows(self, fields: tuple[str, ...]) -> ProjectionRows:
        """Project request inputs and fields directly from normalized payloads."""
        ensure_as_of_read_supported(self._interface_cls)
        native_fields = set(self._interface_cls.input_fields) | set(
            self._interface_cls.fields
        )
        if not set(fields) <= native_fields:
            return super()._project_rows(fields)
        if self._raw_source_kind == _MANAGER_SOURCE:
            return super()._project_rows(fields)

        rows: list[tuple[object, ...]] = []
        for payload in self._ensure_raw_items():
            identification = self._interface_cls.extract_identification(payload)
            self._manager_class._track_identification_dependency(identification)
            rows.append(
                tuple(
                    identification[field]
                    if field in self._interface_cls.input_fields
                    else self._interface_cls.resolve_payload_value(payload, field)
                    for field in fields
                )
            )
        return tuple(rows)

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
        filter_group = self._normalize_lookup_kwargs(kwargs)
        return handler.build_bucket(
            self._interface_cls,
            operation_name=self._operation_name,
            filters=self.filters,
            excludes=self.excludes,
            filter_call_groups=(*self._filter_call_groups, filter_group),
            exclude_call_groups=self._exclude_call_groups,
        )

    def exclude(self, **kwargs: object) -> "RequestBucket[GeneralManagerType]":
        """Return a bucket excluding items that match the supplied lookups.

        Lazy request-plan buckets compile exclude lookups into the request plan.
        Concrete item buckets created by slicing, unioning, or ``none()`` first
        validate exclude support and then remove matching manager instances in
        memory. Lookups from one call are ANDed before the group is negated.
        Missing attributes do not match and therefore are not excluded. An
        empty call returns an independent equivalent bucket.
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
            if not kwargs:
                return self.all()
            self._validate_materialized_excludes(kwargs)
            return self._from_items(
                tuple(
                    item
                    for item in self._ensure_items()
                    if not all(
                        _matches_manager_lookup(item, key, value)
                        for key, value in kwargs.items()
                    )
                )
            )
        handler = self._query_handler()
        exclude_group = self._normalize_lookup_kwargs(kwargs)
        return handler.build_bucket(
            self._interface_cls,
            operation_name=self._operation_name,
            filters=self.filters,
            excludes=self.excludes,
            filter_call_groups=self._filter_call_groups,
            exclude_call_groups=(*self._exclude_call_groups, exclude_group),
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
        """Materialize and return the number of represented rows."""
        return len(self._ensure_items())

    @property
    def total_count(self) -> int | None:
        """Return the upstream total when no local membership change occurred."""
        self._ensure_raw_items()
        return self._total_count

    @property
    def upstream_page(self) -> int | None:
        """Return the explicit page coordinate of the source response, if any."""
        self._ensure_raw_items()
        return self._upstream_page

    @property
    def upstream_page_size(self) -> int | None:
        """Return the explicit page-size coordinate of the source response, if any."""
        self._ensure_raw_items()
        return self._upstream_page_size

    @property
    def response_is_complete(self) -> bool:
        """Whether the source response proves it contains every matching row."""
        self._ensure_raw_items()
        return self._response_is_complete

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
            filter_call_groups=self._filter_call_groups,
            exclude_call_groups=self._exclude_call_groups,
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
        if not bucket._response_is_complete:
            raise RequestIncompleteResultError(
                bucket.operation_name,
                bucket.total_count,
            )
        return items[0]

    def __getitem__(
        self,
        item: int | slice,
    ) -> GeneralManagerType | Bucket[GeneralManagerType]:
        """Return one materialized item or a materialized bucket slice."""
        items = self._ensure_items()
        if isinstance(item, slice):
            return self._from_items(items[item])
        return items[item]

    def __len__(self) -> int:
        """Return the number of materialized items."""
        return len(self._ensure_items())

    def __contains__(self, item: GeneralManagerType) -> bool:
        """Return whether the exact request identity is present."""
        if item.__class__ is not self._manager_class:
            return False
        identity = _manager_identity(item)
        return any(
            _manager_identity(candidate) == identity
            for candidate in self._ensure_items()
        )

    def sort(
        self,
        *fields: str,
    ) -> "RequestBucket[GeneralManagerType]":
        """Return a materialized bucket sorted by one or more manager attributes.

        Raises:
            RequestBucketSortAttributeError: If any item lacks a requested sort
                attribute.
            TypeError: Propagated when Python cannot compare the resolved sort
                values, such as mixed unrelated value types.
        """
        terms = normalize_ordering(fields)
        if not terms:
            return self._from_items(
                self._ensure_items(), preserve_response_provenance=True
            )
        validate_ordering_fields(self._manager_class, terms)
        try:
            return self._from_items(
                tuple(sort_items(self._ensure_items(), terms)),
                preserve_response_provenance=True,
            )
        except AttributeError as error:
            raise RequestBucketSortAttributeError(
                next(iter(self._ensure_items()), None), error.name or "unknown"
            ) from error

    def none(self) -> "RequestBucket[GeneralManagerType]":
        """Return an empty materialized bucket preserving the operation name."""
        return self._from_items(tuple())

    def with_instances(
        self,
        instances: Iterable[GeneralManagerType],
    ) -> Bucket[GeneralManagerType]:
        """Return a materialized subset without re-executing the request plan."""
        items = tuple(instances)
        for instance in items:
            if instance.__class__ is not self._manager_class:
                raise RequestBucketTypeMismatchError(self.__class__, type(instance))
        return self._from_items(items)

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
            if self._raw_source_kind == _PLAN_SOURCE:
                self._raw_source_kind = _MANAGER_SOURCE
            self._materialized = True
            return self._data
        raw_items = self._ensure_raw_items()
        if not raw_items:
            return self._data
        self._data = tuple(
            self._manager_class(**self._interface_cls.extract_identification(payload))
            for payload in raw_items
        )
        for manager, payload in zip(self._data, raw_items, strict=True):
            _set_request_payload_cache(manager, payload)
        return self._data

    def _ensure_raw_items(self) -> tuple[RequestPayload, ...]:
        """Materialize request payloads without constructing manager instances."""
        ensure_as_of_read_supported(self._interface_cls)
        if self._raw_source_kind == _MANAGER_SOURCE:
            self._materialized = True
            return ()
        if self._raw_source_kind == _SNAPSHOT_SOURCE:
            self._materialized = True
            return self._raw_items
        if self._materialized:
            return self._raw_items
        if self.request_plan is None:
            self._raw_source_kind = _MANAGER_SOURCE
            self._materialized = True
            return ()

        handler = self._query_handler()
        result = handler.execute_plan(self._interface_cls, self.request_plan)
        raw_items = tuple(
            payload
            for payload in result.items
            if _matches_local_predicates(
                payload,
                self._interface_cls,
                self.request_plan.local_predicates,
            )
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
        self._upstream_page = result.page
        self._upstream_page_size = result.page_size
        if self.request_plan.local_predicates:
            self._count_override = len(raw_items)
            self._total_count = None
        else:
            self._count_override = len(raw_items)
            self._total_count = result.total_count
        self._response_is_complete = (
            result.total_count is not None and result.total_count == len(result.items)
        )
        self._raw_items = raw_items
        self._raw_source_kind = _SNAPSHOT_SOURCE
        self._materialized = True
        return self._raw_items

    def _from_items(
        self,
        items: tuple[GeneralManagerType, ...],
        *,
        preserve_response_provenance: bool = False,
    ) -> "RequestBucket[GeneralManagerType]":
        if preserve_response_provenance:
            self._ensure_raw_items()
        return RequestBucket(
            self._manager_class,
            self._interface_cls,
            operation_name=self._operation_name,
            items=items,
            count_override=len(items),
            total_count=self._total_count if preserve_response_provenance else None,
            response_is_complete=(
                self._response_is_complete if preserve_response_provenance else True
            ),
            upstream_page=(
                self._upstream_page if preserve_response_provenance else None
            ),
            upstream_page_size=(
                self._upstream_page_size if preserve_response_provenance else None
            ),
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
    interface_cls: type["RequestInterface"],
    predicates: tuple[RequestLocalPredicate, ...],
) -> bool:
    groups: dict[tuple[str, int], list[RequestLocalPredicate]] = {}
    for predicate in predicates:
        groups.setdefault((predicate.action, predicate.call_group), []).append(
            predicate
        )
    for (action, _), group in groups.items():
        matched = all(
            _matches_payload_lookup(
                payload, interface_cls, predicate.lookup_key, predicate.value
            )
            for predicate in group
        )
        if action == "filter" and not matched:
            return False
        if action == "exclude" and matched:
            return False
    return True


def _matches_payload_lookup(
    payload: RequestPayload,
    interface_cls: type["RequestInterface"],
    lookup_key: str,
    expected: object,
) -> bool:
    path, operator = _split_lookup(lookup_key)
    try:
        current = (
            interface_cls.resolve_payload_value(payload, path[0])
            if len(path) == 1 and path[0] in interface_cls.fields
            else resolve_request_value(payload, path)
        )
    except MissingRequestPayloadFieldError:
        raise
    except KeyError:
        return False
    return _apply_lookup(current, operator, expected)


def _split_lookup(lookup_key: str) -> tuple[tuple[str, ...], str]:
    parts = lookup_key.split("__")
    if parts and parts[-1] == "exact":
        return tuple(parts[:-1]), "exact"
    lookup = lookup_name_from_filter(lookup_key)
    if parts and parts[-1] == lookup and lookup != "exact":
        return tuple(parts[:-1]), parts[-1]
    return tuple(parts), "exact"


def _apply_lookup(value: object, operator: str, expected: object) -> bool:
    return apply_request_lookup(value, operator, expected)
