"""Shared request-interface configuration models and errors."""

from __future__ import annotations

from abc import ABC, abstractmethod
import base64
from dataclasses import dataclass, field
import json
import random
from types import MappingProxyType
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request as UrlRequest, urlopen as stdlib_urlopen
from uuid import uuid4
from typing import (
    Callable,
    Iterable,
    Literal,
    Mapping,
    Protocol,
    TypeVar,
    TypedDict,
    cast,
    runtime_checkable,
)

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

type RequestPayload = Mapping[str, object]
type RequestMutablePayload = dict[str, object]
type RequestHeaders = Mapping[str, str]
type RequestMutableHeaders = dict[str, str]
type RequestSerializer = Callable[[object], object]
type RequestValidator = Callable[[object], None]
type RequestResponse = RequestPayload | list[RequestPayload]
type RequestInterfaceType = type[object]
type RequestIdentification = dict[str, object]
RequestPartValue = TypeVar("RequestPartValue")


class SupportsRequestComparison(Protocol):
    """Protocol for values that can be compared by request lookup helpers."""

    def __lt__(self, other: object, /) -> bool:
        """Return whether this value is less than the other value."""
        ...

    def __le__(self, other: object, /) -> bool:
        """Return whether this value is less than or equal to the other value."""
        ...

    def __gt__(self, other: object, /) -> bool:
        """Return whether this value is greater than the other value."""
        ...

    def __ge__(self, other: object, /) -> bool:
        """Return whether this value is greater than or equal to the other value."""
        ...


class UrlopenResponse(Protocol):
    """Small response protocol consumed by `UrllibRequestTransport`."""

    status: int
    headers: RequestHeaders

    def read(self) -> bytes:
        """Return the response body bytes."""
        ...


class UrlopenCallable(Protocol):
    """Callable compatible with `urllib.request.urlopen`."""

    def __call__(
        self,
        request: UrlRequest,
        timeout: float | int | None = None,
    ) -> UrlopenResponse:
        """Execute the prepared URL request and return a readable response."""
        ...


SUPPORTED_REQUEST_LOOKUPS = frozenset(
    {"exact", "in", "contains", "icontains", "gt", "gte", "lt", "lte", "isnull"}
)

_RETRY_POLICY_MAX_ATTEMPTS_ERROR = "RequestRetryPolicy.max_attempts must be at least 1."
_RETRY_POLICY_BASE_BACKOFF_ERROR = (
    "RequestRetryPolicy.base_backoff_seconds cannot be negative."
)
_RETRY_POLICY_BACKOFF_MULTIPLIER_ERROR = (
    "RequestRetryPolicy.backoff_multiplier must be greater than 0."
)
_RETRY_POLICY_JITTER_ERROR = "RequestRetryPolicy.jitter_ratio must be between 0 and 1."
_RETRY_POLICY_MAX_BACKOFF_ERROR = (
    "RequestRetryPolicy.max_backoff_seconds must be greater than or equal to "
    "base_backoff_seconds."
)
_RETRY_POLICY_MISSING_HEADER_ERROR = (
    "RequestRetryPolicy.idempotency_key_header is required when "
    "idempotency_key_factory is provided."
)
_RETRY_POLICY_MISSING_FACTORY_ERROR = (
    "RequestRetryPolicy.idempotency_key_factory is required when "
    "idempotency_key_header is provided."
)
_RETRY_POLICY_NON_CALLABLE_FACTORY_ERROR = (
    "RequestRetryPolicy.idempotency_key_factory must be callable."
)
_RETRY_POLICY_STATUS_CODES_ERROR = (
    "RequestRetryPolicy.retryable_status_codes must be a frozenset of integers."
)
_RETRY_POLICY_EXCEPTIONS_ERROR = (
    "RequestRetryPolicy.retryable_exceptions must be a tuple of exception types."
)
_RETRY_POLICY_HEADER_ERROR = (
    "RequestRetryPolicy.idempotency_key_header must be a non-empty string."
)
_RESOLVE_REQUEST_EMPTY_PATH_ERROR = "resolve_request_value path must not be empty."
_RESOLVE_REQUEST_PATH_TYPE_ERROR = (
    "resolve_request_value path must contain only strings."
)


def _invalid_retry_policy(reason: str) -> ValueError:
    return ValueError(reason)


class RequestInterfaceError(ValueError):
    """Raised when a request-backed interface or bucket receives invalid input."""


class RequestSchemaError(RequestInterfaceError):
    """Raised when a request payload or serializer returns an invalid schema."""

    @classmethod
    def serializer_must_return_mappings(
        cls, interface_name: str
    ) -> "RequestSchemaError":
        """Return an error for serializers that produce non-mapping payloads."""
        return cls(
            f"{interface_name} response_serializer must return mapping payloads."
        )

    @classmethod
    def non_mapping_payload(
        cls,
        interface_name: str,
        operation_name: str,
    ) -> "RequestSchemaError":
        """Return an error for operation payloads that are not mappings."""
        return cls(
            f"{interface_name} returned a non-mapping payload for "
            f"operation '{operation_name}'."
        )

    @classmethod
    def non_mapping_json_list(cls) -> "RequestSchemaError":
        """Return an error for JSON arrays containing non-object items."""
        return cls("HTTP transport received a non-mapping JSON list payload.")

    @classmethod
    def non_object_json_payload(cls) -> "RequestSchemaError":
        """Return an error for decoded JSON that is not an object or object list."""
        return cls("HTTP transport received a non-object JSON payload.")

    @classmethod
    def unsupported_url_scheme(cls, url: str) -> "RequestSchemaError":
        """Return an error for transports asked to call non-HTTP(S) URLs."""
        return cls(f"HTTP transport only supports http/https URLs, got '{url}'.")


