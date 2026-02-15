from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase
from unittest.mock import patch

from general_manager import apps as gm_apps


class AppsUtilitiesTests(SimpleTestCase):
    def tearDown(self) -> None:
        gm_apps._SEARCH_REINDEXED = False
        gm_apps._GRAPHQL_WARMUP_RAN = False
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

    def test_should_run_graphql_warmup_in_debug_for_runserver_only(self) -> None:
        settings = SimpleNamespace(DEBUG=True)
        with patch.object(gm_apps.sys, "argv", ["manage.py", "runserver"]):
            assert gm_apps._should_run_graphql_warmup(settings) is True
        with patch.object(gm_apps.sys, "argv", ["manage.py", "migrate"]):
            assert gm_apps._should_run_graphql_warmup(settings) is False

    def test_should_run_graphql_warmup_in_non_debug(self) -> None:
        settings = SimpleNamespace(DEBUG=False)
        with patch.object(gm_apps.sys, "argv", ["manage.py", "migrate"]):
            assert gm_apps._should_run_graphql_warmup(settings) is True

    def test_graphql_warmup_skips_non_runserver_in_debug(self) -> None:
        gm_apps._GRAPHQL_WARMUP_RAN = False
        gm_apps._GRAPHQL_WARMUP_MANAGERS = (gm_apps.GeneralManager,)
        with (
            patch.object(gm_apps.sys, "argv", ["manage.py", "migrate"]),
            patch("general_manager.apps.settings", SimpleNamespace(DEBUG=True)),
            patch("general_manager.apps.warmup_enabled", return_value=True),
            patch("general_manager.apps.dispatch_graphql_warmup") as dispatch,
            patch(
                "general_manager.apps.GeneralmanagerConfig.warm_up_graphql_properties"
            ) as fallback,
        ):
            gm_apps._run_graphql_warmup_once()

        dispatch.assert_not_called()
        fallback.assert_not_called()
        assert gm_apps._GRAPHQL_WARMUP_RAN is False
