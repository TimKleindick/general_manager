"""GraphQL metrics helpers for GeneralManager."""

from __future__ import annotations

import inspect
import math
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import (
    ClassVar,
    Literal,
    Protocol,
    SupportsIndex,
    SupportsInt,
    TypeAlias,
    cast,
)

from graphql import get_operation_ast, parse
from graphql.error import GraphQLError
from text_unidecode import unidecode

from general_manager.logging import get_logger

logger = get_logger("metrics.graphql")

UNKNOWN_LABEL: Literal["unknown"] = "unknown"
DEFAULT_MAX_OPERATION_LENGTH = 64
DEFAULT_MAX_LABEL_LENGTH = 128
DEFAULT_UNKNOWN_OPERATION_POLICY = "unknown"
HASH_PREFIX = "op_"
GraphQLOperationType = Literal["query", "mutation", "subscription", "unknown"]
GraphQLRequestStatus = Literal["success", "error"]
GraphQLMetricsBackendName = Literal["prometheus", "noop"]
GraphQLUnknownOperationPolicy = Literal["unknown", "hash"]
IntCoercible: TypeAlias = str | bytes | bytearray | SupportsInt | SupportsIndex


class GraphQLMetricsBackend(Protocol):
    """Backend contract for request and resolver-level GraphQL metrics."""

    def record_request(
        self,
        *,
        duration: float,
        operation_name: str,
        operation_type: GraphQLOperationType,
        status: GraphQLRequestStatus,
    ) -> None:
        """
        Record one GraphQL request.

        Inputs are expected to be normalized labels. `operation_type` is
        `query`, `mutation`, `subscription`, or `unknown`; `status` is
        `success` or `error`.
        """
        ...

    def record_error(self, *, operation_name: str, code: str) -> None:
        """Record one GraphQL error for a normalized operation/code label."""
        ...

    def record_resolver_duration(self, *, field_name: str, duration: float) -> None:
        """Record one resolver duration observation."""
        ...

    def record_resolver_error(self, *, field_name: str) -> None:
        """Record one resolver exception for a normalized field label."""
        ...


@dataclass(frozen=True)
class GraphQLMetricsSettings:
    """Resolved GraphQL metrics settings used by the instrumentation helpers."""

    enabled: bool
    backend: GraphQLMetricsBackendName | str
    resolver_timing: bool
    operation_allowlist: set[str] | None
    max_operation_length: int
    max_label_length: int
    unknown_operation_policy: GraphQLUnknownOperationPolicy | str


class NoopGraphQLMetricsBackend:
    """Metrics backend that intentionally drops every metric call."""

    def record_request(
        self,
        *,
        duration: float,
        operation_name: str,
        operation_type: GraphQLOperationType,
        status: GraphQLRequestStatus,
    ) -> None:
        """Drop one request metric."""
        return None

    def record_error(self, *, operation_name: str, code: str) -> None:
        """Drop one error metric."""
        return None

    def record_resolver_duration(self, *, field_name: str, duration: float) -> None:
        """Drop one resolver duration metric."""
        return None

    def record_resolver_error(self, *, field_name: str) -> None:
        """Drop one resolver error metric."""
        return None


class _PrometheusMetric(Protocol):
    """Subset of the Prometheus metric API used by this module."""

    def labels(self, **label_values: str) -> _PrometheusMetric: ...

    def inc(self) -> None: ...

    def observe(self, value: float) -> None: ...


class _PrometheusCollectorFactory(Protocol):
    """Callable shape shared by Prometheus Counter and Histogram factories."""

    def __call__(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
    ) -> _PrometheusMetric: ...


class _PrometheusCollectorRegistry(Protocol):
    """Collector registry attributes used to reuse already-registered metrics."""

    _names_to_collectors: Mapping[str, _PrometheusMetric]


