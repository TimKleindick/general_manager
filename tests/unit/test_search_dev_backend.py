import pytest

from general_manager.search.backend import SearchDocument
from general_manager.search.backends.dev import DevSearchBackend


def test_dev_search_filters_and_boosts() -> None:
    backend = DevSearchBackend()
    backend.ensure_index("global", {})

    doc_public = SearchDocument(
        id='Project:{"id": 1}',
        type="Project",
        identification={"id": 1},
        index="global",
        data={"name": "alpha test", "status": "public"},
        field_boosts={"name": 2.0},
        index_boost=1.0,
    )
    doc_private = SearchDocument(
        id='Project:{"id": 2}',
        type="Project",
        identification={"id": 2},
        index="global",
        data={"name": "alpha", "status": "private"},
        field_boosts={"name": 1.0},
        index_boost=1.0,
    )

    backend.upsert("global", [doc_public, doc_private])

    result = backend.search("global", "test", filters={"status": "public"})
    assert result.total == 1
    assert result.hits[0].identification == {"id": 1}
    assert result.hits[0].score == pytest.approx(2.0)


def test_dev_search_list_filter() -> None:
    backend = DevSearchBackend()
    backend.ensure_index("global", {})

    doc = SearchDocument(
        id='Project:{"id": 1}',
        type="Project",
        identification={"id": 1},
        index="global",
        data={"team_ids": [10, 20]},
        field_boosts={},
        index_boost=None,
    )
    backend.upsert("global", [doc])

    result = backend.search("global", "", filters={"team_ids": [20]})
    assert result.total == 1