class RequestConfigurationError(ValueError):
    """Raised when a request interface declaration is invalid."""

    @classmethod
    def legacy_declaration(
        cls, interface_name: str, legacy_key: str
    ) -> "RequestConfigurationError":
        """Return an error for request config declared in the legacy location."""
        return cls(
            f"{interface_name} uses legacy request declaration '{legacy_key}'. "
            "Declare request fields as class attributes and request "
            "configuration inside Interface.Meta."
        )

    @classmethod
    def rules_without_mutations(
        cls, interface_name: str
    ) -> "RequestConfigurationError":
        """Return an error for validation rules on a read-only request interface."""
        return cls(f"{interface_name} defines rules without mutation operations.")

    @classmethod
    def invalid_rule_type(cls, interface_name: str) -> "RequestConfigurationError":
        """Return an error for non-Rule entries in request-interface rules."""
        return cls(f"{interface_name} rules must use Rule instances.")

    @classmethod
    def serializer_not_callable(
        cls,
        interface_name: str,
        serializer_name: str,
    ) -> "RequestConfigurationError":
        """Return an error for serializer settings that are not callable."""
        return cls(f"{interface_name} {serializer_name} must be callable.")

    @classmethod
    def invalid_auth_provider(cls, interface_name: str) -> "RequestConfigurationError":
        """Return an error for auth providers missing the `apply()` protocol."""
        return cls(f"{interface_name} auth_provider must define apply(...).")

    @classmethod
    def invalid_retry_policy(
        cls,
        interface_name: str,
        reason: str,
    ) -> "RequestConfigurationError":
        """Return an error wrapping an invalid retry policy reason."""
        return cls(f"{interface_name} retry_policy is invalid: {reason}.")

    @classmethod
    def unmapped_remote_error(cls, interface_name: str) -> "RequestConfigurationError":
        """Return an error for remote error payloads without a local mapping."""
        return cls(f"{interface_name} received an unmapped remote error payload.")

    @classmethod
    def missing_remote_manager_fields(
        cls, interface_name: str
    ) -> "RequestConfigurationError":
        """Return an error for remote managers without field declarations."""
        return cls(f"{interface_name} must declare request fields.")

    @classmethod
    def missing_remote_manager_name(
        cls, interface_name: str
    ) -> "RequestConfigurationError":
        """Return an error for remote managers without `Meta.remote_manager`."""
        return cls(f"{interface_name} must define Meta.remote_manager.")

    @classmethod
    def invalid_remote_manager_name(
        cls, interface_name: str
    ) -> "RequestConfigurationError":
        """Return an error for remote manager slugs outside the supported format."""
        return cls(
            f"{interface_name} remote_manager must be a lowercase slug using only "
            "letters, digits, and hyphens."
        )

    @classmethod
    def missing_remote_base_url(
        cls, interface_name: str
    ) -> "RequestConfigurationError":
        """Return an error for remote managers without `Meta.base_url`."""
        return cls(f"{interface_name} must define Meta.base_url.")

    @classmethod
    def missing_remote_protocol_version(
        cls, interface_name: str
    ) -> "RequestConfigurationError":
        """Return an error for remote managers without a protocol version."""
        return cls(f"{interface_name} must define Meta.protocol_version.")

    @classmethod
    def invalid_remote_base_url(
        cls, interface_name: str
    ) -> "RequestConfigurationError":
        """Return an error for non-absolute or non-HTTP(S) base URLs."""
        return cls(f"{interface_name} base_url must be an absolute http/https URL.")

    @classmethod
    def invalid_remote_base_path(
        cls, interface_name: str, reason: str
    ) -> "RequestConfigurationError":
        """Return an error wrapping an invalid remote base path reason."""
        return cls(f"{interface_name} base_path is invalid: {reason}.")


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
        expected: type[object] | tuple[type[object], ...],
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
    headers: RequestHeaders | None = None
    retry_count: int = 0
    error_code: str | None = None
    details: Mapping[str, object] | None = None
    request_id: str | None = None


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
    """Normalized outbound request passed to auth providers and transports.

    `url` is the absolute base URL joined with the formatted operation path,
    while `path` keeps the formatted path alone for tracing and adapters. The
    mappings are copied into immutable mapping proxies during initialization.
    """

    method: str
    url: str
    path: str
    query_params: RequestPayload = field(default_factory=dict)
    headers: RequestHeaders = field(default_factory=dict)
    body: RequestPayload | None = None
    timeout: float | int | None = None
    operation_name: str | None = None
    metadata: RequestPayload = field(default_factory=dict)

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
    """Decoded transport response before conversion into `RequestQueryResult`.

    `payload` must already be decoded into either one mapping or a list of
    mappings. Use `metadata` for adapter-specific values that should survive
    response normalization.
    """

    payload: RequestResponse
    status_code: int
    headers: RequestHeaders = field(default_factory=dict)
    metadata: RequestPayload = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class RequestMetricsBackend(Protocol):
    """Protocol for recording request-interface metrics."""

    def record_request(
        self,
        *,
        service: str,
        operation: str,
        method: str,
        status_code: int,
        outcome: str,
        duration: float,
        retry_count: int,
    ) -> None:
        """Record one completed transport execution in seconds."""
        ...

    def record_error(
        self,
        *,
        service: str,
        operation: str,
        method: str,
        error_class: str,
        status_code: int | None,
        retry_count: int,
    ) -> None:
        """Record one failed transport execution after retries stop."""
        ...


class NoopRequestMetricsBackend:
    """Default metrics backend used when request metrics are not configured."""

    def record_request(
        self,
        *,
        service: str,
        operation: str,
        method: str,
        status_code: int,
        outcome: str,
        duration: float,
        retry_count: int,
    ) -> None:
        """Accept a successful request metric without recording it."""
        return None

    def record_error(
        self,
        *,
        service: str,
        operation: str,
        method: str,
        error_class: str,
        status_code: int | None,
        retry_count: int,
    ) -> None:
        """Accept an error metric without recording it."""
        return None


