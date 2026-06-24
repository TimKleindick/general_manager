from __future__ import annotations

import builtins
import math
from collections.abc import Awaitable
from types import SimpleNamespace
from typing import cast

import pytest
from django.test import override_settings
from graphql.error import GraphQLError

from general_manager.metrics.graphql import (
    GraphQLResolverTimingMiddleware,
    NoopGraphQLMetricsBackend,
    PrometheusGraphQLMetricsBackend,
    _build_field_name,
    build_graphql_middleware,
    extract_error_code,
    get_graphql_metrics_backend,
    normalize_error_code,
    normalize_field_name,
    normalize_operation_name,
    normalize_operation_type,
    resolve_operation_type,
    reset_graphql_metrics_backend_for_tests,
)


def test_operation_name_defaults_to_unknown() -> None:
    assert normalize_operation_name(None) == "unknown"
    assert normalize_operation_name("") == "unknown"


@override_settings(GENERAL_MANAGER_GRAPHQL_METRICS_MAX_OPERATION_LENGTH=4)
def test_operation_name_truncates() -> None:
    assert normalize_operation_name("LongName") == "Long"


@override_settings(
    GENERAL_MANAGER_GRAPHQL_METRICS_OPERATION_ALLOWLIST=["Allowed", "Allowed_2"]
)
def test_operation_name_allowlist() -> None:
    assert normalize_operation_name("Allowed") == "Allowed"
    assert normalize_operation_name("Allowed 2") == "Allowed_2"
    assert normalize_operation_name("NotAllowed") == "unknown"


@override_settings(
    GENERAL_MANAGER_GRAPHQL_METRICS_OPERATION_ALLOWLIST=["Allowed"],
    GENERAL_MANAGER_GRAPHQL_METRICS_UNKNOWN_OPERATION_POLICY="hash",
)
def test_operation_name_hash_policy() -> None:
    hashed = normalize_operation_name("NotAllowed")
    assert hashed.startswith("op_")
    assert hashed != "unknown"


def test_operation_name_hash_policy_respects_max_length() -> None:
    with override_settings(
        GENERAL_MANAGER_GRAPHQL_METRICS_OPERATION_ALLOWLIST=["Allowed"],
        GENERAL_MANAGER_GRAPHQL_METRICS_UNKNOWN_OPERATION_POLICY="hash",
        GENERAL_MANAGER_GRAPHQL_METRICS_MAX_OPERATION_LENGTH=6,
    ):
        assert len(normalize_operation_name("NotAllowed")) == 6


@pytest.mark.parametrize("value", [0, -1, False, "bad"])
def test_operation_name_invalid_max_length_settings_use_default(value: object) -> None:
    with override_settings(GENERAL_MANAGER_GRAPHQL_METRICS_MAX_OPERATION_LENGTH=value):
        assert normalize_operation_name("LongName") == "LongName"


@pytest.mark.parametrize("value", [0, -1, False, "bad"])
def test_field_name_invalid_max_length_settings_use_default(value: object) -> None:
    with override_settings(GENERAL_MANAGER_GRAPHQL_METRICS_MAX_LABEL_LENGTH=value):
        assert normalize_field_name("Query.long field name") == "Query.long_field_name"


def test_operation_name_normalization_strips_and_ascii_normalizes() -> None:
    assert normalize_operation_name("  Müller Query!! ") == "Muller_Query"
    assert normalize_operation_name("!!!") == "unknown"


def test_operation_type_resolution() -> None:
    query = "query MetricsQuery { __typename }"
    assert resolve_operation_type(query, "MetricsQuery") == "query"


def test_operation_type_resolution_ambiguous_document_returns_unknown() -> None:
    query = "query A { __typename } mutation B { __typename }"
    assert resolve_operation_type(query, None) == "unknown"


def test_normalize_operation_type_accepts_nonstandard_values() -> None:
    assert normalize_operation_type("live query") == "live_query"


def test_error_code_normalization() -> None:
    assert normalize_error_code("BAD_USER_INPUT") == "BAD_USER_INPUT"
    assert normalize_error_code(None) == "unknown"


def test_field_name_normalization() -> None:
    assert normalize_field_name("Query.my field") == "Query.my_field"


