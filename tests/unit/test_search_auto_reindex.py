from __future__ import annotations

from unittest.mock import patch

from django.core.signals import request_started
from django.test import TestCase, override_settings

from general_manager import apps as gm_apps
from general_manager.apps import GeneralmanagerConfig


class SearchAutoReindexTests(TestCase):
    def tearDown(self) -> None:
        request_started.disconnect(dispatch_uid="general_manager_auto_reindex_search")
        gm_apps._SEARCH_REINDEXED = False
        super().tearDown()

    @override_settings(GENERAL_MANAGER={"SEARCH_AUTO_REINDEX": True}, DEBUG=True)
    def test_auto_reindex_runs_once(self) -> None:
        gm_apps._SEARCH_REINDEXED = False
        with patch("general_manager.apps.call_command") as call_command:
            GeneralmanagerConfig.install_search_auto_reindex()
            request_started.send(
                sender=self.__class__, environ={"PATH_INFO": "/graphql/"}
            )
            request_started.send(
                sender=self.__class__, environ={"PATH_INFO": "/graphql/"}
            )

            call_command.assert_called_once_with("search_index", reindex=True)

    @override_settings(
        GENERAL_MANAGER={"SEARCH_AUTO_REINDEX": True},
        DEBUG=True,
        GRAPHQL_URL="graphql/",
    )
    def test_auto_reindex_skips_other_paths(self) -> None:
        gm_apps._SEARCH_REINDEXED = False
        with patch("general_manager.apps.call_command") as call_command:
            GeneralmanagerConfig.install_search_auto_reindex()
            request_started.send(
                sender=self.__class__, environ={"PATH_INFO": "/admin/"}
            )

            call_command.assert_not_called()


class DevSearchPrefixTests(TestCase):
    @override_settings(GENERAL_MANAGER={"SEARCH_AUTO_REINDEX": True}, DEBUG=True)
    def test_dev_search_prefix_match(self) -> None:
        from general_manager.search.backends.dev import DevSearchBackend
        from general_manager.search.backend import SearchDocument

        backend = DevSearchBackend()
        backend.ensure_index(
            "global",
            {
                "searchable_fields": ["name"],
                "filterable_fields": [],
                "sortable_fields": [],
                "field_boosts": {},
            },
        )
        backend.upsert(
            "global",
            [
                SearchDocument(
                    id="JobRoleCatalog:1",
                    type="JobRoleCatalog",
                    identification={"id": 1},
                    index="global",
                    data={"name": "Dockmaster"},
                    field_boosts={"name": 1.0},
                    index_boost=1.0,
                )
            ],
        )
        result = backend.search("global", "Dock")
        assert result.total == 1
