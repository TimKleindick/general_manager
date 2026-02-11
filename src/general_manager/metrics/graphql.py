"""GraphQL metrics helpers for GeneralManager."""

from __future__ import annotations

import inspect
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from django.conf import settings
from graphql import get_operation_ast, parse
from graphql.error import GraphQLError
from text_unidecode import unidecode

from general_manager.logging import get_logger

logger = get_logger("metrics.graphql")

UNKNOWN_LABEL = "unknown"
DEFAULT_MAX_OPERATION_LENGTH = 64
DEFAULT_MAX_LABEL_LENGTH = 128
DEFAULT_UNKNOWN_OPERATION_POLICY = "unknown"
HASH_PREFIX = "op_"


class GraphQLMetricsBackend(Protocol):
    def record_request(
        self,
        *,
        duration: float,
        operation_name: str,
        operation_type: str,
        status: str,
    ) -> None: ...

    def record_error(self, *, operation_name: str, code: str) -> None: ...

    def record_resolver_duration(self, *, field_name: str, duration: float) -> None: ...

    def record_resolver_error(self, *, field_name: str) -> None: ...


@dataclass(frozen=True)
class GraphQLMetricsSettings:
    enabled: bool
    backend: str
    resolver_timing: bool
    operation_allowlist: set[str] | None
    max_operation_length: int
    max_label_length: int
    unknown_operation_policy: str


class NoopGraphQLMetricsBackend:
    def record_request(
        self,
        *,
        duration: float,
        operation_name: str,
        operation_type: str,
        status: str,
    ) -> None:
        return None

    def record_error(self, *, operation_name: str, code: str) -> None:
        return None

    def record_resolver_duration(self, *, field_name: str, duration: float) -> None:
        return None

    def record_resolver_error(self, *, field_name: str) -> None:
        return None


class PrometheusGraphQLMetricsBackend:
    _initialized = False
    _request_counter: Any
    _request_duration: Any
    _error_counter: Any
    _resolver_duration: Any
    _resolver_error: Any

    def __init__(self) -> None:
        self._ensure_metrics()

    @classmethod
    def _ensure_metrics(cls) -> None:
        if cls._initialized:
            return

        from prometheus_client import Counter, Histogram, REGISTRY

        def _get_or_create(
            collector_cls: type, name: str, desc: str, labels: list[str]
        ):
            existing = REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]
            if existing is not None:
                return existing
            return collector_cls(name, desc, labels)

        cls._request_counter = _get_or_create(
            Counter,
            "graphql_requests_total",
            "Total GraphQL requests.",
            ["operation_name", "operation_type", "status"],
        )
        cls._request_duration = _get_or_create(
            Histogram,
            "graphql_request_duration_seconds",
            "GraphQL request duration in seconds.",
            ["operation_name", "operation_type"],
        )
        cls._error_counter = _get_or_create(
            Counter,
            "graphql_errors_total",
            "Total GraphQL errors.",
            ["operation_name", "code"],
        )
        cls._resolver_duration = _get_or_create(
            Histogram,
            "graphql_resolver_duration_seconds",
            "GraphQL resolver duration in seconds.",
            ["field_name"],
        )
        cls._resolver_error = _get_or_create(
            Counter,
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
        operation_type: str,
        status: str,
    ) -> None:
        self._request_counter.labels(
            operation_name=operation_name,
            operation_type=operation_type,
            status=status,
        ).inc()
        self._request_duration.labels(
            operation_name=operation_name,
            operation_type=operation_type,
        ).observe(max(duration, 0.0))

    def record_error(self, *, operation_name: str, code: str) -> None:
        self._error_counter.labels(operation_name=operation_name, code=code).inc()

    def record_resolver_duration(self, *, field_name: str, duration: float) -> None:
        self._resolver_duration.labels(field_name=field_name).observe(
            max(duration, 0.0)
        )

    def record_resolver_error(self, *, field_name: str) -> None:
        self._resolver_error.labels(field_name=field_name).inc()


_metrics_backend: GraphQLMetricsBackend | None = None


def reset_graphql_metrics_backend_for_tests() -> None:
    global _metrics_backend
    _metrics_backend = None