class RequestTraceBackend(Protocol):
    """Protocol for optional request tracing hooks."""

    def on_request_start(
        self,
        *,
        service: str,
        operation: str,
        method: str,
        path: str,
    ) -> object:
        """Start tracing for the overall request execution and return context."""
        ...

    def on_request_end(
        self,
        *,
        trace_context: object,
        service: str,
        operation: str,
        method: str,
        path: str,
        status_code: int,
        request_id: str | None,
        retry_count: int,
    ) -> None:
        """Finish tracing for a successful request execution."""
        ...

    def on_request_error(
        self,
        *,
        trace_context: object,
        service: str,
        operation: str,
        method: str,
        path: str,
        error: Exception,
        status_code: int | None,
        retry_count: int,
    ) -> None:
        """Finish tracing for a failed request execution."""
        ...


class NoopRequestTraceBackend:
    """Default tracing backend used when request tracing is not configured."""

    def on_request_start(
        self,
        *,
        service: str,
        operation: str,
        method: str,
        path: str,
    ) -> object:
        """Return an inert trace context for a request start event."""
        return None

    def on_request_end(
        self,
        *,
        trace_context: object,
        service: str,
        operation: str,
        method: str,
        path: str,
        status_code: int,
        request_id: str | None,
        retry_count: int,
    ) -> None:
        """Ignore a successful request end event."""
        return None

    def on_request_error(
        self,
        *,
        trace_context: object,
        service: str,
        operation: str,
        method: str,
        path: str,
        error: Exception,
        status_code: int | None,
        retry_count: int,
    ) -> None:
        """Ignore a failed request end event."""
        return None


@runtime_checkable
class RequestAuthProvider(Protocol):
    """Protocol for applying authentication to an outbound transport request."""

    def apply(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: RequestInterfaceType,
        operation: "RequestOperation",
        plan: "RequestPlan",
    ) -> RequestTransportRequest:
        """Return a request copy with authentication data applied."""
        ...


type RequestResponseNormalizer = Callable[
    [
        RequestTransportResponse | RequestResponse,
        RequestInterfaceType,
        "RequestOperation",
        "RequestPlan",
    ],
    "RequestQueryResult",
]


@dataclass(frozen=True, slots=True)
class RequestTransportConfig:
    """Static configuration used by a shared request transport.

    `base_url` must be an absolute HTTP(S) origin, optionally with a base path;
    operation paths are joined with one slash. `timeout` is expressed in
    seconds and may be overridden per operation.
    """

    base_url: str
    timeout: float | int | None = 10
    auth_provider: RequestAuthProvider | None = None
    response_normalizer: RequestResponseNormalizer | None = None
    retry_policy: "RequestRetryPolicy | None" = None
    metrics_backend: RequestMetricsBackend | None = None
    trace_backend: RequestTraceBackend | None = None


@dataclass(frozen=True, slots=True)
class RequestRetryPolicy:
    """Framework retry/backoff policy for shared request transports.

    By default only idempotent HTTP methods are retried. Set
    `retry_non_idempotent_methods` with an idempotency-key header/factory when
    retrying mutation methods such as POST or PATCH.
    """

    max_attempts: int = 1
    retryable_status_codes: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    retryable_exceptions: tuple[type[BaseException], ...] = (TimeoutError, OSError)
    base_backoff_seconds: float = 0.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float | None = None
    jitter_ratio: float = 0.0
    retry_non_idempotent_methods: bool = False
    idempotency_key_header: str | None = None
    idempotency_key_factory: Callable[[], str] | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise _invalid_retry_policy(_RETRY_POLICY_MAX_ATTEMPTS_ERROR)
        if not isinstance(self.retryable_status_codes, frozenset) or not all(
            isinstance(code, int) for code in self.retryable_status_codes
        ):
            raise _invalid_retry_policy(_RETRY_POLICY_STATUS_CODES_ERROR)
        if not isinstance(self.retryable_exceptions, tuple) or not all(
            isinstance(exc, type) and issubclass(exc, BaseException)
            for exc in self.retryable_exceptions
        ):
            raise _invalid_retry_policy(_RETRY_POLICY_EXCEPTIONS_ERROR)
        if self.base_backoff_seconds < 0.0:
            raise _invalid_retry_policy(_RETRY_POLICY_BASE_BACKOFF_ERROR)
        if self.backoff_multiplier <= 0.0:
            raise _invalid_retry_policy(_RETRY_POLICY_BACKOFF_MULTIPLIER_ERROR)
        if not 0.0 <= self.jitter_ratio <= 1.0:
            raise _invalid_retry_policy(_RETRY_POLICY_JITTER_ERROR)
        if (
            self.max_backoff_seconds is not None
            and self.max_backoff_seconds < self.base_backoff_seconds
        ):
            raise _invalid_retry_policy(_RETRY_POLICY_MAX_BACKOFF_ERROR)
        if self.idempotency_key_header is None and self.idempotency_key_factory is None:
            return
        if self.idempotency_key_header is not None and (
            not isinstance(self.idempotency_key_header, str)
            or not self.idempotency_key_header.strip()
        ):
            raise _invalid_retry_policy(_RETRY_POLICY_HEADER_ERROR)
        if self.idempotency_key_header is None:
            raise _invalid_retry_policy(_RETRY_POLICY_MISSING_HEADER_ERROR)
        if self.idempotency_key_factory is None:
            raise _invalid_retry_policy(_RETRY_POLICY_MISSING_FACTORY_ERROR)
        if not callable(self.idempotency_key_factory):
            raise _invalid_retry_policy(_RETRY_POLICY_NON_CALLABLE_FACTORY_ERROR)

    def allows_method(self, method: str) -> bool:
        """Return whether this policy permits retrying the HTTP method."""
        if self.retry_non_idempotent_methods:
            return True
        return method.upper() in {"GET", "HEAD", "OPTIONS", "DELETE"}

    def compute_backoff_seconds(
        self,
        *,
        retry_count: int,
        random_factor: float | None = None,
    ) -> float:
        """Return the 1-based retry delay in seconds after optional jitter."""
        if self.base_backoff_seconds <= 0:
            return 0.0
        backoff = self.base_backoff_seconds * (
            self.backoff_multiplier ** (retry_count - 1)
        )
        if self.jitter_ratio > 0:
            factor = (
                random_factor
                if random_factor is not None
                else random.SystemRandom().uniform(-1.0, 1.0)
            )
            backoff *= 1 + (self.jitter_ratio * factor)
        if self.max_backoff_seconds is not None:
            backoff = min(backoff, self.max_backoff_seconds)
        return max(backoff, 0.0)

    def build_idempotency_key(self) -> str:
        """Return a caller-supplied idempotency key or generate a UUID string."""
        factory = self.idempotency_key_factory
        if factory is not None:
            return factory()
        return str(uuid4())