class PrometheusGraphQLMetricsBackend:
    """
    Prometheus backend that records GraphQL request and resolver metrics.

    Collectors are registered in the default Prometheus registry on first
    construction and reused by later instances in the same process. Recording
    methods clamp negative, `NaN`, and infinite durations to `0.0`, but
    otherwise pass labels through as supplied. Prometheus registration and
    recording exceptions are not caught by this backend; callers that need
    isolation should catch them at the call site.
    """

    _initialized = False
    _request_counter: ClassVar[_PrometheusMetric]
    _request_duration: ClassVar[_PrometheusMetric]
    _error_counter: ClassVar[_PrometheusMetric]
    _resolver_duration: ClassVar[_PrometheusMetric]
    _resolver_error: ClassVar[_PrometheusMetric]

    def __init__(self) -> None:
        self._ensure_metrics()

    @classmethod
    def _ensure_metrics(cls) -> None:
        if cls._initialized:
            return

        from prometheus_client import (
            Counter,
            Histogram,
            REGISTRY,
        )

        def _get_or_create(
            collector_cls: _PrometheusCollectorFactory,
            name: str,
            desc: str,
            labels: Sequence[str],
        ) -> _PrometheusMetric:
            registry = cast(_PrometheusCollectorRegistry, REGISTRY)
            existing = registry._names_to_collectors.get(name)
            if existing is not None:
                return existing
            return collector_cls(name, desc, labels)

        cls._request_counter = _get_or_create(
            cast(_PrometheusCollectorFactory, Counter),
            "graphql_requests_total",
            "Total GraphQL requests.",
            ["operation_name", "operation_type", "status"],
        )
        cls._request_duration = _get_or_create(
            cast(_PrometheusCollectorFactory, Histogram),
            "graphql_request_duration_seconds",
            "GraphQL request duration in seconds.",
            ["operation_name", "operation_type"],
        )
        cls._error_counter = _get_or_create(
            cast(_PrometheusCollectorFactory, Counter),
            "graphql_errors_total",
            "Total GraphQL errors.",
            ["operation_name", "code"],
        )
        cls._resolver_duration = _get_or_create(
            cast(_PrometheusCollectorFactory, Histogram),
            "graphql_resolver_duration_seconds",
            "GraphQL resolver duration in seconds.",
            ["field_name"],
        )
        cls._resolver_error = _get_or_create(
            cast(_PrometheusCollectorFactory, Counter),
            "graphql_resolver_errors_total",
            "Total GraphQL resolver errors.",
            ["field_name"],
        )
        cls._initialized = True

    def record_request(
        self,
        *,
        duration: float,
        operation_name: str,
        operation_type: GraphQLOperationType,
        status: GraphQLRequestStatus,
    ) -> None:
        """Increment request count and observe non-negative request duration."""
        self._request_counter.labels(
            operation_name=operation_name,
            operation_type=operation_type,
            status=status,
        ).inc()
        self._request_duration.labels(
            operation_name=operation_name,
            operation_type=operation_type,
        ).observe(_safe_duration(duration))

    def record_error(self, *, operation_name: str, code: str) -> None:
        """Increment the GraphQL error counter for a normalized code label."""
        self._error_counter.labels(operation_name=operation_name, code=code).inc()

    def record_resolver_duration(self, *, field_name: str, duration: float) -> None:
        """Observe non-negative resolver duration for a normalized field label."""
        self._resolver_duration.labels(field_name=field_name).observe(
            _safe_duration(duration)
        )

    def record_resolver_error(self, *, field_name: str) -> None:
        """Increment the resolver error counter for a normalized field label."""
        self._resolver_error.labels(field_name=field_name).inc()


_metrics_backend: GraphQLMetricsBackend | None = None


def reset_graphql_metrics_backend_for_tests() -> None:
    """Clear the cached metrics backend for settings-isolated tests."""
    global _metrics_backend
    _metrics_backend = None