class RecordingResolverBackend:
    def __init__(self) -> None:
        self.durations: list[tuple[str, float]] = []
        self.errors: list[str] = []

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
        self.durations.append((field_name, duration))

    def record_resolver_error(self, *, field_name: str) -> None:
        self.errors.append(field_name)


class RecordingPrometheusMetric:
    def __init__(self) -> None:
        self.inc_count = 0
        self.observations: list[float] = []
        self.label_calls: list[dict[str, str]] = []

    def labels(self, **label_values: str) -> RecordingPrometheusMetric:
        self.label_calls.append(label_values)
        return self

    def inc(self) -> None:
        self.inc_count += 1

    def observe(self, value: float) -> None:
        self.observations.append(value)


def test_build_graphql_middleware_returns_existing_list_when_disabled(
    settings,
    monkeypatch,
) -> None:
    settings.GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED = False
    from graphene_django.settings import graphene_settings

    existing = object()
    monkeypatch.setattr(graphene_settings, "MIDDLEWARE", [existing])
    assert build_graphql_middleware() == [existing]


def test_build_graphql_middleware_adds_resolver_timing(settings, monkeypatch) -> None:
    settings.GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED = True
    settings.GENERAL_MANAGER_GRAPHQL_METRICS_RESOLVER_TIMING = True
    from graphene_django.settings import graphene_settings

    monkeypatch.setattr(graphene_settings, "MIDDLEWARE", [])
    middleware = build_graphql_middleware()
    assert any(
        isinstance(entry, GraphQLResolverTimingMiddleware) for entry in middleware
    )


def test_build_graphql_middleware_deduplicates(settings, monkeypatch):
    settings.GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED = True
    settings.GENERAL_MANAGER_GRAPHQL_METRICS_RESOLVER_TIMING = True
    from graphene_django.settings import graphene_settings

    existing = GraphQLResolverTimingMiddleware()
    monkeypatch.setattr(graphene_settings, "MIDDLEWARE", [existing])
    middleware = build_graphql_middleware()
    assert middleware == [existing]


def test_get_graphql_metrics_backend_falls_back_when_prometheus_missing(
    settings,
    monkeypatch,
) -> None:
    settings.GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED = True
    settings.GENERAL_MANAGER_GRAPHQL_METRICS_BACKEND = "prometheus"
    reset_graphql_metrics_backend_for_tests()
    original_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "prometheus_client":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    backend = get_graphql_metrics_backend()
    try:
        assert isinstance(backend, NoopGraphQLMetricsBackend)
    finally:
        reset_graphql_metrics_backend_for_tests()


def test_get_graphql_metrics_backend_unknown_backend_is_noop(settings) -> None:
    settings.GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED = True
    settings.GENERAL_MANAGER_GRAPHQL_METRICS_BACKEND = "custom"
    reset_graphql_metrics_backend_for_tests()
    try:
        assert isinstance(get_graphql_metrics_backend(), NoopGraphQLMetricsBackend)
    finally:
        reset_graphql_metrics_backend_for_tests()


def test_get_graphql_metrics_backend_noop_backend_is_noop(settings) -> None:
    settings.GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED = True
    settings.GENERAL_MANAGER_GRAPHQL_METRICS_BACKEND = "noop"
    reset_graphql_metrics_backend_for_tests()
    try:
        assert isinstance(get_graphql_metrics_backend(), NoopGraphQLMetricsBackend)
    finally:
        reset_graphql_metrics_backend_for_tests()


def test_prometheus_backend_clamps_negative_and_nonfinite_durations(
    monkeypatch,
) -> None:
    request_counter = RecordingPrometheusMetric()
    request_duration = RecordingPrometheusMetric()
    resolver_duration = RecordingPrometheusMetric()
    monkeypatch.setattr(PrometheusGraphQLMetricsBackend, "_initialized", True)
    monkeypatch.setattr(
        PrometheusGraphQLMetricsBackend,
        "_request_counter",
        request_counter,
        raising=False,
    )
    monkeypatch.setattr(
        PrometheusGraphQLMetricsBackend,
        "_request_duration",
        request_duration,
        raising=False,
    )
    monkeypatch.setattr(
        PrometheusGraphQLMetricsBackend,
        "_resolver_duration",
        resolver_duration,
        raising=False,
    )

    backend = PrometheusGraphQLMetricsBackend()
    backend.record_request(
        duration=-1.0,
        operation_name="op",
        operation_type="query",
        status="success",
    )
    backend.record_resolver_duration(field_name="Query.ping", duration=math.nan)
    backend.record_resolver_duration(field_name="Query.pong", duration=math.inf)

    assert request_counter.inc_count == 1
    assert request_duration.observations == [0.0]
    assert resolver_duration.observations == [0.0, 0.0]