class RequestTransportStatusError(RequestTransportError):
    """Raised by transports when an upstream HTTP-style status indicates failure."""

    def __init__(
        self,
        *,
        status_code: int,
        request: RequestTransportRequest,
        payload: object = None,
        headers: RequestHeaders | None = None,
    ) -> None:
        """Store the failed request, decoded payload, response headers, and status."""
        super().__init__(f"Upstream request failed with status {status_code}.")
        self.status_code = status_code
        self.request = request
        self.payload = payload
        self.headers = MappingProxyType(dict(headers or {}))


def _resolve_secret(value: str | Callable[[], str]) -> str:
    resolved = value() if callable(value) else value
    return str(resolved)


@dataclass(frozen=True, slots=True)
class BearerTokenAuthProvider:
    """Apply a bearer token to the `Authorization` header."""

    token: str | Callable[[], str]
    header_name: str = "Authorization"

    def apply(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: RequestInterfaceType,
        operation: "RequestOperation",
        plan: "RequestPlan",
    ) -> RequestTransportRequest:
        """Return a request copy with a `Bearer <token>` header."""
        headers = dict(request.headers)
        headers[self.header_name] = f"Bearer {_resolve_secret(self.token)}"
        return RequestTransportRequest(
            method=request.method,
            url=request.url,
            path=request.path,
            query_params=request.query_params,
            headers=headers,
            body=request.body,
            timeout=request.timeout,
            operation_name=request.operation_name,
            metadata=request.metadata,
        )


@dataclass(frozen=True, slots=True)
class HeaderApiKeyAuthProvider:
    """Apply an API key to a configured request header."""

    header_name: str
    api_key: str | Callable[[], str]

    def apply(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: RequestInterfaceType,
        operation: "RequestOperation",
        plan: "RequestPlan",
    ) -> RequestTransportRequest:
        """Return a request copy with the configured header set to the API key."""
        headers = dict(request.headers)
        headers[self.header_name] = _resolve_secret(self.api_key)
        return RequestTransportRequest(
            method=request.method,
            url=request.url,
            path=request.path,
            query_params=request.query_params,
            headers=headers,
            body=request.body,
            timeout=request.timeout,
            operation_name=request.operation_name,
            metadata=request.metadata,
        )


@dataclass(frozen=True, slots=True)
class QueryApiKeyAuthProvider:
    """Apply an API key to a configured query-string parameter."""

    param_name: str
    api_key: str | Callable[[], str]

    def apply(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: RequestInterfaceType,
        operation: "RequestOperation",
        plan: "RequestPlan",
    ) -> RequestTransportRequest:
        """Return a request copy with the configured query parameter set."""
        query_params = dict(request.query_params)
        query_params[self.param_name] = _resolve_secret(self.api_key)
        return RequestTransportRequest(
            method=request.method,
            url=request.url,
            path=request.path,
            query_params=query_params,
            headers=request.headers,
            body=request.body,
            timeout=request.timeout,
            operation_name=request.operation_name,
            metadata=request.metadata,
        )


@dataclass(frozen=True, slots=True)
class BasicAuthProvider:
    """Apply HTTP basic auth to the `Authorization` header."""

    username: str | Callable[[], str]
    password: str | Callable[[], str]
    header_name: str = "Authorization"

    def apply(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: RequestInterfaceType,
        operation: "RequestOperation",
        plan: "RequestPlan",
    ) -> RequestTransportRequest:
        """Return a request copy with an RFC 7617-style Basic auth header."""
        username = _resolve_secret(self.username)
        password = _resolve_secret(self.password)
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode(
            "ascii"
        )
        headers = dict(request.headers)
        headers[self.header_name] = f"Basic {token}"
        return RequestTransportRequest(
            method=request.method,
            url=request.url,
            path=request.path,
            query_params=request.query_params,
            headers=headers,
            body=request.body,
            timeout=request.timeout,
            operation_name=request.operation_name,
            metadata=request.metadata,
        )


@dataclass(frozen=True, slots=True)
class FieldMappingSerializer:
    """Map one dictionary shape into another using declared key names.

    The mapping is `{target_key: source_key}`. Missing source keys raise
    `KeyError`; extra payload keys are ignored.
    """

    field_map: Mapping[str, str]

    def __call__(self, payload: RequestPayload) -> RequestMutablePayload:
        """Return a new payload containing only mapped target keys."""
        return {
            target_key: payload[source_key]
            for target_key, source_key in self.field_map.items()
        }


@dataclass(frozen=True, slots=True)
class RequestField:
    """Describe a manager attribute exposed by a request-backed interface.

    `source` may be a dotted path or tuple path inside the remote payload.
    `default` is used by higher-level interface code when an optional value is
    absent, and `normalizer` can convert the resolved value before assignment.
    """

    field_type: type[object]
    source: str | tuple[str, ...] | None = None
    default: object = None
    is_editable: bool = False
    is_required: bool = True
    is_derived: bool = False
    normalizer: RequestSerializer | None = None

    def value_path(self, field_name: str) -> tuple[str, ...]:
        """Return the payload path used to resolve this field's value."""
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
    value: object
    action: RequestAction


