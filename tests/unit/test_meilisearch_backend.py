from __future__ import annotations

from collections import UserList
from collections.abc import Mapping, Sequence

import pytest

from general_manager.search.backend import SearchBackendError, SearchDocument
from general_manager.search.backends import meilisearch as meili_module
from general_manager.search.backends.meilisearch import (
    MeilisearchBackend,
    MeilisearchTaskFailedError,
    _is_meilisearch_already_exists,
    _is_meilisearch_not_found,
    _meilisearch_error_code,
    _meilisearch_status_code,
)


class _FakeIndex:
    def __init__(self) -> None:
        """
        Initialize the fake index's internal state for tests.

        Attributes:
            added (list[dict[str, object]]): Documents passed to add_documents, appended in insertion order.
            deleted (list[list[str]]): Lists of document IDs passed to delete_documents, appended per call.
            settings (list[dict[str, object]]): Payloads passed to update_settings, recorded in update order.
        """
        self.added: list[dict[str, object]] = []
        self.deleted: list[list[str]] = []
        self.settings: list[dict[str, object]] = []
        self.documents: list[dict[str, object]] = []
        self.search_payloads: list[dict[str, object]] = []

    def update_settings(self, payload: dict[str, object]) -> dict[str, int]:
        """
        Record the provided settings payload for later inspection by tests.

        Parameters:
            payload (dict[str, object]): Settings payload to store.

        Returns:
            dict[str, int]: A simulated task response containing `{"taskUid": 1}`.
        """
        self.settings.append(payload)
        return {"taskUid": 1}

    def add_documents(self, payload: list[dict[str, object]]) -> dict[str, int]:
        """
        Add documents to the fake index for testing.

        Parameters:
            payload (list[dict[str, object]]): Documents to append to the index's stored documents.

        Returns:
            dict[str, int]: A simulated task response containing `"taskUid": 2`.
        """
        self.added.extend(payload)
        return {"taskUid": 2}

    def delete_documents(self, ids: list[str]) -> dict[str, int]:
        """
        Record the given document IDs as deleted and return a mock task identifier.

        Parameters:
            ids (list[str]): Sequence of document IDs to delete; the list is appended to self.deleted.

        Returns:
            dict[str, int]: A payload containing the mock task UID, e.g. {"taskUid": 3}.
        """
        self.deleted.append(ids)
        return {"taskUid": 3}

    def search(self, _query: str, _payload: dict[str, object]) -> dict[str, object]:
        """
        Return a fixed empty search response used by the fake index in tests.

        Parameters:
            _query (str): Ignored.
            _payload (dict[str, object]): Ignored.

        Returns:
            dict[str, object]: A search result with keys:
                - "hits": empty list.
                - "estimatedTotalHits": 0.
                - "processingTimeMs": 0.
        """
        self.search_payloads.append(_payload)
        return {"hits": [], "estimatedTotalHits": 0, "processingTimeMs": 0}

    def get_documents(self, payload: dict[str, object]) -> dict[str, object]:
        """Return a paginated fake documents response."""
        limit = int(payload["limit"])
        offset = int(payload["offset"])
        return {
            "results": self.documents[offset : offset + limit],
            "total": len(self.documents),
        }


class _FakeClient:
    def __init__(self, index: _FakeIndex) -> None:
        """
        Create a fake Meilisearch client bound to a fake index and initialize task wait tracking.

        Parameters:
            index (_FakeIndex): The fake index instance this client will operate on.

        Attributes:
            index (_FakeIndex): The provided index instance.
            waited (list[int]): List of task UIDs for which wait/get calls were recorded.
        """
        self.index = index
        self.waited: list[int] = []

    def get_or_create_index(
        self, _name: str, _payload: dict[str, object]
    ) -> _FakeIndex:
        """Return the configured fake index."""
        return self.index

    def get_index(self, _name: str) -> _FakeIndex:
        """
        Return the fake index instance associated with this client.

        Parameters:
            _name (str): Ignored; present to match the expected client interface.

        Returns:
            _FakeIndex: The associated fake index instance.
        """
        return self.index

    def create_index(self, _name: str, _payload: dict[str, object]) -> dict[str, int]:
        """
        Create an index and return a task identifier.

        Returns:
            dict: A mapping containing `'taskUid': 4`, the task identifier for the created index.
        """
        return {"taskUid": 4}

    def wait_for_task(self, task_uid: int) -> dict[str, object]:
        """
        Record the given task UID in the instance's waited list and return a succeeded status.

        Parameters:
            task_uid (int): Task UID to record as waited-on.

        Returns:
            dict[str, object]: A mapping with key "status" set to "succeeded".
        """
        self.waited.append(task_uid)
        return {"status": "succeeded"}

    def get_task(self, task_uid: int) -> dict[str, object]:
        """
        Record the provided task UID and return a succeeded task status.

        Appends the given task_uid to the instance's waited list as a side effect and returns a dictionary representing a successful task state.

        Returns:
            dict[str, object]: A mapping containing the task status, e.g. {"status": "succeeded"}.
        """
        self.waited.append(task_uid)
        return {"status": "succeeded"}