def test_resolve_operation_type_invalid_query() -> None:
    assert resolve_operation_type("query {", None) == "unknown"


def test_extract_error_code_from_graphql_error() -> None:
    error = GraphQLError("bad", extensions={"code": "BAD"})
    assert extract_error_code(error) == "BAD"


def test_extract_error_code_does_not_unwrap_original_error() -> None:
    error = RuntimeError("bad")
    assert extract_error_code(error) == "unknown"


def test_build_field_name_variants() -> None:
    info = SimpleNamespace(
        parent_type=SimpleNamespace(name="Query"),
        field_name="status",
    )
    assert _build_field_name(info) == "Query.status"

    info_no_parent = SimpleNamespace(parent_type=None, field_name="status")
    assert _build_field_name(info_no_parent) == "status"

    info_missing = SimpleNamespace(parent_type=None, field_name=None)
    assert _build_field_name(info_missing) == "unknown"


def test_resolver_timing_middleware_records_sync_success(monkeypatch) -> None:
    backend = RecordingResolverBackend()
    middleware = GraphQLResolverTimingMiddleware()
    info = SimpleNamespace(parent_type=SimpleNamespace(name="Query"), field_name="ping")
    monkeypatch.setattr(
        "general_manager.metrics.graphql.get_graphql_metrics_backend",
        lambda: backend,
    )

    result = middleware.resolve(lambda _root, _info: "pong", None, info)

    assert result == "pong"
    assert backend.errors == []
    assert len(backend.durations) == 1
    assert backend.durations[0][0] == "Query.ping"


def test_resolver_timing_middleware_records_and_reraises_sync_error(
    monkeypatch,
) -> None:
    backend = RecordingResolverBackend()
    middleware = GraphQLResolverTimingMiddleware()
    info = SimpleNamespace(parent_type=SimpleNamespace(name="Query"), field_name="ping")
    monkeypatch.setattr(
        "general_manager.metrics.graphql.get_graphql_metrics_backend",
        lambda: backend,
    )

    def raise_error(_root: object, _info: object) -> object:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        middleware.resolve(raise_error, None, info)

    assert backend.errors == ["Query.ping"]
    assert len(backend.durations) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_resolver_timing_middleware_records_async_success(
    monkeypatch,
    anyio_backend: str,
) -> None:
    del anyio_backend
    backend = RecordingResolverBackend()
    middleware = GraphQLResolverTimingMiddleware()
    info = SimpleNamespace(parent_type=SimpleNamespace(name="Query"), field_name="ping")
    monkeypatch.setattr(
        "general_manager.metrics.graphql.get_graphql_metrics_backend",
        lambda: backend,
    )

    async def resolve_async(_root: object, _info: object) -> str:
        return "pong"

    result = middleware.resolve(resolve_async, None, info)
    assert await cast(Awaitable[object], result) == "pong"
    assert backend.errors == []
    assert len(backend.durations) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_resolver_timing_middleware_records_and_reraises_async_error(
    monkeypatch,
    anyio_backend: str,
) -> None:
    del anyio_backend
    backend = RecordingResolverBackend()
    middleware = GraphQLResolverTimingMiddleware()
    info = SimpleNamespace(parent_type=SimpleNamespace(name="Query"), field_name="ping")
    monkeypatch.setattr(
        "general_manager.metrics.graphql.get_graphql_metrics_backend",
        lambda: backend,
    )

    async def raise_async_error(_root: object, _info: object) -> object:
        raise RuntimeError("boom")

    result = middleware.resolve(raise_async_error, None, info)
    with pytest.raises(RuntimeError):
        await cast(Awaitable[object], result)

    assert backend.errors == ["Query.ping"]
    assert len(backend.durations) == 1