@dataclass(frozen=True, slots=True)
class RequestPlanFragment:
    """A partial request-plan contribution produced by one filter mapping."""

    query_params: RequestPayload = field(default_factory=dict)
    headers: RequestHeaders = field(default_factory=dict)
    path_params: RequestPayload = field(default_factory=dict)
    body: RequestPayload = field(default_factory=dict)
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
    """Declare how one manager lookup compiles into a remote request fragment.

    `location` chooses whether the compiled value is written to query params,
    headers, path params, or body. `compiler` receives a `RequestFilterBinding`
    and may return a custom fragment. Filters without remote output must enable
    `allow_local_fallback` so matching happens against returned items.
    """

    remote_name: str | None = None
    location: RequestLocation = "query"
    value_type: type[object] | tuple[type[object], ...] | None = None
    serializer: RequestSerializer | None = None
    validator: RequestValidator | None = None
    supports_exclude: bool = False
    exclude_remote_name: str | None = None
    allow_local_fallback: bool = False
    operation_names: frozenset[str] = field(default_factory=frozenset)
    compiler: Callable[["RequestFilterBinding"], RequestPlanFragment] | None = None

    @property
    def param(self) -> str | None:
        """Return the legacy name for `remote_name`."""
        return self.remote_name

    @property
    def allow_exclude(self) -> bool:
        """Return the legacy name for `supports_exclude`."""
        return self.supports_exclude

    @property
    def exclude_param(self) -> str | None:
        """Return the legacy name for `exclude_remote_name`."""
        return self.exclude_remote_name

    @property
    def local_fallback(self) -> bool:
        """Return the legacy name for `allow_local_fallback`."""
        return self.allow_local_fallback

    @property
    def remote(self) -> bool:
        """Return whether this filter writes a remote request parameter."""
        return self.remote_name is not None

    def applies_to_operation(self, operation_name: str) -> bool:
        """Return whether this filter may be used with the named operation."""
        return not self.operation_names or operation_name in self.operation_names

    def validate_value(self, filter_key: str, value: object) -> None:
        """Validate a lookup value and raise when its type or validator rejects it."""
        if self.value_type is not None and not isinstance(value, self.value_type):
            raise InvalidRequestFilterValueError(filter_key, value, self.value_type)
        if self.validator is not None:
            self.validator(value)


@dataclass(frozen=True, slots=True)
class RequestFilterBinding:
    """Context passed into custom request filter compilers."""

    lookup_key: str
    value: object
    action: RequestAction
    operation_name: str
    spec: RequestFilter


@dataclass(frozen=True, slots=True)
class RequestOperation:
    """Describe a named remote request operation used by a request interface.

    The `path` can contain `{name}` placeholders populated from
    `RequestPlan.path_params`. Static request parts are merged with the plan at
    execution time and conflicting duplicate keys raise `RequestPlanConflictError`.
    `filters=None` is a request-interface sentinel meaning "inherit the
    interface-level filter mapping"; an explicit mapping, including an empty
    mapping, is operation-specific.
    """

    name: str
    path: str
    method: str = "GET"
    collection: bool = False
    filters: Mapping[str, "RequestFilter"] | None = field(default_factory=dict)
    metadata: RequestPayload = field(default_factory=dict)
    static_query_params: RequestPayload = field(default_factory=dict)
    static_headers: RequestHeaders = field(default_factory=dict)
    static_body: RequestPayload | None = None
    timeout: float | int | None = None

    def __post_init__(self) -> None:
        if self.filters is not None:
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
    """Describe a named create, update, or delete operation for a request interface."""


class RequestPlanState(TypedDict):
    """Pickle state used to rebuild immutable `RequestPlan` instances."""

    operation_name: str
    action: RequestAction
    method: str
    path: str
    query_params: RequestMutablePayload
    headers: RequestMutableHeaders
    path_params: RequestMutablePayload
    body: RequestMutablePayload | None
    local_predicates: tuple[RequestLocalPredicate, ...]
    filters: dict[str, tuple[object, ...]]
    excludes: dict[str, tuple[object, ...]]
    metadata: RequestMutablePayload


@dataclass(frozen=True, slots=True)
class RequestPlan:
    """Normalized request plan produced from declarative request operations.

    Transports consume this immutable value object. `query_params`, `headers`,
    `path_params`, and `body` are outbound request fragments; `filters` and
    `excludes` retain original manager lookup values for local fallback and
    introspection.
    """

    operation_name: str
    action: RequestAction
    method: str
    path: str
    query_params: RequestPayload = field(default_factory=dict)
    headers: RequestHeaders = field(default_factory=dict)
    path_params: RequestPayload = field(default_factory=dict)
    body: RequestPayload | None = None
    local_predicates: tuple[RequestLocalPredicate, ...] = field(default_factory=tuple)
    filters: Mapping[str, tuple[object, ...]] = field(default_factory=dict)
    excludes: Mapping[str, tuple[object, ...]] = field(default_factory=dict)
    metadata: RequestPayload = field(default_factory=dict)

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

    def __reduce__(
        self,
    ) -> tuple[Callable[[RequestPlanState], "RequestPlan"], tuple[RequestPlanState]]:
        """Return pickle state that restores immutable mapping proxies as mappings."""
        return (
            _restore_request_plan,
            (
                {
                    "operation_name": self.operation_name,
                    "action": self.action,
                    "method": self.method,
                    "path": self.path,
                    "query_params": dict(self.query_params),
                    "headers": dict(self.headers),
                    "path_params": dict(self.path_params),
                    "body": dict(self.body) if self.body is not None else None,
                    "local_predicates": tuple(self.local_predicates),
                    "filters": {
                        key: tuple(values) for key, values in self.filters.items()
                    },
                    "excludes": {
                        key: tuple(values) for key, values in self.excludes.items()
                    },
                    "metadata": dict(self.metadata),
                },
            ),
        )

    @property
    def local_filters(self) -> dict[str, object]:
        """Return local filter predicates keyed by lookup name."""
        return {
            predicate.lookup_key: predicate.value
            for predicate in self.local_predicates
            if predicate.action == "filter"
        }


