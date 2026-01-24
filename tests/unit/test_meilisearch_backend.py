from __future__ import annotations

import pytest

from general_manager.search.backend import SearchBackendError, SearchDocument
from general_manager.search.backends.meilisearch import MeilisearchBackend


class _FakeIndex:
    def __init__(self) -> None:
        self.added: list[dict[str, object]] = []
        self.deleted: list[list[str]] = []
        self.settings: list[dict[str, object]] = []

    def update_settings(self, payload: dict[str, object]) -> dict[str, int]:
        self.settings.append(payload)
        return {"taskUid": 1}

    def add_documents(self, payload: list[dict[str, object]]) -> dict[str, int]:
        self.added.extend(payload)
        return {"taskUid": 2}

    def delete_documents(self, ids: list[str]) -> dict[str, int]:
        self.deleted.append(ids)
        return {"taskUid": 3}

    def search(self, _query: str, _payload: dict[str, object]) -> dict[str, object]:
        return {"hits": [], "estimatedTotalHits": 0, "processingTimeMs": 0}


class _FakeClient:
    def __init__(self, index: _FakeIndex) -> None:
        self.index = index
        self.waited: list[int] = []

    def get_index(self, _name: str) -> _FakeIndex:
        return self.index

    def create_index(self, _name: str, _payload: dict[str, object]) -> dict[str, int]:
        return {"taskUid": 4}

    def wait_for_task(self, task_uid: int) -> dict[str, object]:
        self.waited.append(task_uid)
        return {"status": "succeeded"}


class _FailingClient(_FakeClient):
    def wait_for_task(self, task_uid: int) -> dict[str, object]:
        self.waited.append(task_uid)
        return {"status": "failed", "error": {"message": "bad payload"}}


def test_meilisearch_backend_waits_for_tasks() -> None:
    index = _FakeIndex()
    client = _FakeClient(index)
    backend = MeilisearchBackend(client=client)

    backend.ensure_index("test-index", {"searchable_fields": ["name"]})
    backend.upsert(
        "test-index",
        [
            SearchDocument(
                id='Project:{"id": 1}',
                type="Project",
                identification={"id": 1},
                index="test-index",
                data={"name": "Alpha"},
                field_boosts={},
            )
        ],
    )

    assert client.waited == [1, 2]


def test_meilisearch_backend_raises_on_failed_task() -> None:
    index = _FakeIndex()
    client = _FailingClient(index)
    backend = MeilisearchBackend(client=client)

    with pytest.raises(SearchBackendError, match="Meilisearch task did not succeed"):
        backend.upsert(
            "test-index",
            [
                SearchDocument(
                    id='Project:{"id": 2}',
                    type="Project",
                    identification={"id": 2},
                    index="test-index",
                    data={"name": "Beta"},
                    field_boosts={},
                )
            ],
        )
