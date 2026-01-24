from __future__ import annotations

from django.test import SimpleTestCase

from general_manager.search.backend import SearchDocument
from general_manager.search.backends.dev import DevSearchBackend


class DevSearchBackendTests(SimpleTestCase):
    def setUp(self) -> None:
        self.backend = DevSearchBackend()
        self.backend.ensure_index("global", {})
        self.backend.upsert(
            "global",
            [
                SearchDocument(
                    id="Project:1",
                    type="Project",
                    identification={"id": 1},
                    index="global",
                    data={"name": "Alpha Project", "status": "public", "tags": ["a"]},
                    field_boosts={"name": 2.0},
                ),
                SearchDocument(
                    id="Project:2",
                    type="Project",
                    identification={"id": 2},
                    index="global",
                    data={"name": "Beta Project", "status": "private", "tags": ["b"]},
                    field_boosts={"name": 1.0},
                ),
            ],
        )

    def test_search_with_filter_groups(self) -> None:
        result = self.backend.search(
            "global",
            "",
            filters=[{"status": "public"}, {"tags__in": ["b"]}],
        )
        assert result.total == 2

    def test_search_sorting(self) -> None:
        result = self.backend.search("global", "", sort_by="name", sort_desc=True)
        assert result.hits[0].data["name"] == "Beta Project"
