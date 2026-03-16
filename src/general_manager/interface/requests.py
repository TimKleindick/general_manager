"""Shared request-interface configuration models and errors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import MappingProxyType
import time
from typing import Any, Callable, Literal, Mapping, Protocol, cast, runtime_checkable

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

    @classmethod
    def legacy_declaration(
        cls, interface_name: str, legacy_key: str
    ) -> "RequestConfigurationError":
        return cls(
            f"{interface_name} uses legacy request declaration '{legacy_key}'. "
            "Declare request fields as class attributes and request "
            "configuration inside Interface.Meta."
        )

    @classmethod
    def rules_without_mutations(
        cls, interface_name: str
    ) -> "RequestConfigurationError":
        return cls(f"{interface_name} defines rules without mutation operations.")

    @classmethod
    def invalid_rule_type(cls, interface_name: str) -> "RequestConfigurationError":
        return cls(f"{interface_name} rules must use Rule instances.")

    @classmethod
    def serializer_not_callable(
        cls,
        interface_name: str,
        serializer_name: str,
    ) -> "RequestConfigurationError":
        return cls(f"{interface_name} {serializer_name} must be callable.")

    @classmethod
    def invalid_auth_provider(cls, interface_name: str) -> "RequestConfigurationError":
        return cls(f"{interface_name} auth_provider must define apply(...).")


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


class RequestRemoteError(RequestInterfaceError):
    """Base class for transport or upstream-service request failures."""

    status_code: int | None = None
    request: RequestTransportRequest | None = None
    headers: Mapping[str, Any] | None = None
    retry_count: int = 0


class RequestTransportError(RequestRemoteError):
    """Raised when request execution fails before a usable upstream response exists."""


class RequestAuthenticationError(RequestRemoteError):
    """Raised when the upstream service rejects request authentication."""


class RequestAuthorizationError(RequestRemoteError):
    """Raised when the upstream service denies access to the request."""


class RequestNotFoundError(RequestRemoteError):
    """Raised when the upstream service reports that a resource was not found."""


class RequestConflictError(RequestRemoteError):
    """Raised when the upstream service reports a conflicting request."""


class RequestRateLimitedError(RequestRemoteError):
    """Raised when the upstream service rate-limits the request."""


class RequestServerError(RequestRemoteError):
    """Raised when the upstream service returns a server-side error."""


@dataclass(frozen=True, slots=True)
class RequestTransportRequest:
    """Normalized outbound request sent through a shared request transport."""

    method: str
    url: str
    path: str
    query_params: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, Any] = field(default_factory=dict)
    body: Mapping[str, Any] | None = None
    timeout: float | int | None = None
    operation_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "query_params", MappingProxyType(dict(self.query_params))
        )
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        if self.body is not None:
            object.__setattr__(self, "body", MappingProxyType(dict(self.body)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class RequestTransportResponse:
    """Normalized transport response before conversion into a query result."""

    payload: Mapping[str, Any] | list[Mapping[str, Any]]
    status_code: int
    headers: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@runtime_checkable
class RequestAuthProvider(Protocol):
    """Protocol for applying authentication to an outbound transport request."""

    def apply(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: type[Any],
        operation: "RequestOperation",
        plan: "RequestPlan",
    ) -> RequestTransportRequest: ...


type RequestResponseNormalizer = Callable[
    [
        RequestTransportResponse | RequestResponse,
        type[Any],
        "RequestOperation",
        "RequestPlan",
    ],
    "RequestQueryResult",
]


@dataclass(frozen=True, slots=True)
class RequestTransportConfig:
    """Static configuration used by a shared request transport."""

    base_url: str
    timeout: float | int | None = 10
    auth_provider: RequestAuthProvider | None = None
    response_normalizer: RequestResponseNormalizer | None = None
    retry_policy: "RequestRetryPolicy | None" = None


@dataclass(frozen=True, slots=True)
class RequestRetryPolicy:
    """Framework retry/backoff policy for shared request transports."""

    max_attempts: int = 1
    retryable_status_codes: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    retryable_exceptions: tuple[type[BaseException], ...] = (TimeoutError, OSError)
    base_backoff_seconds: float = 0.0
    retry_non_idempotent_methods: bool = False

    def allows_method(self, method: str) -> bool:
        if self.retry_non_idempotent_methods:
            return True
        return method.upper() in {"GET", "HEAD", "OPTIONS", "DELETE"}


class RequestTransportStatusError(RequestTransportError):
    """Raised by transports when an upstream HTTP-style status indicates failure."""

    def __init__(
        self,
        *,
        status_code: int,
        request: RequestTransportRequest,
        payload: Any = None,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"Upstream request failed with status {status_code}.")
        self.status_code = status_code
        self.request = request
        self.payload = payload
        self.headers = MappingProxyType(dict(headers or {}))


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
class RequestMutationOperation(RequestOperation):
    """Describe a named remote mutation operation used by a request interface."""


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
    ) -> RequestQueryResult | RequestTransportResponse | RequestResponse: ...


class SharedRequestTransport(ABC):
    """Base transport that builds a normalized outbound request from a request plan."""

    def execute(
        self,
        *,
        interface_cls: type[Any],
        operation: RequestOperation,
        plan: RequestPlan,
        identification: dict[str, Any] | None = None,
    ) -> RequestQueryResult:
        config = getattr(interface_cls, "transport_config", None)
        if config is None:
            raise MissingRequestTransportError(interface_cls.__name__)

        retry_policy = config.retry_policy or RequestRetryPolicy()
        base_request = self._build_request(
            config=config, operation=operation, plan=plan
        )
        last_error: Exception | BaseException | None = None
        retry_count = 0

        for attempt in range(1, retry_policy.max_attempts + 1):
            request = base_request
            auth_provider = (
                getattr(interface_cls, "auth_provider", None) or config.auth_provider
            )
            if auth_provider is not None:
                request = auth_provider.apply(
                    request,
                    interface_cls=interface_cls,
                    operation=operation,
                    plan=plan,
                )

            try:
                response = self.send(
                    request,
                    interface_cls=interface_cls,
                    operation=operation,
                    plan=plan,
                    identification=identification,
                )
                response = self._with_retry_count(response, retry_count)
                break
            except RequestTransportStatusError as error:
                last_error = error
                if not self._should_retry_status(
                    error, retry_policy, operation, attempt
                ):
                    mapped_error = map_request_transport_error(error)
                    mapped_error.retry_count = retry_count
                    raise mapped_error from error
            except retry_policy.retryable_exceptions as error:
                last_error = error
                if not self._should_retry_exception(retry_policy, operation, attempt):
                    transport_error = RequestTransportError(str(error))
                    transport_error.retry_count = retry_count
                    raise transport_error from error
            except ValueError as error:
                transport_error = RequestTransportError(str(error))
                transport_error.retry_count = retry_count
                raise transport_error from error

            retry_count += 1
            self._sleep_backoff(retry_policy, retry_count)
        else:  # pragma: no cover - defensive guard
            if isinstance(last_error, RequestTransportStatusError):
                mapped_error = map_request_transport_error(last_error)
                mapped_error.retry_count = retry_count
                raise mapped_error from last_error
            transport_error = RequestTransportError(str(last_error))
            transport_error.retry_count = retry_count
            raise transport_error from last_error

        normalizer = config.response_normalizer or default_request_response_normalizer
        return normalizer(response, interface_cls, operation, plan)

    @staticmethod
    def _should_retry_status(
        error: "RequestTransportStatusError",
        retry_policy: "RequestRetryPolicy",
        operation: "RequestOperation",
        attempt: int,
    ) -> bool:
        return (
            attempt < retry_policy.max_attempts
            and retry_policy.allows_method(operation.method)
            and error.status_code in retry_policy.retryable_status_codes
        )

    @staticmethod
    def _should_retry_exception(
        retry_policy: "RequestRetryPolicy",
        operation: "RequestOperation",
        attempt: int,
    ) -> bool:
        return attempt < retry_policy.max_attempts and retry_policy.allows_method(
            operation.method
        )

    @staticmethod
    def _sleep_backoff(retry_policy: "RequestRetryPolicy", retry_count: int) -> None:
        if retry_policy.base_backoff_seconds <= 0:
            return
        time.sleep(retry_policy.base_backoff_seconds * retry_count)

    @staticmethod
    def _with_retry_count(
        response: "RequestTransportResponse | RequestResponse",
        retry_count: int,
    ) -> "RequestTransportResponse | RequestResponse":
        if not isinstance(response, RequestTransportResponse):
            return response
        metadata = dict(response.metadata)
        metadata["retry_count"] = retry_count
        return RequestTransportResponse(
            payload=response.payload,
            status_code=response.status_code,
            headers=response.headers,
            metadata=metadata,
        )

    @abstractmethod
    def send(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: type[Any],
        operation: RequestOperation,
        plan: RequestPlan,
        identification: dict[str, Any] | None,
    ) -> RequestTransportResponse | RequestResponse:
        """Send a normalized request and return a transport response."""

    @staticmethod
    def _build_request(
        *,
        config: RequestTransportConfig,
        operation: RequestOperation,
        plan: RequestPlan,
    ) -> RequestTransportRequest:
        path = plan.path.format(**plan.path_params)
        query_params = SharedRequestTransport._merge_request_parts(
            operation.static_query_params,
            plan.query_params,
            location="query",
        )
        headers = SharedRequestTransport._merge_request_parts(
            operation.static_headers,
            plan.headers,
            location="headers",
        )
        static_body = operation.static_body or {}
        dynamic_body = plan.body or {}
        body = SharedRequestTransport._merge_request_parts(
            static_body,
            dynamic_body,
            location="body",
        )
        base_url = config.base_url.rstrip("/")
        suffix = path if path.startswith("/") else f"/{path}" if path else ""
        return RequestTransportRequest(
            method=plan.method,
            url=f"{base_url}{suffix}",
            path=path,
            query_params=query_params,
            headers=headers,
            body=body or None,
            timeout=config.timeout,
            operation_name=plan.operation_name,
            metadata=plan.metadata,
        )

    @staticmethod
    def _merge_request_parts(
        static_values: Mapping[str, Any],
        dynamic_values: Mapping[str, Any],
        *,
        location: RequestLocation,
    ) -> dict[str, Any]:
        merged = dict(static_values)
        for key, value in dynamic_values.items():
            if key in merged and merged[key] != value:
                raise RequestPlanConflictError(location=location, key=key)
            merged[key] = value
        return merged


def default_request_response_normalizer(
    response: RequestTransportResponse | RequestResponse,
    interface_cls: type[Any],
    operation: RequestOperation,
    plan: RequestPlan,
) -> RequestQueryResult:
    """Convert raw transport responses into a normalized query result."""

    metadata: dict[str, Any] = dict(plan.metadata)
    payload: RequestResponse
    if isinstance(response, RequestTransportResponse):
        payload = response.payload
        metadata.update(response.metadata)
        metadata["status_code"] = response.status_code
        metadata.setdefault("retry_count", 0)
        if response.headers:
            request_id = response.headers.get("x-request-id")
            if request_id is not None:
                metadata["request_id"] = request_id
            metadata["response_headers"] = dict(response.headers)
    else:
        payload = response

    if isinstance(payload, Mapping):
        return RequestQueryResult(items=(payload,), metadata=metadata)
    return RequestQueryResult(items=tuple(payload), metadata=metadata)


def map_request_transport_error(
    error: RequestTransportStatusError,
) -> RequestRemoteError:
    """Map a low-level transport status error into a stable request exception."""

    status_code = cast(int, error.status_code)
    if status_code == 401:
        mapped: RequestRemoteError = RequestAuthenticationError(str(error))
    elif status_code == 403:
        mapped = RequestAuthorizationError(str(error))
    elif status_code == 404:
        mapped = RequestNotFoundError(str(error))
    elif status_code == 409:
        mapped = RequestConflictError(str(error))
    elif status_code == 429:
        mapped = RequestRateLimitedError(str(error))
    elif status_code >= 500:
        mapped = RequestServerError(str(error))
    else:
        mapped = RequestTransportError(str(error))
    mapped.status_code = status_code
    mapped.request = error.request
    mapped.headers = error.headers
    return mapped


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