class _FailingClient(_FakeClient):
    def wait_for_task(self, task_uid: int) -> dict[str, object]:
        """
        Record the given task UID in the client's waited list and return a failed task payload.

        Returns:
            dict: A task result object with "status" set to "failed" and "error" containing {"message": "bad payload"}.
        """
        self.waited.append(task_uid)
        return {"status": "failed", "error": {"message": "bad payload"}}


def test_meilisearch_backend_waits_for_tasks() -> None:
    """Wait for Meilisearch settings, upsert, and delete tasks."""
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
    """Extract task UIDs from mapping and object task payloads."""
    backend = MeilisearchBackend(client=_FakeClient(_FakeIndex()))
    assert backend._extract_task_uid({"taskUid": 9}) == 9

    class _Task:
        task_uid = 10

    assert backend._extract_task_uid(_Task()) == 10


def test_meilisearch_backend_get_task_fallback() -> None:
    """Poll get_task when wait_for_task is not available."""

    class _Client:
        def __init__(self, index: _FakeIndex) -> None:
            """
            Initialize the fake client with an associated fake index and a tracker for awaited task UIDs.

            Parameters:
                index (_FakeIndex): The fake index instance this client operates on. The client will delegate index-related calls to this object and record task UIDs in `waited` when wait/get methods are invoked.
            """
            self.index = index
            self.waited: list[int] = []
            self.calls = 0
            self.status_sequence = ["enqueued", "processing", "succeeded"]

        def get_or_create_index(
            self, _name: str, _payload: dict[str, object]
        ) -> _FakeIndex:
            """Return the configured fake index."""
            return self.index

        def get_index(self, _name: str) -> _FakeIndex:
            """
            Return the configured fake index instance.

            Parameters:
                _name (str): Ignored; present for API compatibility.

            Returns:
                _FakeIndex: The fake index associated with this client.
            """
            return self.index

        def create_index(
            self, _name: str, _payload: dict[str, object]
        ) -> dict[str, int]:
            """
            Simulates creating a Meilisearch index and returns a fixed task UID.

            Returns:
                dict[str, int]: `{'taskUid': 4}` containing the task UID for the created index.
            """
            return {"taskUid": 4}

        def get_task(self, task_uid: int) -> dict[str, object]:
            """
            Record the requested task UID and return a succeeded status for that task.

            Parameters:
                task_uid (int): The identifier of the task being queried; appended to the client's `waited` list.

            Returns:
                dict[str, object]: A mapping with `"status"` set to `"succeeded"`.
            """
            self.waited.append(task_uid)
            status = self.status_sequence[
                min(self.calls, len(self.status_sequence) - 1)
            ]
            self.calls += 1
            return {"status": status}

    index = _FakeIndex()
    client = _Client(index)
    backend = MeilisearchBackend(client=client)

    backend.ensure_index("test-index", {"searchable_fields": ["name"]})
    assert client.waited == [1, 1, 1]


def test_meilisearch_backend_normalize_document_id() -> None:
    """Normalize invalid Meilisearch document IDs deterministically."""
    backend = MeilisearchBackend(client=_FakeClient(_FakeIndex()))
    assert backend._normalize_document_id("valid-id_1") == "valid-id_1"
    invalid_id = "invalid:{id}"
    unicode_id = "Project/ümlaut id"
    empty_id = ""
    assert backend._normalize_document_id(invalid_id) != invalid_id
    assert backend._normalize_document_id(unicode_id) == backend._normalize_document_id(
        unicode_id
    )
    assert backend._normalize_document_id(empty_id).startswith("gm_")
    assert backend._normalize_document_id("A" * 512).startswith("gm_")


