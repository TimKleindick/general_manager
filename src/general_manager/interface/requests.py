"""Shared request-interface configuration models and errors."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Protocol, runtime_checkable

RequestAction = Literal[
    "all",
    "create",
    "delete",
    "detail",
    "exclude",
    "filter",
    "update",
]
RequestLocation = Literal["query", "headers", "path", "body"]

type RequestSerializer = Callable[[Any], Any]
type RequestValidator = Callable[[Any], None]
type RequestResponse = Mapping[str, Any] | list[Mapping[str, Any]]

SUPPORTED_REQUEST_LOOKUPS = frozenset(
    {"exact", "in", "contains", "icontains", "gt", "gte", "lt", "lte", "isnull"}
)


class RequestInterfaceError(ValueError):
    """Raised when a request-backed interface or bucket receives invalid input."""


class RequestConfigurationError(ValueError):
    """Raised when a request interface declaration is invalid."""


class MissingRequestTransportError(RequestConfigurationError):
    """Raised when a request interface omits its transport."""

    def __init__(self, interface_name: str) -> None:
        super().__init__(f"{interface_name} must define a request transport.")


class MissingRequestAttributeDefinitionsError(RequestConfigurationError):
    """Raised when a request interface omits attribute definitions."""

    def __init__(self, interface_name: str) -> None:
        super().__init__(f"{interface_name} must define attribute_definitions.")


class RequestFieldsRequiredError(RequestConfigurationError):
    """Raised when a request interface omits exposed field declarations."""

    def __init__(self, interface_name: str) -> None:
        super().__init__(f"{interface_name} must declare request fields.")


class MissingRequestDetailOperationError(RequestConfigurationError):
    """Raised when a request interface omits its detail operation."""

    def __init__(self, interface_name: str) -> None:
        super().__init__(f"{interface_name} must define detail_operation.")


class InvalidRequestFilterConfigurationError(RequestConfigurationError):
    """Raised when a request filter declaration is incomplete or contradictory."""

    def __init__(self, filter_key: str) -> None:
        super().__init__(
            f"Request filter '{filter_key}' must define a remote_name, compiler, "
            "or local fallback."
        )


class RequestLocalFallbackRequiredError(RequestConfigurationError):
    """Raised when a local-only filter forgets to enable local fallback."""

    def __init__(self, filter_key: str) -> None:
        super().__init__(
            f"Request filter '{filter_key}' disables remote compilation "
            "without enabling local_fallback."
        )


class UnknownRequestFilterOperationReferenceError(RequestConfigurationError):
    """Raised when a filter references operations that the interface does not expose."""

    def __init__(self, filter_key: str, unknown_operations: set[str]) -> None:
        super().__init__(
            f"Request filter '{filter_key}' references unknown operations "
            f"{sorted(unknown_operations)}."
        )


class UnknownRequestFilterError(KeyError):
    """Raised when a query references an undeclared request filter."""

    def __init__(self, lookup_key: str, operation_name: str) -> None:
        super().__init__(
            f"Unknown request filter '{lookup_key}' for operation '{operation_name}'."
        )


class InvalidRequestFilterValueError(TypeError):
    """Raised when a request filter receives a value with the wrong type."""

    def __init__(
        self,
        lookup_key: str,
        value: object,
        expected: type[Any] | tuple[type[Any], ...],
    ) -> None:
        super().__init__(
            f"Invalid value for request filter '{lookup_key}': "
            f"{type(value)!r}, expected {expected!r}."
        )


class RequestExcludeNotSupportedError(ValueError):
    """Raised when `exclude()` is used for a filter that cannot be negated safely."""

    def __init__(self, lookup_key: str, operation_name: str) -> None:
        super().__init__(
            f"Request filter '{lookup_key}' does not support exclude() "
            f"for operation '{operation_name}'."
        )


class RequestPlanConflictError(ValueError):
    """Raised when multiple filters try to write incompatible request fragments."""

    def __init__(self, *, location: RequestLocation, key: str) -> None:
        super().__init__(
            f"Conflicting request plan entries for {location!r} key '{key}'."
        )


class UnsupportedRequestFilterLookupError(RequestConfigurationError):
    """Raised when a request filter key uses an unsupported lookup suffix."""

    def __init__(self, lookup: str, filter_key: str) -> None:
        super().__init__(
            f"Unsupported request filter lookup '{lookup}' in '{filter_key}'."
        )


class UnknownRequestOperationError(KeyError):
    """Raised when a request interface references an unknown query operation."""

    def __init__(self, operation_name: str) -> None:
        super().__init__(f"Unknown request query operation '{operation_name}'.")


class MissingRequestPayloadFieldError(KeyError):
    """Raised when a required field is missing from a remote payload."""

    def __init__(self, field_name: str, path: tuple[str, ...]) -> None:
        joined = ".".join(path)
        super().__init__(
            f"Missing required request payload field '{field_name}' at '{joined}'."
        )


class UnsupportedRequestLocationError(ValueError):
    """Raised when a filter compiler targets an unsupported request-plan location."""

    def __init__(self, location: str) -> None:
        super().__init__(f"Unsupported request fragment location '{location}'.")


class RequestSingleItemRequiredError(ValueError):
    """Raised when an operation expects exactly one bucket item."""

    def __init__(self) -> None:
        super().__init__("get() requires exactly one item.")


class RequestSingleResponseRequiredError(ValueError):
    """Raised when a detail/create/update operation returns the wrong item count."""

    def __init__(self, interface_name: str, count: int) -> None:
        super().__init__(
            f"{interface_name} expected exactly one item, received {count}."
        )


class RequestLocalPaginationUnsupportedError(ValueError):
    """Raised when local fallback filtering is applied to a partial remote page."""

    def __init__(self, operation_name: str, remote_count: int, page_count: int) -> None:
        super().__init__(
            "Local fallback filtering is not supported for partial remote pages: "
            f"operation '{operation_name}' returned {page_count} items from "
            f"{remote_count} remote matches."
        )


@dataclass(frozen=True, slots=True)
class RequestField:
    """Describe a manager attribute exposed by a request-backed interface."""

    field_type: type[Any]
    source: str | tuple[str, ...] | None = None
    default: Any = None
    is_editable: bool = False
    is_required: bool = True
    is_derived: bool = False
    normalizer: RequestSerializer | None = None

    def value_path(self, field_name: str) -> tuple[str, ...]:
        source = self.source
        if source is None:
            return (field_name,)
        if isinstance(source, tuple):
            return source
        return tuple(source.split("."))


RequestAttribute = RequestField


@dataclass(frozen=True, slots=True)
class RequestLocalPredicate:
    """Represent a client-side predicate applied after a remote response returns."""

    lookup_key: str
    value: Any
    action: RequestAction


@dataclass(frozen=True, slots=True)
class RequestPlanFragment:
    """A partial request-plan contribution produced by a single filter mapping."""

    query_params: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, Any] = field(default_factory=dict)
    path_params: Mapping[str, Any] = field(default_factory=dict)
    body: Mapping[str, Any] = field(default_factory=dict)
    local_predicates: tuple[RequestLocalPredicate, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "query_params", MappingProxyType(dict(self.query_params))
        )
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(
            self, "path_params", MappingProxyType(dict(self.path_params))
        )
        object.__setattr__(self, "body", MappingProxyType(dict(self.body)))
        object.__setattr__(self, "local_predicates", tuple(self.local_predicates))


@dataclass(frozen=True, slots=True)
class RequestFilter:
    """Declare how a single manager lookup compiles into a remote request fragment."""

    remote_name: str | None = None
    location: RequestLocation = "query"
    value_type: type[Any] | tuple[type[Any], ...] | None = None
    serializer: RequestSerializer | None = None
    validator: RequestValidator | None = None
    supports_exclude: bool = False
    exclude_remote_name: str | None = None
    allow_local_fallback: bool = False
    operation_names: frozenset[str] = field(default_factory=frozenset)
    compiler: Callable[["RequestFilterBinding"], RequestPlanFragment] | None = None

    @property
    def param(self) -> str | None:
        return self.remote_name

    @property
    def allow_exclude(self) -> bool:
        return self.supports_exclude

    @property
    def exclude_param(self) -> str | None:
        return self.exclude_remote_name

    @property
    def local_fallback(self) -> bool:
        return self.allow_local_fallback

    @property
    def remote(self) -> bool:
        return self.remote_name is not None

    def applies_to_operation(self, operation_name: str) -> bool:
        return not self.operation_names or operation_name in self.operation_names

    def validate_value(self, filter_key: str, value: Any) -> None:
        if self.value_type is not None and not isinstance(value, self.value_type):
            raise InvalidRequestFilterValueError(filter_key, value, self.value_type)
        if self.validator is not None:
            self.validator(value)


@dataclass(frozen=True, slots=True)
class RequestFilterBinding:
    """Context passed into custom request filter compilers."""

    lookup_key: str
    value: Any
    action: RequestAction
    operation_name: str
    spec: RequestFilter


@dataclass(frozen=True, slots=True)
class RequestOperation:
    """Describe a named remote request operation used by a request interface."""

    name: str
    path: str
    method: str = "GET"
    collection: bool = False
    filters: Mapping[str, "RequestFilter"] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    static_query_params: Mapping[str, Any] = field(default_factory=dict)
    static_headers: Mapping[str, Any] = field(default_factory=dict)
    static_body: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "filters", MappingProxyType(dict(self.filters)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(
            self,
            "static_query_params",
            MappingProxyType(dict(self.static_query_params)),
        )
        object.__setattr__(
            self,
            "static_headers",
            MappingProxyType(dict(self.static_headers)),
        )
        if self.static_body is not None:
            object.__setattr__(
                self, "static_body", MappingProxyType(dict(self.static_body))
            )


RequestQueryOperation = RequestOperation


@dataclass(frozen=True, slots=True)
class RequestPlan:
    """Normalized request plan produced from declarative request operations."""

    operation_name: str
    action: RequestAction
    method: str
    path: str
    query_params: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, Any] = field(default_factory=dict)
    path_params: Mapping[str, Any] = field(default_factory=dict)
    body: Mapping[str, Any] | None = None
    local_predicates: tuple[RequestLocalPredicate, ...] = field(default_factory=tuple)
    filters: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)
    excludes: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "query_params", MappingProxyType(dict(self.query_params))
        )
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(
            self, "path_params", MappingProxyType(dict(self.path_params))
        )
        if self.body is not None:
            object.__setattr__(self, "body", MappingProxyType(dict(self.body)))
        object.__setattr__(self, "local_predicates", tuple(self.local_predicates))
        object.__setattr__(
            self,
            "filters",
            MappingProxyType(
                {key: tuple(values) for key, values in self.filters.items()}
            ),
        )
        object.__setattr__(
            self,
            "excludes",
            MappingProxyType(
                {key: tuple(values) for key, values in self.excludes.items()}
            ),
        )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def local_filters(self) -> dict[str, Any]:
        return {
            predicate.lookup_key: predicate.value
            for predicate in self.local_predicates
            if predicate.action == "filter"
        }


RequestQueryPlan = RequestPlan


@dataclass(frozen=True, slots=True)
class RequestQueryResult:
    """Normalized output returned by request query execution hooks."""

    items: tuple[Mapping[str, Any], ...]
    total_count: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@runtime_checkable
class RequestTransport(Protocol):
    """Protocol for transport adapters used by request-backed interfaces."""

    def execute(
        self,
        *,
        interface_cls: type[Any],
        operation: RequestOperation,
        plan: RequestPlan,
        identification: dict[str, Any] | None = None,
    ) -> RequestResponse: ...


def resolve_request_value(
    payload: Mapping[str, Any] | object, path: tuple[str, ...]
) -> Any:
    """Resolve a dotted field path from a mapping/object payload."""

    current: Any = payload
    for part in path:
        if isinstance(current, Mapping):
            if part not in current:
                raise KeyError(part)
            current = current[part]
            continue
        if hasattr(current, part):
            current = getattr(current, part)
            continue
        raise KeyError(part)
    return current


def validate_filter_key(filter_key: str) -> None:
    """Validate that a request filter key uses a supported lookup suffix."""

    lookup = lookup_name_from_filter(filter_key)
    if lookup not in SUPPORTED_REQUEST_LOOKUPS:
        raise UnsupportedRequestFilterLookupError(lookup, filter_key)


def lookup_name_from_filter(filter_key: str) -> str:
    """Extract the lookup suffix from a request filter key."""

    parts = filter_key.split("__")
    if parts and parts[-1] in SUPPORTED_REQUEST_LOOKUPS:
        return parts[-1]
    return "exact"


def resolve_payload_value(
    payload: Mapping[str, Any],
    source: str | tuple[str, ...] | None,
    field_name: str,
) -> Any:
    """Resolve a configured payload source path for a request attribute."""

    path = (field_name,) if source is None else source
    resolved_path = tuple(path.split(".")) if isinstance(path, str) else path
    try:
        return resolve_request_value(payload, resolved_path)
    except KeyError as error:
        raise MissingRequestPayloadFieldError(field_name, resolved_path) from error


def apply_request_lookup(value_to_check: Any, lookup: str, filter_value: Any) -> bool:
    """Evaluate a request-filter lookup against a candidate value."""

    try:
        if lookup == "exact":
            return value_to_check == filter_value
        if lookup == "lt":
            return value_to_check < filter_value
        if lookup == "lte":
            return value_to_check <= filter_value
        if lookup == "gt":
            return value_to_check > filter_value
        if lookup == "gte":
            return value_to_check >= filter_value
        if lookup == "contains" and isinstance(value_to_check, str):
            return str(filter_value) in value_to_check
        if lookup == "icontains" and isinstance(value_to_check, str):
            return str(filter_value).lower() in value_to_check.lower()
        if lookup == "in":
            return value_to_check in filter_value
        if lookup == "isnull":
            return (value_to_check is None) is bool(filter_value)
    except TypeError:
        return False
    return False


UnsupportedRequestExcludeError = RequestExcludeNotSupportedError
