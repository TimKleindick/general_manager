import unittest
from types import SimpleNamespace
from django.test import SimpleTestCase, override_settings
from graphql.error import GraphQLError

from general_manager.metrics.graphql import (
    GraphQLResolverTimingMiddleware,
    _build_field_name,
    build_graphql_middleware,
    extract_error_code,
    normalize_error_code,
    normalize_field_name,
    normalize_operation_name,
    resolve_operation_type,
)


class GraphQLMetricsNormalizationTests(SimpleTestCase):
    def test_operation_name_defaults_to_unknown(self):
        self.assertEqual(normalize_operation_name(None), "unknown")
        self.assertEqual(normalize_operation_name(""), "unknown")

    @override_settings(GENERAL_MANAGER_GRAPHQL_METRICS_MAX_OPERATION_LENGTH=4)
    def test_operation_name_truncates(self):
        self.assertEqual(normalize_operation_name("LongName"), "Long")

    @override_settings(
        GENERAL_MANAGER_GRAPHQL_METRICS_OPERATION_ALLOWLIST=["Allowed", "Allowed_2"]
    )
    def test_operation_name_allowlist(self):
        self.assertEqual(normalize_operation_name("Allowed"), "Allowed")
        self.assertEqual(normalize_operation_name("Allowed 2"), "Allowed_2")
        self.assertEqual(normalize_operation_name("NotAllowed"), "unknown")

    @override_settings(
        GENERAL_MANAGER_GRAPHQL_METRICS_OPERATION_ALLOWLIST=["Allowed"],
        GENERAL_MANAGER_GRAPHQL_METRICS_UNKNOWN_OPERATION_POLICY="hash",
    )
    def test_operation_name_hash_policy(self):
        hashed = normalize_operation_name("NotAllowed")
        self.assertTrue(hashed.startswith("op_"))
        self.assertNotEqual(hashed, "unknown")

    def test_operation_type_resolution(self):
        query = "query MetricsQuery { __typename }"
        self.assertEqual(resolve_operation_type(query, "MetricsQuery"), "query")

    def test_error_code_normalization(self):
        self.assertEqual(normalize_error_code("BAD_USER_INPUT"), "BAD_USER_INPUT")
        self.assertEqual(normalize_error_code(None), "unknown")

    def test_field_name_normalization(self):
        self.assertEqual(normalize_field_name("Query.my field"), "Query.my_field")


def test_build_graphql_middleware_adds_resolver_timing(settings, monkeypatch):
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


def test_resolve_operation_type_invalid_query():
    assert resolve_operation_type("query {", None) == "unknown"


def test_extract_error_code_from_graphql_error():
    error = GraphQLError("bad", extensions={"code": "BAD"})
    assert extract_error_code(error) == "BAD"


def test_build_field_name_variants():
    info = SimpleNamespace(
        parent_type=SimpleNamespace(name="Query"),
        field_name="status",
    )
    assert _build_field_name(info) == "Query.status"

    info_no_parent = SimpleNamespace(parent_type=None, field_name="status")
    assert _build_field_name(info_no_parent) == "status"

    info_missing = SimpleNamespace(parent_type=None, field_name=None)
    assert _build_field_name(info_missing) == "unknown"


if __name__ == "__main__":
    unittest.main()
