from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase
from unittest.mock import patch

from general_manager import apps as gm_apps


class AppsUtilitiesTests(SimpleTestCase):
    def tearDown(self) -> None:
        gm_apps._SEARCH_REINDEXED = False
        super().tearDown()

    def test_normalize_graphql_path(self) -> None:
        assert gm_apps._normalize_graphql_path("graphql") == "/graphql/"
        assert gm_apps._normalize_graphql_path("/graphql") == "/graphql/"
        assert gm_apps._normalize_graphql_path("/graphql/") == "/graphql/"

    def test_should_auto_reindex(self) -> None:
        settings = SimpleNamespace(
            GENERAL_MANAGER={"SEARCH_AUTO_REINDEX": True}, DEBUG=True
        )
        assert gm_apps._should_auto_reindex(settings) is True

        settings = SimpleNamespace(
            GENERAL_MANAGER={"SEARCH_AUTO_REINDEX": True}, DEBUG=False
        )
        assert gm_apps._should_auto_reindex(settings) is False

    def test_auto_reindex_search_skips_invalid_env(self) -> None:
        gm_apps._SEARCH_REINDEXED = False
        gm_apps._auto_reindex_search()
        gm_apps._auto_reindex_search(environ={"PATH_INFO": None})
        assert gm_apps._SEARCH_REINDEXED is False

    def test_auto_reindex_search_triggers_on_graphql_path(self) -> None:
        gm_apps._SEARCH_REINDEXED = False
        with patch("general_manager.apps.call_command") as call_command:
            gm_apps._auto_reindex_search(environ={"PATH_INFO": "/graphql"})
            call_command.assert_called_once_with("search_index", reindex=True)
            assert gm_apps._SEARCH_REINDEXED is True
        gm_apps._SEARCH_REINDEXED = False
