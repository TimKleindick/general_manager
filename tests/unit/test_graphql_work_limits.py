"""Optional workload limits never replace permission decisions."""

from django.test import override_settings
from graphql import GraphQLError
import pytest
from general_manager.api.graphql_query_permissions import QueryFieldAuthorizationBudget
from general_manager.api.graphql_search import get_graphql_search_total_scan_limit


@override_settings(GENERAL_MANAGER={})
def test_query_field_budget_is_unlimited_by_default():
    budget = QueryFieldAuthorizationBudget()
    for _ in range(1001):
        budget.charge()


@override_settings(GENERAL_MANAGER={"GRAPHQL_QUERY_PERMISSION_LIMIT": 2})
def test_query_field_budget_obeys_explicit_limit():
    budget = QueryFieldAuthorizationBudget()
    budget.charge()
    budget.charge()
    with pytest.raises(GraphQLError, match="candidate limit exceeded"):
        budget.charge()


@override_settings(GENERAL_MANAGER={})
def test_search_scan_budget_is_unlimited_by_default():
    assert get_graphql_search_total_scan_limit() is None


@pytest.mark.parametrize(
    "name",
    [
        "GRAPHQL_QUERY_PERMISSION_LIMIT",
        "GRAPHQL_SEARCH_SOURCE_AUTHORIZATION_LIMIT",
        "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT",
    ],
)
@pytest.mark.parametrize(
    "value,expected", [("1", 1), (" 2000 ", 2000), ("none", None), ("unlimited", None)]
)
@override_settings(GENERAL_MANAGER={})
def test_work_budget_environment(name, value, expected, monkeypatch):
    from general_manager.api.graphql_work_limits import get_graphql_work_limit

    monkeypatch.setenv(f"GENERAL_MANAGER_{name}", value)
    assert get_graphql_work_limit(name) == expected


@pytest.mark.parametrize("value", [None, 10])
def test_explicit_setting_precedes_environment(value, monkeypatch):
    from general_manager.api.graphql_work_limits import get_graphql_work_limit

    monkeypatch.setenv("GENERAL_MANAGER_GRAPHQL_QUERY_PERMISSION_LIMIT", "invalid")
    with override_settings(GENERAL_MANAGER={"GRAPHQL_QUERY_PERMISSION_LIMIT": value}):
        assert get_graphql_work_limit("GRAPHQL_QUERY_PERMISSION_LIMIT") == value


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "12"])
def test_invalid_setting_is_rejected(value):
    from general_manager.api.graphql_work_limits import get_graphql_work_limit

    with override_settings(GENERAL_MANAGER={"GRAPHQL_QUERY_PERMISSION_LIMIT": value}):
        with pytest.raises(GraphQLError, match="positive integer or None"):
            get_graphql_work_limit("GRAPHQL_QUERY_PERMISSION_LIMIT")
