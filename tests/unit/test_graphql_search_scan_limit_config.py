"""Configuration coverage for the server-owned GraphQL search scan budget."""

from __future__ import annotations

import pytest
from django.test import override_settings
from graphql import GraphQLError

from general_manager.api.graphql_search import get_graphql_search_total_scan_limit


_ENV_NAME = "GENERAL_MANAGER_GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT"


@pytest.fixture(autouse=True)
def _clear_scan_limit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV_NAME, raising=False)


@override_settings(GENERAL_MANAGER={})
def test_search_scan_limit_keeps_default_without_environment() -> None:
    assert get_graphql_search_total_scan_limit() is None


@pytest.mark.parametrize("value", ["1", "10000", " 25000 "])
@override_settings(GENERAL_MANAGER={})
def test_search_scan_limit_reads_environment(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(_ENV_NAME, value)

    assert get_graphql_search_total_scan_limit() == int(value)


@pytest.mark.parametrize("value", ["", " ", "0", "-1", "1.5", "true"])
@override_settings(GENERAL_MANAGER={})
def test_search_scan_limit_rejects_invalid_environment(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(_ENV_NAME, value)

    with pytest.raises(GraphQLError, match="must be a positive integer") as caught:
        get_graphql_search_total_scan_limit()

    assert caught.value.extensions == {"code": "BAD_USER_INPUT"}


@pytest.mark.parametrize("env_value", ["9000", "invalid"])
@pytest.mark.parametrize(
    ("configuration", "expected"),
    [
        (
            {
                "GENERAL_MANAGER": {"GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 2000},
                "GENERAL_MANAGER_GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 3000,
                "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 4000,
            },
            2000,
        ),
        (
            {
                "GENERAL_MANAGER": {},
                "GENERAL_MANAGER_GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 3000,
                "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 4000,
            },
            3000,
        ),
        (
            {"GENERAL_MANAGER": {}, "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 4000},
            4000,
        ),
    ],
)
def test_search_scan_limit_preserves_django_setting_precedence(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    configuration: dict[str, object],
    expected: int,
) -> None:
    monkeypatch.setenv(_ENV_NAME, env_value)

    with override_settings(**configuration):
        assert get_graphql_search_total_scan_limit() == expected


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "2000"])
def test_search_scan_limit_does_not_hide_invalid_django_setting(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    monkeypatch.setenv(_ENV_NAME, "10000")

    with (
        override_settings(GENERAL_MANAGER={"GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": value}),
        pytest.raises(GraphQLError, match="must be a positive integer") as caught,
    ):
        get_graphql_search_total_scan_limit()

    assert caught.value.extensions == {"code": "BAD_USER_INPUT"}
