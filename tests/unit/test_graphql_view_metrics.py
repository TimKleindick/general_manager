from __future__ import annotations

import logging
from types import SimpleNamespace

import graphene
import pytest
from graphql import GraphQLError

from general_manager.as_of import HistoricalReadNotSupportedError
from general_manager.api import graphql_view as view_module
from general_manager.api.graphql_view import GeneralManagerGraphQLView
from general_manager.metrics.graphql import GraphQLResolverTimingMiddleware

METRICS_UNAVAILABLE = "metrics unavailable"
EXECUTE_FAILED = "execute failed"


class _FakeBackend:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str]] = []
        self.errors: list[tuple[str, str]] = []

    def record_request(
        self,
        *,
        duration: float,
        operation_name: str,
        operation_type: str,
        status: str,
    ) -> None:
        self.requests.append((operation_name, operation_type, status))

    def record_error(self, *, operation_name: str, code: str) -> None:
        self.errors.append((operation_name, code))


class _FailingBackend(_FakeBackend):
    def record_request(
        self,
        *,
        duration: float,
        operation_name: str,
        operation_type: str,
        status: str,
    ) -> None:
        raise RuntimeError(METRICS_UNAVAILABLE)


def test_graphql_view_get_middleware_disabled_preserves_base_value(monkeypatch) -> None:
    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    existing = object()
    monkeypatch.setattr(
        view_module, "graphql_metrics_resolver_timing_enabled", lambda: False
    )

    def _base_middleware(_self: object, _request: object) -> list[object]:
        return [existing]

    monkeypatch.setattr(view_module.GraphQLView, "get_middleware", _base_middleware)
    assert view.get_middleware(request=object()) == [existing]


def test_graphql_view_get_middleware_adds_resolver_timing(monkeypatch) -> None:
    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    monkeypatch.setattr(
        view_module, "graphql_metrics_resolver_timing_enabled", lambda: True
    )

    def _base_middleware(_self: object, _request: object) -> None:
        return None

    monkeypatch.setattr(view_module.GraphQLView, "get_middleware", _base_middleware)
    middleware = view.get_middleware(request=object())
    assert len(middleware) == 1
    assert isinstance(middleware[0], GraphQLResolverTimingMiddleware)


def test_graphql_view_get_middleware_appends_after_existing(monkeypatch) -> None:
    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    existing = object()
    monkeypatch.setattr(
        view_module, "graphql_metrics_resolver_timing_enabled", lambda: True
    )

    def _base_middleware(_self: object, _request: object) -> list[object]:
        return [existing]

    monkeypatch.setattr(view_module.GraphQLView, "get_middleware", _base_middleware)
    middleware = view.get_middleware(request=object())
    assert middleware is not None
    assert middleware[0] is existing
    assert isinstance(middleware[1], GraphQLResolverTimingMiddleware)


def test_graphql_view_get_middleware_deduplicates(monkeypatch) -> None:
    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    existing = GraphQLResolverTimingMiddleware()
    monkeypatch.setattr(
        view_module, "graphql_metrics_resolver_timing_enabled", lambda: True
    )

    def _base_middleware(_self: object, _request: object) -> list[object]:
        return [existing]

    monkeypatch.setattr(view_module.GraphQLView, "get_middleware", _base_middleware)
    middleware = view.get_middleware(request=object())
    assert middleware == [existing]


def test_graphql_view_get_response_records_metrics(monkeypatch) -> None:
    backend = _FakeBackend()

    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    request = SimpleNamespace()

    monkeypatch.setattr(view, "get_graphql_params", lambda *_: ("query", {}, "Op", "1"))
    error = SimpleNamespace(path=None, extensions={"code": "BAD"})
    execution_result = SimpleNamespace(data=None, errors=[error])
    monkeypatch.setattr(
        view, "execute_graphql_request", lambda *_args, **_kwargs: execution_result
    )
    monkeypatch.setattr(view, "format_error", lambda _err: {"message": "bad"})
    monkeypatch.setattr(view, "json_encode", lambda _req, payload, **_kw: payload)
    monkeypatch.setattr(view_module, "graphql_metrics_enabled", lambda: True)
    monkeypatch.setattr(view_module, "get_graphql_metrics_backend", lambda: backend)
    monkeypatch.setattr(view_module, "normalize_operation_name", lambda _name: "op")
    monkeypatch.setattr(view_module, "resolve_operation_type", lambda *_: "query")
    monkeypatch.setattr(view_module, "extract_error_code", lambda _err: "bad")

    payload, status = view.get_response(request, data={}, show_graphiql=False)

    assert status == 400
    assert payload == {"errors": [{"message": "bad"}]}
    assert backend.requests == [("op", "query", "error")]
    assert backend.errors == [("op", "bad")]


def test_graphql_view_metrics_use_mapped_historical_error_code(monkeypatch) -> None:
    backend = _FakeBackend()

    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    request = SimpleNamespace()
    monkeypatch.setattr(view, "get_graphql_params", lambda *_: ("query", {}, "Op", "1"))
    error = GraphQLError(
        "wrapped",
        path=["ping"],
        original_error=HistoricalReadNotSupportedError("RemoteInterface"),
    )
    execution_result = SimpleNamespace(data={"ping": None}, errors=[error])
    monkeypatch.setattr(
        view, "execute_graphql_request", lambda *_args, **_kwargs: execution_result
    )
    monkeypatch.setattr(view, "json_encode", lambda _req, payload, **_kw: payload)
    monkeypatch.setattr(view_module, "graphql_metrics_enabled", lambda: True)
    monkeypatch.setattr(view_module, "get_graphql_metrics_backend", lambda: backend)
    monkeypatch.setattr(view_module, "normalize_operation_name", lambda _name: "op")
    monkeypatch.setattr(view_module, "resolve_operation_type", lambda *_: "query")

    view.get_response(request, data={}, show_graphiql=False)

    assert backend.errors == [("op", "HISTORICAL_READ_NOT_SUPPORTED")]