def _get_settings() -> GraphQLMetricsSettings:
    enabled = bool(getattr(settings, "GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED", False))
    backend = str(
        getattr(settings, "GENERAL_MANAGER_GRAPHQL_METRICS_BACKEND", "prometheus")
    )
    resolver_timing = bool(
        getattr(settings, "GENERAL_MANAGER_GRAPHQL_METRICS_RESOLVER_TIMING", False)
    )
    allowlist_raw = getattr(
        settings, "GENERAL_MANAGER_GRAPHQL_METRICS_OPERATION_ALLOWLIST", None
    )
    max_operation_length = int(
        getattr(
            settings,
            "GENERAL_MANAGER_GRAPHQL_METRICS_MAX_OPERATION_LENGTH",
            DEFAULT_MAX_OPERATION_LENGTH,
        )
    )
    max_label_length = int(
        getattr(
            settings,
            "GENERAL_MANAGER_GRAPHQL_METRICS_MAX_LABEL_LENGTH",
            DEFAULT_MAX_LABEL_LENGTH,
        )
    )
    unknown_policy = str(
        getattr(
            settings,
            "GENERAL_MANAGER_GRAPHQL_METRICS_UNKNOWN_OPERATION_POLICY",
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


def graphql_metrics_enabled() -> bool:
    return _get_settings().enabled


def graphql_metrics_resolver_timing_enabled() -> bool:
    config = _get_settings()
    return config.enabled and config.resolver_timing


def get_graphql_metrics_backend() -> GraphQLMetricsBackend:
    global _metrics_backend
    if _metrics_backend is not None:
        return _metrics_backend

    config = _get_settings()
    if not config.enabled:
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


def build_graphql_middleware() -> list[Any] | None:
    from graphene_django.settings import graphene_settings

    middleware: list[Any] = list(graphene_settings.MIDDLEWARE or [])
    if graphql_metrics_resolver_timing_enabled() and not any(
        entry is GraphQLResolverTimingMiddleware
        or isinstance(entry, GraphQLResolverTimingMiddleware)
        for entry in middleware
    ):
        middleware.append(GraphQLResolverTimingMiddleware())
    return middleware


def normalize_operation_name(operation_name: str | None) -> str:
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
    if not operation_type:
        return UNKNOWN_LABEL
    normalized = _normalize_label_value(operation_type, DEFAULT_MAX_OPERATION_LENGTH)
    return normalized or UNKNOWN_LABEL


def normalize_error_code(code: Any) -> str:
    if code is None:
        return UNKNOWN_LABEL
    normalized = _normalize_label_value(str(code), DEFAULT_MAX_OPERATION_LENGTH)
    return normalized or UNKNOWN_LABEL


def normalize_field_name(field_name: str | None) -> str:
    return _normalize_label_value(field_name, _get_settings().max_label_length)


def resolve_operation_type(query: str | None, operation_name: str | None) -> str:
    if not query:
        return UNKNOWN_LABEL
    try:
        document = parse(query)
        operation_ast = get_operation_ast(document, operation_name)
    except (GraphQLError, TypeError, ValueError):
        return UNKNOWN_LABEL
    if operation_ast is None or not getattr(operation_ast, "operation", None):
        return UNKNOWN_LABEL
    return normalize_operation_type(operation_ast.operation.value)


def extract_error_code(error: Exception) -> str:
    if isinstance(error, GraphQLError):
        extensions = error.extensions
        if isinstance(extensions, dict):
            return normalize_error_code(extensions.get("code"))
    return UNKNOWN_LABEL


def _normalize_allowlist(
    allowlist: Iterable[str] | None, max_length: int
) -> set[str] | None:
    if allowlist is None:
        return None
    normalized: set[str] = set()
    for name in allowlist:
        label = _normalize_label_value(name, max_length)
        if label != UNKNOWN_LABEL:
            normalized.add(label)
    return normalized


def _normalize_label_value(value: Any, max_length: int) -> str:
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


def _hash_operation_name(raw_name: str, max_length: int) -> str:
    import hashlib

    digest = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()[:8]
    label = f"{HASH_PREFIX}{digest}"
    if max_length > 0:
        label = label[:max_length]
    return label or UNKNOWN_LABEL


class GraphQLResolverTimingMiddleware:
    def resolve(self, next_, root, info, **args):  # type: ignore[no-untyped-def]
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
            return _wrap_async_result(backend, field_name, start, result)

        duration = time.perf_counter() - start
        _safe_record_resolver_duration(backend, field_name, duration)
        return result


async def _wrap_async_result(
    backend: GraphQLMetricsBackend, field_name: str, start: float, result: Any
) -> Any:
    try:
        return await result
    except Exception:
        _safe_record_resolver_error(backend, field_name)
        raise
    finally:
        duration = time.perf_counter() - start
        _safe_record_resolver_duration(backend, field_name, duration)


def _build_field_name(info: Any) -> str:
    parent = getattr(info, "parent_type", None)
    parent_name = getattr(parent, "name", None)
    field = getattr(info, "field_name", None)
    if parent_name and field:
        return _cached_normalize_field_name(str(parent_name), str(field))
    if field:
        return normalize_field_name(str(field))
    return UNKNOWN_LABEL


@lru_cache(maxsize=1024)
def _cached_normalize_field_name(parent_name: str, field_name: str) -> str:
    return normalize_field_name(f"{parent_name}.{field_name}")


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