def test_meilisearch_backend_settings_ignore_strings_and_accept_iterables() -> None:
    """Apply iterable settings while treating strings as invalid scalar values."""
    index = _FakeIndex()
    backend = MeilisearchBackend(client=_FakeClient(index))

    backend.ensure_index(
        "test-index",
        {
            "searchable_fields": "title",
            "filterable_fields": ("status", "owner"),
            "sortable_fields": {"created_at", 5},
        },
    )

    assert {"searchableAttributes": ["t", "i", "t", "l", "e"]} not in index.settings
    assert {"filterableAttributes": ["status", "owner"]} in index.settings
    sortable_payload = next(
        payload for payload in index.settings if "sortableAttributes" in payload
    )
    assert set(sortable_payload["sortableAttributes"]) == {"created_at", "5"}


def test_meilisearch_backend_create_index_uses_id_primary_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create missing indexes with `id` as the Meilisearch primary key."""

    class _NotFoundError(Exception):
        status_code = 404
        error_code = "not_found"

    class _CreateClient:
        def __init__(self) -> None:
            self.index = _FakeIndex()
            self.created: list[dict[str, object]] = []
            self.waited: list[int] = []
            self.lookups = 0

        def get_index(self, _name: str) -> _FakeIndex:
            self.lookups += 1
            if self.lookups == 1:
                raise _NotFoundError
            return self.index

        def create_index(
            self, _name: str, payload: dict[str, object]
        ) -> dict[str, int]:
            self.created.append(payload)
            return {"taskUid": 4}

        def wait_for_task(self, task_uid: int) -> dict[str, object]:
            self.waited.append(task_uid)
            return {"status": "succeeded"}

    client = _CreateClient()
    monkeypatch.setattr(meili_module, "MeilisearchApiError", _NotFoundError)
    backend = MeilisearchBackend(client=client)

    backend.ensure_index("missing-index", {})

    assert client.created == [{"primaryKey": "id"}]
    assert client.waited == [4]


def test_meilisearch_backend_empty_upsert_and_delete_behavior() -> None:
    """Empty upsert ensures the index, while empty delete does not touch the client."""

    class _TrackingClient(_FakeClient):
        def __init__(self, index: _FakeIndex) -> None:
            super().__init__(index)
            self.get_or_create_calls = 0

        def get_or_create_index(
            self, _name: str, _payload: dict[str, object]
        ) -> _FakeIndex:
            self.get_or_create_calls += 1
            return self.index

    index = _FakeIndex()
    client = _TrackingClient(index)
    backend = MeilisearchBackend(client=client)

    backend.upsert("test-index", [])
    backend.delete("test-index", [])
    backend.delete("test-index", [""])

    assert client.get_or_create_calls == 2
    assert index.added == []
    assert index.deleted == [[MeilisearchBackend._normalize_document_id("")]]


def test_meilisearch_backend_search_prefers_gm_document_id() -> None:
    """Return original GeneralManager document IDs from search hits."""

    class _SearchIndex(_FakeIndex):
        def search(self, _query: str, _payload: dict[str, object]) -> dict[str, object]:
            """
            Return a fixed simulated search result containing a single Project hit.

            Parameters:
                _query (str): Query string (unused in this fake index).
                _payload (dict[str, object]): Search options/payload (unused in this fake index).

            Returns:
                dict[str, object]: A Meilisearch-like result with:
                    - "hits": list containing one hit with keys:
                        - "id": internal hit id (str)
                        - "gm_document_id": original document id (str)
                        - "type": document type (str)
                        - "identification": identification object (dict)
                        - "data": document data (dict)
                        - "_rankingScore": ranking score (float)
                    - "estimatedTotalHits": total matching documents (int)
                    - "processingTimeMs": query processing time in milliseconds (int)
            """
            return {
                "hits": [
                    {
                        "id": "gm_hash",
                        "gm_document_id": 'Project:{"id": 9}',
                        "type": "Project",
                        "identification": {"id": 9},
                        "data": {"name": "Alpha"},
                        "_rankingScore": 1.0,
                    }
                ],
                "estimatedTotalHits": 1,
                "processingTimeMs": 5,
            }

    backend = MeilisearchBackend(client=_FakeClient(_SearchIndex()))
    result = backend.search("index", "Alpha")
    assert result.hits[0].id == 'Project:{"id": 9}'


def test_meilisearch_backend_search_payload_precedence_sort_and_defaults() -> None:
    """Build search payloads predictably and default missing hit fields."""

    class _SearchIndex(_FakeIndex):
        def search(self, _query: str, payload: dict[str, object]) -> dict[str, object]:
            self.search_payloads.append(payload)
            return {
                "hits": [
                    "malformed",
                    {"id": "safe_id"},
                    {
                        "gm_document_id": "original",
                        "type": "Project",
                        "identification": {"id": 1},
                        "data": {"name": "Alpha"},
                    },
                ],
                "estimatedTotalHits": 2,
            }

    index = _SearchIndex()
    backend = MeilisearchBackend(client=_FakeClient(index))

    result = backend.search(
        "index",
        "Alpha",
        filters={"status": "ready"},
        filter_expression='status = "raw"',
        sort_by="created_at:desc",
        sort_desc=True,
        types=["Project"],
    )

    assert index.search_payloads[0]["filter"] == 'status = "raw"'
    assert index.search_payloads[0]["sort"] == ["created_at:desc:desc"]
    assert len(result.hits) == 2
    assert result.hits[0].id == "safe_id"
    assert result.hits[0].type == ""
    assert result.hits[0].identification == {}
    assert result.hits[0].data == {}
    assert result.hits[0].score is None
    assert result.hits[1].id == "original"


def test_meilisearch_backend_search_uses_empty_filter_expression_as_provided() -> None:
    index = _FakeIndex()
    backend = MeilisearchBackend(client=_FakeClient(index))

    backend.search("index", "Alpha", filters={"status": "ready"}, filter_expression="")

    assert "filter" not in index.search_payloads[0]


def test_meilisearch_backend_applies_empty_settings_lists() -> None:
    index = _FakeIndex()
    backend = MeilisearchBackend(client=_FakeClient(index))

    backend.ensure_index(
        "test-index",
        {
            "searchable_fields": [],
            "filterable_fields": [],
            "sortable_fields": [],
        },
    )

    assert index.settings == [
        {"searchableAttributes": []},
        {"filterableAttributes": []},
        {"sortableAttributes": []},
    ]


def test_meilisearch_backend_lists_original_document_ids_by_type() -> None:
    """List original document IDs for the requested type labels."""
    index = _FakeIndex()
    index.documents = [
        {
            "id": "gm_hash_a",
            "gm_document_id": 'Project:{"id": 1}',
            "type": "Project",
        },
        {
            "id": "gm_hash_b",
            "gm_document_id": 'Other:{"id": 1}',
            "type": "Other",
        },
        {"id": "falsey_fallback", "gm_document_id": "", "type": "Project"},
        {"id": "legacy_id", "type": "Project"},
    ]
    backend = MeilisearchBackend(client=_FakeClient(index))

    assert backend.list_document_ids("index", types=["Project"]) == {
        'Project:{"id": 1}',
        "falsey_fallback",
        "legacy_id",
    }


def test_meilisearch_backend_document_payload_reserved_keys() -> None:
    """Preserve reserved user data keys under the nested data payload."""
    document = SearchDocument(
        id='Project:{"id": 5}',
        type="Project",
        identification={"id": 5},
        index="index",
        data={
            "id": "override",
            "gm_document_id": "override",
            "type": "override",
            "identification": {"id": "override"},
            "data": {"name": "override"},
            "name": "Alpha",
        },
        field_boosts={},
    )
    payload = MeilisearchBackend._document_payload(document)
    assert payload["gm_document_id"] == 'Project:{"id": 5}'
    assert payload["type"] == "Project"
    assert payload["identification"] == {"id": 5}
    assert payload["data"] == {
        "id": "override",
        "gm_document_id": "override",
        "type": "override",
        "identification": {"id": "override"},
        "data": {"name": "override"},
        "name": "Alpha",
    }
    assert payload["name"] == "Alpha"


def test_meilisearch_backend_build_filter_expression_escapes() -> None:
    """Escape quotes and backslashes in filter expression values."""
    expr = MeilisearchBackend._build_filter_expression(
        {"status": 'a"b\\c'},
        types=['Type"X'],
    )
    assert 'type = "Type\\"X"' in expr
    assert 'status = "a\\"b\\\\c"' in expr


def test_meilisearch_backend_non_terminal_status() -> None:
    """Ignore non-terminal task statuses when checking for failures."""
    MeilisearchBackend._raise_for_failed_task({"status": "processing"})


def test_meilisearch_backend_wait_for_task_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise a timeout error when fallback task polling never completes."""

    class _Client:
        def __init__(self) -> None:
            """Initialize fallback client call counters."""
            self.calls = 0

        def get_or_create_index(
            self, _name: str, _payload: dict[str, object]
        ) -> _FakeIndex:
            """Return a new fake index for creation and settings calls."""
            return _FakeIndex()

        def get_index(self, _name: str) -> _FakeIndex:
            """Return a new fake index for index lookup calls."""
            return _FakeIndex()

        def create_index(
            self, _name: str, _payload: dict[str, object]
        ) -> dict[str, int]:
            """Return a fake create-index task UID."""
            return {"taskUid": 1}

        def get_task(self, _task_uid: int) -> dict[str, object]:
            """Return a non-terminal task status for every poll."""
            self.calls += 1
            return {"status": "processing"}

    client = _Client()
    backend = MeilisearchBackend(client=client)

    timeline = iter([0.0, 6.0])
    monkeypatch.setattr(meili_module.time, "monotonic", lambda: next(timeline))
    monkeypatch.setattr(meili_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(MeilisearchTaskFailedError, match="timeout"):
        backend.ensure_index("test-index", {"searchable_fields": ["name"]})


def test_meilisearch_backend_raises_on_failed_task() -> None:
    """Raise SearchBackendError when a Meilisearch task fails."""
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


def test_meilisearch_backend_build_filter_expression_in_lookup() -> None:
    """Build filter expressions for __in lookups."""
    expr = MeilisearchBackend._build_filter_expression(
        {"status__in": ["ready", "paused"], "team": "alpha"},
        types=None,
    )
    assert 'status = "ready"' in expr
    assert 'status = "paused"' in expr
    assert 'team = "alpha"' in expr


def test_meilisearch_backend_build_filter_expression_groups() -> None:
    """Build grouped OR filter expressions with type restrictions."""
    expr = MeilisearchBackend._build_filter_expression(
        [{"status": "ready"}, {"status": "paused"}],
        types=["TypeA", "TypeB"],
    )
    assert 'type = "TypeA"' in expr
    assert 'type = "TypeB"' in expr
    assert 'status = "ready"' in expr
    assert 'status = "paused"' in expr


def test_meilisearch_backend_build_filter_expression_accepts_general_sequence() -> None:
    """Build grouped filters from sequences other than lists and tuples."""
    filters: Sequence[Mapping[str, object]] = UserList(
        [{"status": "ready"}, {"status": "paused"}]
    )

    expr = MeilisearchBackend._build_filter_expression(filters, types=None)

    assert expr == '(status = "ready") OR (status = "paused")'


def test_meilisearch_backend_build_filter_expression_empty() -> None:
    """Return no filter expression when no filters or types are provided."""
    assert MeilisearchBackend._build_filter_expression(None, None) is None


def test_meilisearch_backend_build_filter_expression_exact_value_rendering() -> None:
    """Render all structured filter values as quoted escaped strings."""
    expr = MeilisearchBackend._build_filter_expression(
        {
            "flag": True,
            "missing": None,
            "count": 3,
            "empty__in": [],
        },
        types=None,
    )

    assert 'flag = "True"' in expr
    assert 'missing = "None"' in expr
    assert 'count = "3"' in expr
    assert "()" in expr


def test_meilisearch_error_helpers() -> None:
    """Classify Meilisearch error helpers by code and HTTP status."""

    class _Error(Exception):
        def __init__(self, code: str | None, status: int | None) -> None:
            """Store fake Meilisearch error code and status values."""
            self.error_code = code
            self.status_code = status

    not_found = _Error("not_found", 404)
    assert _meilisearch_error_code(not_found) == "not_found"
    assert _meilisearch_status_code(not_found) == 404
    assert _is_meilisearch_not_found(not_found) is True

    exists = _Error("already_exists", 409)
    assert _is_meilisearch_already_exists(exists) is True