def _get_settings() -> GraphQLMetricsSettings:
    from general_manager.conf import get_setting

    enabled = bool(get_setting("GRAPHQL_METRICS_ENABLED", False))
    backend = str(get_setting("GRAPHQL_METRICS_BACKEND", "prometheus"))
    resolver_timing = bool(get_setting("GRAPHQL_METRICS_RESOLVER_TIMING", False))
    allowlist_raw = get_setting("GRAPHQL_METRICS_OPERATION_ALLOWLIST", None)
    max_operation_length = _positive_int_setting(
        get_setting(
            "GRAPHQL_METRICS_MAX_OPERATION_LENGTH", DEFAULT_MAX_OPERATION_LENGTH
        ),
        DEFAULT_MAX_OPERATION_LENGTH,
    )
    max_label_length = _positive_int_setting(
        get_setting("GRAPHQL_METRICS_MAX_LABEL_LENGTH", DEFAULT_MAX_LABEL_LENGTH),
        DEFAULT_MAX_LABEL_LENGTH,
    )
    unknown_policy = str(
        get_setting(
            "GRAPHQL_METRICS_UNKNOWN_OPERATION_POLICY",
            DEFAULT_UNKNOWN_OPERATION_POLICY,
        )
    ).lower()
    allowlist = _normalize_allowlist(allowlist_raw, max_operation_length)
    return GraphQLMetricsSettings(
        enabled=enabled,
        backend=backend,
        resolver_timing=resolver_timing,
        operation_allowlist=allowlist,
        max_operation_length=max_operation_length,
        max_label_length=max_label_length,
        unknown_operation_policy=unknown_policy,
    )


