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

    def get_task(self, task_uid: int) -> dict[str, object]:
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
    raw_id = 'Project:{"id": 1}'
    backend.upsert(
        "test-index",
        [
            SearchDocument(
                id=raw_id,
                type="Project",
                identification={"id": 1},
                index="test-index",
                data={"name": "Alpha"},
                field_boosts={},
            )
        ],
    )

    assert client.waited == [1, 2]
    assert index.added[0]["gm_document_id"] == raw_id
    assert index.added[0]["id"] != raw_id
    backend.delete("test-index", [raw_id])
    assert index.deleted[0][0] == index.added[0]["id"]


def test_meilisearch_backend_extract_task_uid() -> None:
    backend = MeilisearchBackend(client=_FakeClient(_FakeIndex()))
    assert backend._extract_task_uid({"taskUid": 9}) == 9

    class _Task:
        task_uid = 10

    assert backend._extract_task_uid(_Task()) == 10


def test_meilisearch_backend_get_task_fallback() -> None:
    class _Client:
        def __init__(self, index: _FakeIndex) -> None:
            self.index = index
            self.waited: list[int] = []

        def get_index(self, _name: str) -> _FakeIndex:
            return self.index

        def create_index(
            self, _name: str, _payload: dict[str, object]
        ) -> dict[str, int]:
            return {"taskUid": 4}

        def get_task(self, task_uid: int) -> dict[str, object]:
            self.waited.append(task_uid)
            return {"status": "succeeded"}

    index = _FakeIndex()
    client = _Client(index)
    backend = MeilisearchBackend(client=client)

    backend.ensure_index("test-index", {"searchable_fields": ["name"]})
    assert client.waited == [1]


def test_meilisearch_backend_normalize_document_id() -> None:
    backend = MeilisearchBackend(client=_FakeClient(_FakeIndex()))
    assert backend._normalize_document_id("valid-id_1") == "valid-id_1"
    assert backend._normalize_document_id("invalid:{id}") != "invalid:{id}"


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
