from __future__ import annotations

from types import SimpleNamespace

import graphene

from general_manager.api import graphql_view as view_module
from general_manager.api.graphql_view import GeneralManagerGraphQLView
from general_manager.metrics.graphql import GraphQLResolverTimingMiddleware


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


def test_graphql_view_get_middleware_adds_resolver_timing(monkeypatch):
    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    monkeypatch.setattr(
        view_module, "graphql_metrics_resolver_timing_enabled", lambda: True
    )

    def _base_middleware(_self, _request):
        return None

    monkeypatch.setattr(view_module.GraphQLView, "get_middleware", _base_middleware)
    middleware = view.get_middleware(request=object())
    assert len(middleware) == 1
    assert isinstance(middleware[0], GraphQLResolverTimingMiddleware)


def test_graphql_view_get_middleware_deduplicates(monkeypatch):
    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    existing = GraphQLResolverTimingMiddleware()
    monkeypatch.setattr(
        view_module, "graphql_metrics_resolver_timing_enabled", lambda: True
    )

    def _base_middleware(_self, _request):
        return [existing]

    monkeypatch.setattr(view_module.GraphQLView, "get_middleware", _base_middleware)
    middleware = view.get_middleware(request=object())
    assert middleware == [existing]


def test_graphql_view_get_response_records_metrics(monkeypatch):
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


def test_graphql_view_get_response_none_execution(monkeypatch):
    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(schema=graphene.Schema(query=Query))
    request = SimpleNamespace()
    monkeypatch.setattr(view, "get_graphql_params", lambda *_: (None, None, None, None))
    monkeypatch.setattr(view, "execute_graphql_request", lambda *_args, **_kwargs: None)

    payload, status = view.get_response(request, data={}, show_graphiql=False)
    assert payload is None
    assert status == 200