RequestQueryPlan = RequestPlan


def _restore_request_plan(state: RequestPlanState) -> RequestPlan:
    return RequestPlan(
        operation_name=state["operation_name"],
        action=state["action"],
        method=state["method"],
        path=state["path"],
        query_params=state["query_params"],
        headers=state["headers"],
        path_params=state["path_params"],
        body=state["body"],
        local_predicates=tuple(state["local_predicates"]),
        filters=state["filters"],
        excludes=state["excludes"],
        metadata=state["metadata"],
    )


@dataclass(frozen=True, slots=True)
class RequestQueryResult:
    """Normalized output returned by request query execution hooks.

    `items` contains zero or more mapping payloads after response normalization.
    Single-object responses are represented as a one-item tuple.
    """

    items: tuple[RequestPayload, ...]
    total_count: int | None = None
    metadata: RequestPayload = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@runtime_checkable
class RequestTransport(Protocol):
    """Protocol for transport adapters used by request-backed interfaces."""

    def execute(
        self,
        *,
        interface_cls: RequestInterfaceType,
        operation: RequestOperation,
        plan: RequestPlan,
        identification: RequestIdentification | None = None,
    ) -> RequestQueryResult | RequestTransportResponse | RequestResponse:
        """Execute a request plan and return either raw or normalized results."""
        ...


