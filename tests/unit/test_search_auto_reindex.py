from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from general_manager import apps as gm_apps


class SearchAutoReindexRemovedTests(SimpleTestCase):
    def test_legacy_auto_reindex_helpers_are_removed(self) -> None:
        assert not hasattr(gm_apps, "_SEARCH_REINDEXED")
        assert not hasattr(gm_apps, "_auto_reindex_search")
        assert not hasattr(gm_apps, "install_search_auto_reindex")
        assert not hasattr(gm_apps.GeneralmanagerConfig, "install_search_auto_reindex")


class DevSearchPrefixTests(TestCase):
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