def _positive_int_setting(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(cast(IntCoercible, value))
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def graphql_metrics_enabled() -> bool:
    """Return whether request-level GraphQL metrics are enabled in settings."""
    return _get_settings().enabled


def graphql_metrics_resolver_timing_enabled() -> bool:
    """Return whether resolver timing metrics should be attached."""
    config = _get_settings()
    return config.enabled and config.resolver_timing


def get_graphql_metrics_backend() -> GraphQLMetricsBackend:
    """
    Return the cached configured GraphQL metrics backend.

    Disabled metrics, `GRAPHQL_METRICS_BACKEND="noop"`, and unknown backend
    names use `NoopGraphQLMetricsBackend`. Unknown names log a warning; `noop`
    does not.
    The Prometheus backend is created only when metrics are enabled and
    `GRAPHQL_METRICS_BACKEND` resolves to `prometheus`; if `prometheus-client`
    is unavailable, the helper logs a warning and falls back to the no-op
    backend. Backend construction errors other than import failure propagate.
    """
    global _metrics_backend
    if _metrics_backend is not None:
        return _metrics_backend

    config = _get_settings()
    if not config.enabled:
        _metrics_backend = NoopGraphQLMetricsBackend()
        return _metrics_backend

    if config.backend.lower() == "noop":
        _metrics_backend = NoopGraphQLMetricsBackend()
        return _metrics_backend

    if config.backend.lower() == "prometheus":
        try:
            import prometheus_client  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dependency
            logger.warning(
                "prometheus metrics backend unavailable",
                context={"error": type(exc).__name__, "message": str(exc)},
            )
            _metrics_backend = NoopGraphQLMetricsBackend()
            return _metrics_backend
        _metrics_backend = PrometheusGraphQLMetricsBackend()
        return _metrics_backend

    logger.warning(
        "unknown graphql metrics backend; falling back to noop",
        context={"backend": config.backend},
    )
    _metrics_backend = NoopGraphQLMetricsBackend()
    return _metrics_backend


def build_graphql_middleware() -> list[object]:
    """
    Return Graphene middleware with resolver timing attached when enabled.

    The helper copies `graphene_settings.MIDDLEWARE` into a new list. If metrics
    or resolver timing are disabled, the copied list is returned unchanged. An
    existing `GraphQLResolverTimingMiddleware` class or instance is not added a
    second time.
    """
    from graphene_django.settings import graphene_settings

    middleware = list(cast(Iterable[object], graphene_settings.MIDDLEWARE or []))
    if graphql_metrics_resolver_timing_enabled() and not any(
        entry is GraphQLResolverTimingMiddleware
        or isinstance(entry, GraphQLResolverTimingMiddleware)
        for entry in middleware
    ):
        middleware.append(GraphQLResolverTimingMiddleware())
    return middleware


def normalize_operation_name(operation_name: str | None) -> str:
    """
    Normalize a GraphQL operation name for use as a metric label.

    `None`, empty, and punctuation-only values become `unknown`. Values are
    transliterated to ASCII, stripped, sanitized to `[0-9A-Za-z_.-]`, and
    truncated to `GRAPHQL_METRICS_MAX_OPERATION_LENGTH` when positive.
    Allowlist checks run after normalization. With hash policy enabled,
    non-allowlisted raw operation names become `op_<sha256-prefix>`, where the
    SHA-256 hex digest prefix is eight characters before the `op_` prefix is
    truncated by a positive `GRAPHQL_METRICS_MAX_OPERATION_LENGTH`. Unrecognized
    operation policies behave like `unknown`.
    """
    config = _get_settings()
    normalized = _normalize_label_value(operation_name, config.max_operation_length)
    if config.operation_allowlist is None:
        return normalized
    if normalized in config.operation_allowlist:
        return normalized
    if (
        config.unknown_operation_policy == "hash"
        and operation_name
        and normalized != UNKNOWN_LABEL
    ):
        return _hash_operation_name(operation_name, config.max_operation_length)
    return UNKNOWN_LABEL


def normalize_operation_type(operation_type: str | None) -> str:
    """
    Normalize a GraphQL operation type for use as a metric label.

    `query`, `mutation`, and `subscription` are the expected GraphQL values, but
    other non-empty strings are sanitized instead of rejected. Missing values
    become `unknown`.
    """
    if not operation_type:
        return UNKNOWN_LABEL
    normalized = _normalize_label_value(operation_type, DEFAULT_MAX_OPERATION_LENGTH)
    return normalized or UNKNOWN_LABEL


def normalize_error_code(code: object) -> str:
    """
    Normalize a GraphQL error code value for use as a metric label.

    Empty and punctuation-only values become `unknown`. Values use the same
    ASCII/sanitize rules as operation names and are truncated to 64 characters.
    """
    if code is None:
        return UNKNOWN_LABEL
    normalized = _normalize_label_value(str(code), DEFAULT_MAX_OPERATION_LENGTH)
    return normalized or UNKNOWN_LABEL


def normalize_field_name(field_name: str | None) -> str:
    """
    Normalize a GraphQL resolver field name using the field-label length limit.

    Empty and punctuation-only values become `unknown`. Values use the same
    ASCII/sanitize rules as operation names and are truncated to
    `GRAPHQL_METRICS_MAX_LABEL_LENGTH` when positive.
    """
    return _normalize_label_value(field_name, _get_settings().max_label_length)


def resolve_operation_type(
    query: str | None,
    operation_name: str | None,
) -> GraphQLOperationType:
    """
    Parse a GraphQL document and return the selected operation type label.

    Invalid, missing, or ambiguous documents return `unknown` instead of raising
    parser errors. Unexpected runtime errors from GraphQL parsing helpers are
    not caught.
    """
    if not query:
        return UNKNOWN_LABEL
    try:
        document = parse(query)
        operation_ast = get_operation_ast(document, operation_name)
    except (GraphQLError, TypeError, ValueError):
        return UNKNOWN_LABEL
    if operation_ast is None or not getattr(operation_ast, "operation", None):
        return UNKNOWN_LABEL
    operation_type = str(operation_ast.operation.value)
    if operation_type == "query":
        return "query"
    if operation_type == "mutation":
        return "mutation"
    if operation_type == "subscription":
        return "subscription"
    return UNKNOWN_LABEL


def extract_error_code(error: Exception) -> str:
    """
    Return a normalized GraphQL error extension code, or `unknown`.

    Only direct `GraphQLError` instances are inspected. Wrapped/original
    exceptions are not unwrapped.
    """
    if isinstance(error, GraphQLError):
        extensions = error.extensions
        if isinstance(extensions, dict):
            return normalize_error_code(extensions.get("code"))
    return UNKNOWN_LABEL


def _normalize_allowlist(allowlist: object, max_length: int) -> set[str] | None:
    if allowlist is None:
        return None
    if isinstance(allowlist, str):
        names: Iterable[object] = (allowlist,)
    else:
        names = cast(Iterable[object], allowlist)
    normalized: set[str] = set()
    for name in names:
        label = _normalize_label_value(name, max_length)
        if label != UNKNOWN_LABEL:
            normalized.add(label)
    return normalized


def _normalize_label_value(value: object, max_length: int) -> str:
    if value is None:
        return UNKNOWN_LABEL
    label = unidecode(str(value)).strip()
    if not label:
        return UNKNOWN_LABEL
    label = re.sub(r"[^0-9A-Za-z_.-]+", "_", label)
    label = label.strip("._-")
    if not label:
        return UNKNOWN_LABEL
    if max_length > 0:
        label = label[:max_length]
    return label or UNKNOWN_LABEL


def _safe_duration(duration: float) -> float:
    if not math.isfinite(duration) or duration < 0.0:
        return 0.0
    return duration


def _hash_operation_name(raw_name: str, max_length: int) -> str:
    import hashlib

    digest = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()[:8]
    label = f"{HASH_PREFIX}{digest}"
    if max_length > 0:
        label = label[:max_length]
    return label or UNKNOWN_LABEL


class GraphQLResolverTimingMiddleware:
    """Graphene middleware that records resolver duration and error metrics."""

    def resolve(
        self,
        next_: Callable[..., object],
        root: object,
        info: object,
        **args: object,
    ) -> object:
        """
        Resolve one field and record duration/error metrics.

        The middleware supports synchronous return values and awaitables. Metric
        backend exceptions are caught and logged at debug level so resolver
        execution is not changed by metrics failures. Exceptions from the
        resolver itself are recorded and then re-raised.
        """
        backend = get_graphql_metrics_backend()
        field_name = _build_field_name(info)
        start = time.perf_counter()
        try:
            result = next_(root, info, **args)
        except Exception:
            duration = time.perf_counter() - start
            _safe_record_resolver_error(backend, field_name)
            _safe_record_resolver_duration(backend, field_name, duration)
            raise

        if inspect.isawaitable(result):
            return _wrap_async_result(
                backend,
                field_name,
                start,
                cast(Awaitable[object], result),
            )

        duration = time.perf_counter() - start
        _safe_record_resolver_duration(backend, field_name, duration)
        return result


async def _wrap_async_result(
    backend: GraphQLMetricsBackend,
    field_name: str,
    start: float,
    result: Awaitable[object],
) -> object:
    try:
        return await result
    except Exception:
        _safe_record_resolver_error(backend, field_name)
        raise
    finally:
        duration = time.perf_counter() - start
        _safe_record_resolver_duration(backend, field_name, duration)


def _build_field_name(info: object) -> str:
    parent = getattr(info, "parent_type", None)
    parent_name = getattr(parent, "name", None)
    field = getattr(info, "field_name", None)
    if parent_name and field:
        return normalize_field_name(f"{parent_name}.{field}")
    if field:
        return normalize_field_name(str(field))
    return UNKNOWN_LABEL


def _safe_record_resolver_duration(
    backend: GraphQLMetricsBackend, field_name: str, duration: float
) -> None:
    try:
        backend.record_resolver_duration(field_name=field_name, duration=duration)
    except Exception as exc:  # pragma: no cover - safety net  # noqa: BLE001
        logger.debug(
            "resolver duration metric failed",
            context={"error": type(exc).__name__, "message": str(exc)},
        )


def _safe_record_resolver_error(
    backend: GraphQLMetricsBackend, field_name: str
) -> None:
    try:
        backend.record_resolver_error(field_name=field_name)
    except Exception as exc:  # pragma: no cover - safety net  # noqa: BLE001
        logger.debug(
            "resolver error metric failed",
            context={"error": type(exc).__name__, "message": str(exc)},
        )