class SharedRequestTransport(ABC):
    """Base transport that builds and executes a request from a request plan.

    `execute()` performs request construction, idempotency-key injection, auth,
    retry handling, metrics, tracing, and response normalization. Subclasses
    implement only `send()` for adapter-specific I/O.
    """

    def execute(
        self,
        *,
        interface_cls: RequestInterfaceType,
        operation: RequestOperation,
        plan: RequestPlan,
        identification: RequestIdentification | None = None,
    ) -> RequestQueryResult:
        """Execute a plan and return normalized query results."""
        config = getattr(interface_cls, "transport_config", None)
        if config is None:
            raise MissingRequestTransportError(interface_cls.__name__)

        retry_policy = (
            getattr(interface_cls, "retry_policy", None)
            or config.retry_policy
            or RequestRetryPolicy()
        )
        base_request = self._build_request(
            config=config, operation=operation, plan=plan
        )
        base_request = self._with_idempotency_key(
            request=base_request,
            operation=operation,
            retry_policy=retry_policy,
        )
        last_error: Exception | BaseException | None = None
        retry_count = 0
        metrics_backend = config.metrics_backend or NoopRequestMetricsBackend()
        trace_backend = config.trace_backend or NoopRequestTraceBackend()
        service_name = interface_cls.__name__
        trace_context = trace_backend.on_request_start(
            service=service_name,
            operation=operation.name,
            method=operation.method,
            path=base_request.path,
        )
        started_at = time.monotonic()

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
                    metrics_backend.record_error(
                        service=service_name,
                        operation=operation.name,
                        method=operation.method,
                        error_class=type(mapped_error).__name__,
                        status_code=mapped_error.status_code,
                        retry_count=retry_count,
                    )
                    trace_backend.on_request_error(
                        trace_context=trace_context,
                        service=service_name,
                        operation=operation.name,
                        method=operation.method,
                        path=base_request.path,
                        error=mapped_error,
                        status_code=mapped_error.status_code,
                        retry_count=retry_count,
                    )
                    raise mapped_error from error
            except retry_policy.retryable_exceptions as error:
                last_error = error
                if not self._should_retry_exception(retry_policy, operation, attempt):
                    transport_error = RequestTransportError(str(error))
                    transport_error.retry_count = retry_count
                    metrics_backend.record_error(
                        service=service_name,
                        operation=operation.name,
                        method=operation.method,
                        error_class=type(transport_error).__name__,
                        status_code=None,
                        retry_count=retry_count,
                    )
                    trace_backend.on_request_error(
                        trace_context=trace_context,
                        service=service_name,
                        operation=operation.name,
                        method=operation.method,
                        path=base_request.path,
                        error=transport_error,
                        status_code=None,
                        retry_count=retry_count,
                    )
                    raise transport_error from error
            except ValueError as error:
                if isinstance(error, RequestSchemaError):
                    metrics_backend.record_error(
                        service=service_name,
                        operation=operation.name,
                        method=operation.method,
                        error_class=type(error).__name__,
                        status_code=None,
                        retry_count=retry_count,
                    )
                    trace_backend.on_request_error(
                        trace_context=trace_context,
                        service=service_name,
                        operation=operation.name,
                        method=operation.method,
                        path=base_request.path,
                        error=error,
                        status_code=None,
                        retry_count=retry_count,
                    )
                    raise
                transport_error = RequestTransportError(str(error))
                transport_error.retry_count = retry_count
                metrics_backend.record_error(
                    service=service_name,
                    operation=operation.name,
                    method=operation.method,
                    error_class=type(transport_error).__name__,
                    status_code=None,
                    retry_count=retry_count,
                )
                trace_backend.on_request_error(
                    trace_context=trace_context,
                    service=service_name,
                    operation=operation.name,
                    method=operation.method,
                    path=base_request.path,
                    error=transport_error,
                    status_code=None,
                    retry_count=retry_count,
                )
                raise transport_error from error

            retry_count += 1
            self._sleep_backoff(retry_policy, retry_count)
        else:  # pragma: no cover - defensive guard
            if isinstance(last_error, RequestTransportStatusError):
                mapped_error = map_request_transport_error(last_error)
                mapped_error.retry_count = retry_count
                metrics_backend.record_error(
                    service=service_name,
                    operation=operation.name,
                    method=operation.method,
                    error_class=type(mapped_error).__name__,
                    status_code=mapped_error.status_code,
                    retry_count=retry_count,
                )
                trace_backend.on_request_error(
                    trace_context=trace_context,
                    service=service_name,
                    operation=operation.name,
                    method=operation.method,
                    path=base_request.path,
                    error=mapped_error,
                    status_code=mapped_error.status_code,
                    retry_count=retry_count,
                )
                raise mapped_error from last_error
            transport_error = RequestTransportError(str(last_error))
            transport_error.retry_count = retry_count
            metrics_backend.record_error(
                service=service_name,
                operation=operation.name,
                method=operation.method,
                error_class=type(transport_error).__name__,
                status_code=None,
                retry_count=retry_count,
            )
            trace_backend.on_request_error(
                trace_context=trace_context,
                service=service_name,
                operation=operation.name,
                method=operation.method,
                path=base_request.path,
                error=transport_error,
                status_code=None,
                retry_count=retry_count,
            )
            raise transport_error from last_error

        normalizer = config.response_normalizer or default_request_response_normalizer
        result = normalizer(response, interface_cls, operation, plan)
        duration = max(time.monotonic() - started_at, 0.0)
        retry_count_value = cast(int, result.metadata.get("retry_count", 0))
        status_code = cast(int, result.metadata.get("status_code", 0))
        request_id = cast(str | None, result.metadata.get("request_id"))
        metrics_backend.record_request(
            service=service_name,
            operation=operation.name,
            method=operation.method,
            status_code=status_code,
            outcome="success",
            duration=duration,
            retry_count=retry_count_value,
        )
        trace_backend.on_request_end(
            trace_context=trace_context,
            service=service_name,
            operation=operation.name,
            method=operation.method,
            path=base_request.path,
            status_code=status_code,
            request_id=request_id,
            retry_count=retry_count_value,
        )
        return result

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
        seconds = retry_policy.compute_backoff_seconds(retry_count=retry_count)
        if seconds <= 0:
            return
        time.sleep(seconds)

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
        interface_cls: RequestInterfaceType,
        operation: RequestOperation,
        plan: RequestPlan,
        identification: RequestIdentification | None,
    ) -> RequestTransportResponse | RequestResponse:
        """Send a normalized request and return a transport response."""

    @staticmethod
    def _build_request(
        *,
        config: RequestTransportConfig,
        operation: RequestOperation,
        plan: RequestPlan,
    ) -> RequestTransportRequest:
        encoded_path_params = {
            key: quote(str(value), safe="") for key, value in plan.path_params.items()
        }
        path = plan.path.format(**encoded_path_params)
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
            timeout=operation.timeout
            if operation.timeout is not None
            else config.timeout,
            operation_name=plan.operation_name,
            metadata=plan.metadata,
        )

    @staticmethod
    def _with_idempotency_key(
        *,
        request: RequestTransportRequest,
        operation: RequestOperation,
        retry_policy: "RequestRetryPolicy",
    ) -> RequestTransportRequest:
        if (
            retry_policy.idempotency_key_header is None
            or retry_policy.max_attempts <= 1
            or not retry_policy.retry_non_idempotent_methods
            or operation.method.upper() in {"GET", "HEAD", "OPTIONS", "DELETE"}
        ):
            return request
        headers = dict(request.headers)
        headers.setdefault(
            retry_policy.idempotency_key_header,
            retry_policy.build_idempotency_key(),
        )
        return RequestTransportRequest(
            method=request.method,
            url=request.url,
            path=request.path,
            query_params=request.query_params,
            headers=headers,
            body=request.body,
            timeout=request.timeout,
            operation_name=request.operation_name,
            metadata=request.metadata,
        )

    @staticmethod
    def _merge_request_parts(
        static_values: Mapping[str, RequestPartValue],
        dynamic_values: Mapping[str, RequestPartValue],
        *,
        location: RequestLocation,
    ) -> dict[str, RequestPartValue]:
        merged = dict(static_values)
        for key, value in dynamic_values.items():
            if key in merged and merged[key] != value:
                raise RequestPlanConflictError(location=location, key=key)
            merged[key] = value
        return merged


def default_request_response_normalizer(
    response: RequestTransportResponse | RequestResponse,
    interface_cls: RequestInterfaceType,
    operation: RequestOperation,
    plan: RequestPlan,
) -> RequestQueryResult:
    """Convert transport responses into `RequestQueryResult`.

    Mapping payloads become a single item; lists must contain only mappings.
    Transport metadata, status code, response headers, retry count, and request
    id are copied into result metadata when available.
    """

    metadata: RequestMutablePayload = dict(plan.metadata)
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
    items = tuple(payload)
    if not all(isinstance(item, Mapping) for item in items):
        raise RequestSchemaError.non_mapping_payload(
            interface_cls.__name__,
            operation.name,
        )
    return RequestQueryResult(
        items=items,
        metadata=metadata,
    )


def map_request_transport_error(
    error: RequestTransportStatusError,
) -> RequestRemoteError:
    """Map a low-level status error into a stable request exception.

    Status codes map as 401 auth, 403 authorization, 404 not found, 409
    conflict, 429 rate limit, and 5xx server error. Mapping payload metadata may
    fill `error_code`, `details`, and `request_id`.
    """

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
    payload = getattr(error, "payload", None)
    if isinstance(payload, Mapping):
        error_code = payload.get("error_code")
        if isinstance(error_code, str):
            mapped.error_code = error_code
        details = payload.get("details")
        if isinstance(details, Mapping):
            mapped.details = cast(Mapping[str, object], details)
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            request_id = metadata.get("request_id")
            if isinstance(request_id, str):
                mapped.request_id = request_id
    if mapped.request_id is None and error.headers is not None:
        header_request_id = error.headers.get("X-Request-ID") or error.headers.get(
            "x-request-id"
        )
        if isinstance(header_request_id, str):
            mapped.request_id = header_request_id
    return mapped