def test_graphql_view_get_response_records_success_metrics(monkeypatch) -> None:
    backend = _FakeBackend()

    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    request = SimpleNamespace()

    monkeypatch.setattr(view, "get_graphql_params", lambda *_: ("query", {}, "Op", "1"))
    execution_result = SimpleNamespace(data={"ping": "pong"}, errors=None)
    monkeypatch.setattr(
        view, "execute_graphql_request", lambda *_args, **_kwargs: execution_result
    )
    monkeypatch.setattr(view, "json_encode", lambda _req, payload, **_kw: payload)
    monkeypatch.setattr(view_module, "graphql_metrics_enabled", lambda: True)
    monkeypatch.setattr(view_module, "get_graphql_metrics_backend", lambda: backend)
    monkeypatch.setattr(view_module, "normalize_operation_name", lambda _name: "op")
    monkeypatch.setattr(view_module, "resolve_operation_type", lambda *_: "query")

    payload, status = view.get_response(request, data={}, show_graphiql=True)

    assert status == 200
    assert payload == {"data": {"ping": "pong"}}
    assert backend.requests == [("op", "query", "success")]
    assert backend.errors == []


def test_graphql_view_get_response_partial_errors_record_error_status(
    monkeypatch,
) -> None:
    backend = _FakeBackend()

    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    request = SimpleNamespace()

    monkeypatch.setattr(view, "get_graphql_params", lambda *_: ("query", {}, "Op", "1"))
    error = SimpleNamespace(path=["ping"], extensions={})
    execution_result = SimpleNamespace(data={"ping": None}, errors=[error])
    monkeypatch.setattr(
        view, "execute_graphql_request", lambda *_args, **_kwargs: execution_result
    )
    monkeypatch.setattr(view, "format_error", lambda _err: {"message": "bad"})
    monkeypatch.setattr(view, "json_encode", lambda _req, payload, **_kw: payload)
    monkeypatch.setattr(view_module, "graphql_metrics_enabled", lambda: True)
    monkeypatch.setattr(view_module, "get_graphql_metrics_backend", lambda: backend)
    monkeypatch.setattr(view_module, "normalize_operation_name", lambda _name: "op")
    monkeypatch.setattr(view_module, "resolve_operation_type", lambda *_: "query")
    monkeypatch.setattr(view_module, "extract_error_code", lambda _err: "unknown")

    payload, status = view.get_response(request, data={}, show_graphiql=False)

    assert status == 200
    assert payload == {"errors": [{"message": "bad"}], "data": {"ping": None}}
    assert backend.requests == [("op", "query", "error")]
    assert backend.errors == [("op", "unknown")]


def test_graphql_view_get_response_metrics_failure_does_not_change_response(
    monkeypatch,
    caplog,
) -> None:
    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    request = SimpleNamespace()

    monkeypatch.setattr(view, "get_graphql_params", lambda *_: ("query", {}, "Op", "1"))
    execution_result = SimpleNamespace(data={"ping": "pong"}, errors=None)
    monkeypatch.setattr(
        view, "execute_graphql_request", lambda *_args, **_kwargs: execution_result
    )
    monkeypatch.setattr(view, "json_encode", lambda _req, payload, **_kw: payload)
    monkeypatch.setattr(view_module, "graphql_metrics_enabled", lambda: True)
    monkeypatch.setattr(
        view_module, "get_graphql_metrics_backend", lambda: _FailingBackend()
    )
    monkeypatch.setattr(view_module, "normalize_operation_name", lambda _name: "op")
    monkeypatch.setattr(view_module, "resolve_operation_type", lambda *_: "query")

    with caplog.at_level(logging.DEBUG):
        payload, status = view.get_response(request, data={}, show_graphiql=False)

    assert status == 200
    assert payload == {"data": {"ping": "pong"}}
    assert "graphql metrics recording failed" in caplog.text


def test_graphql_view_get_response_non_metrics_exceptions_propagate(
    monkeypatch,
) -> None:
    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    monkeypatch.setattr(view, "get_graphql_params", lambda *_: ("query", {}, "Op", "1"))

    def raise_execution_error(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(EXECUTE_FAILED)

    monkeypatch.setattr(view, "execute_graphql_request", raise_execution_error)

    with pytest.raises(RuntimeError, match=EXECUTE_FAILED):
        view.get_response(request=object(), data={}, show_graphiql=False)


def test_graphql_view_get_response_none_execution(monkeypatch) -> None:
    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    request = SimpleNamespace()
    monkeypatch.setattr(view, "get_graphql_params", lambda *_: (None, None, None, None))
    monkeypatch.setattr(view, "execute_graphql_request", lambda *_args, **_kwargs: None)

    payload, status = view.get_response(request, data={}, show_graphiql=False)
    assert payload is None
    assert status == 200