class UrllibRequestTransport(SharedRequestTransport):
    """First-party JSON transport backed by Python's stdlib HTTP client.

    The transport supports absolute HTTP(S) URLs, JSON request bodies, decoded
    JSON object/list responses, operation-level timeout overrides, and HTTP
    status mapping through `RequestTransportStatusError`.
    """

    def __init__(
        self,
        *,
        urlopen: UrlopenCallable | None = None,
        json_dumps: Callable[[object], str] | None = None,
    ) -> None:
        self._urlopen = urlopen or stdlib_urlopen
        self._json_dumps = json_dumps or json.dumps

    def send(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: RequestInterfaceType,
        operation: RequestOperation,
        plan: RequestPlan,
        identification: RequestIdentification | None,
    ) -> RequestTransportResponse:
        """Send a normalized HTTP request and return a decoded response."""
        url = self._build_url(request)
        if urlsplit(url).scheme not in {"http", "https"}:
            raise RequestSchemaError.unsupported_url_scheme(url)
        body_bytes: bytes | None = None
        headers = dict(request.headers)
        if request.body is not None:
            body_bytes = self._json_dumps(dict(request.body)).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")

        raw_request = UrlRequest(  # noqa: S310 - scheme is restricted above
            url=url,
            data=body_bytes,
            headers=headers,
            method=request.method,
        )
        try:
            raw_response = self._urlopen(raw_request, timeout=request.timeout)
            payload = self._decode_payload(raw_response.read())
            return RequestTransportResponse(
                payload=payload,
                status_code=int(getattr(raw_response, "status", 200)),
                headers=dict(getattr(raw_response, "headers", {})),
            )
        except HTTPError as error:
            raise RequestTransportStatusError(
                status_code=error.code,
                request=request,
                payload=self._decode_payload(error.read()),
                headers=dict(error.headers.items()),
            ) from error
        except URLError as error:
            raise OSError(str(error.reason)) from error

    @staticmethod
    def _build_url(request: RequestTransportRequest) -> str:
        if not request.query_params:
            return request.url
        query_string = urlencode(list(request.query_params.items()), doseq=True)
        separator = "&" if "?" in request.url else "?"
        return f"{request.url}{separator}{query_string}"

    @staticmethod
    def _decode_payload(payload_bytes: bytes) -> RequestResponse:
        if not payload_bytes:
            return {}
        try:
            decoded = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RequestSchemaError.non_object_json_payload() from error
        if isinstance(decoded, Mapping):
            return cast(RequestPayload, decoded)
        if isinstance(decoded, list):
            if not all(isinstance(item, Mapping) for item in decoded):
                raise RequestSchemaError.non_mapping_json_list()
            return cast(list[RequestPayload], decoded)
        raise RequestSchemaError.non_object_json_payload()


def resolve_request_value(
    payload: RequestPayload | object, path: tuple[str, ...]
) -> object:
    """Resolve a field path from a mapping/object payload.

    Mapping keys take precedence for mapping values; otherwise object
    attributes are read. Empty paths raise `ValueError`, non-string path parts
    raise `TypeError`, and missing parts raise `KeyError`.
    """

    if not path:
        raise ValueError(_RESOLVE_REQUEST_EMPTY_PATH_ERROR)
    if any(not isinstance(part, str) for part in path):
        raise TypeError(_RESOLVE_REQUEST_PATH_TYPE_ERROR)

    current: object = payload
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
    """Extract the lookup suffix from a request filter key.

    Bare field names and unknown suffixes resolve to `"exact"`; call
    `validate_filter_key()` when unknown suffixes should raise.
    """

    parts = filter_key.split("__")
    if parts and parts[-1] in SUPPORTED_REQUEST_LOOKUPS:
        return parts[-1]
    return "exact"


def resolve_payload_value(
    payload: RequestPayload,
    source: str | tuple[str, ...] | None,
    field_name: str,
) -> object:
    """Resolve a configured payload source path for a request attribute.

    `source=None` reads `field_name`; dotted strings and tuples are treated as
    nested paths. Missing values raise `MissingRequestPayloadFieldError`.
    """

    path = (field_name,) if source is None else source
    resolved_path = tuple(path.split(".")) if isinstance(path, str) else path
    try:
        return resolve_request_value(payload, resolved_path)
    except KeyError as error:
        raise MissingRequestPayloadFieldError(field_name, resolved_path) from error


def apply_request_lookup(
    value_to_check: object,
    lookup: str,
    filter_value: object,
) -> bool:
    """Evaluate a supported request-filter lookup against a candidate value.

    Supported lookups are `exact`, ordering comparisons, string contains,
    `in`, and `isnull`. Type errors during comparison return `False`.
    """

    try:
        if lookup == "exact":
            return value_to_check == filter_value
        comparable = cast(SupportsRequestComparison, value_to_check)
        if lookup == "lt":
            return comparable < filter_value
        if lookup == "lte":
            return comparable <= filter_value
        if lookup == "gt":
            return comparable > filter_value
        if lookup == "gte":
            return comparable >= filter_value
        if lookup == "contains" and isinstance(value_to_check, str):
            return str(filter_value) in value_to_check
        if lookup == "icontains" and isinstance(value_to_check, str):
            return str(filter_value).lower() in value_to_check.lower()
        if (
            lookup == "in"
            and isinstance(filter_value, Iterable)
            and not isinstance(filter_value, (str, bytes))
        ):
            return value_to_check in filter_value
        if lookup == "isnull":
            return (value_to_check is None) is bool(filter_value)
    except TypeError:
        return False
    return False


UnsupportedRequestExcludeError = RequestExcludeNotSupportedError
