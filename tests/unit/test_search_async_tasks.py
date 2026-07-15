from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest
from django.test import TestCase, override_settings

import general_manager.search.async_tasks as async_tasks
from general_manager.search.indexer import SearchIndexer


@dataclass
class _DummyInstance:
    value: int


class _DummyManager:
    def __init__(self, **kwargs: object) -> None:
        """
        Initialize the manager with identification derived from provided keyword arguments.

        Parameters:
            **kwargs: Arbitrary identification attributes to store.

        Attributes:
            identification (dict): Mapping of provided keyword argument names to their values.
        """
        self.identification = kwargs


class _DummyIndexer:
    def __init__(self) -> None:
        """
        Initialize a dummy indexer that records indexed and deleted instances.

        Attributes:
            indexed (list[object]): Instances that have been passed to index operations, in order.
            deleted (list[object]): Instances that have been passed to delete operations, in order.
        """
        self.indexed: list[object] = []
        self.deleted: list[object] = []

    def index_instance(self, instance: object) -> None:
        """
        Record an instance as indexed.

        Parameters:
            instance (object): The instance to record as indexed.
        """
        self.indexed.append(instance)

    def delete_instance(self, instance: object) -> None:
        """
        Record that an instance was deleted by appending it to this indexer's `deleted` list.

        Parameters:
            instance (object): The instance that was deleted and should be recorded.
        """
        self.deleted.append(instance)


class _DummyTask:
    def __init__(self) -> None:
        """
        Initialize the dummy task and prepare its call log.

        Creates `self.calls`, a list used to record tuples of `(manager_path, identification)` each time the task's `delay` method is invoked.
        """
        self.calls: list[tuple[str, dict]] = []

    def delay(self, manager_path: str, identification: dict) -> None:
        """
        Record a delayed task invocation by appending the manager path and identification to the calls list.

        Parameters:
            manager_path (str): Dotted import path identifying the manager to handle the task.
            identification (dict): Mapping of fields used to identify the target instance.
        """
        self.calls.append((manager_path, identification))


def test_async_enabled_reads_settings() -> None:
    with override_settings(GENERAL_MANAGER={}):
        assert async_tasks._async_enabled() is False
    with override_settings(SEARCH_ASYNC=True):
        assert async_tasks._async_enabled() is True
    with override_settings(SEARCH_ASYNC=False):
        assert async_tasks._async_enabled() is False
    with override_settings(SEARCH_ASYNC=True, GENERAL_MANAGER={"SEARCH_ASYNC": False}):
        assert async_tasks._async_enabled() is False


def test_dispatch_index_update_inline_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    indexer = _DummyIndexer()

    def _noop_init(_self: SearchIndexer, _backend: object) -> None:
        """
        No-op initializer that accepts a SearchIndexer instance and a backend but performs no initialization.

        Parameters:
            _self (SearchIndexer): The SearchIndexer instance to initialize (ignored).
            _backend (object): The backend that would normally be used for initialization (ignored).
        """
        return None

    monkeypatch.setattr(async_tasks, "get_search_backend", lambda: object())
    monkeypatch.setattr(SearchIndexer, "__init__", _noop_init)
    monkeypatch.setattr(
        SearchIndexer,
        "index_instance",
        lambda _self, instance: indexer.index_instance(instance),
    )
    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", False)

    async_tasks.dispatch_index_update(
        action="index",
        manager_path="tests.unit.test_search_async_tasks._DummyManager",
        identification={"id": 1},
        instance=_DummyInstance(1),
    )

    assert indexer.indexed


def test_dispatch_index_update_inline_instance_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    indexer = _DummyIndexer()

    def _noop_init(_self: SearchIndexer, _backend: object) -> None:
        """
        No-op initializer that accepts a SearchIndexer instance and a backend but performs no initialization.

        Parameters:
            _self (SearchIndexer): The SearchIndexer instance to initialize (ignored).
            _backend (object): The backend that would normally be used for initialization (ignored).
        """
        return None

    monkeypatch.setattr(async_tasks, "get_search_backend", lambda: object())
    monkeypatch.setattr(SearchIndexer, "__init__", _noop_init)
    monkeypatch.setattr(
        SearchIndexer,
        "delete_instance",
        lambda _self, instance: indexer.delete_instance(instance),
    )
    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", False)

    async_tasks.dispatch_index_update(
        action="delete",
        manager_path="tests.unit.test_search_async_tasks._DummyManager",
        identification={"id": 5},
        instance=_DummyInstance(5),
    )

    assert indexer.deleted


def test_dispatch_index_update_inline_by_path(monkeypatch: pytest.MonkeyPatch) -> None:
    indexer = _DummyIndexer()

    def _noop_init(_self: SearchIndexer, _backend: object) -> None:
        """
        No-op initializer that accepts a SearchIndexer instance and a backend but performs no initialization.

        Parameters:
            _self (SearchIndexer): The SearchIndexer instance to initialize (ignored).
            _backend (object): The backend that would normally be used for initialization (ignored).
        """
        return None

    def _delete_task(_manager_path: str, identification: dict) -> None:
        """
        Delete the instance identified by `identification` from the dummy index.

        Parameters:
            _manager_path (str): Unused manager import path placeholder (ignored).
            identification (dict): Keyword arguments used to construct a `_DummyManager`; that manager is passed to the indexer's `delete_instance` method.
        """
        indexer.delete_instance(_DummyManager(**identification))

    monkeypatch.setattr(async_tasks, "get_search_backend", lambda: object())
    monkeypatch.setattr(SearchIndexer, "__init__", _noop_init)
    monkeypatch.setattr(
        SearchIndexer,
        "delete_instance",
        lambda _self, instance: indexer.delete_instance(instance),
    )
    monkeypatch.setattr(async_tasks, "_resolve_manager", lambda _path: _DummyManager)
    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", False)
    monkeypatch.setattr(async_tasks, "delete_instance_task", _delete_task)

    async_tasks.dispatch_index_update(
        action="delete",
        manager_path="tests.unit.test_search_async_tasks._DummyManager",
        identification={"id": 2},
        instance=None,
    )

    assert indexer.deleted


def test_dispatch_index_update_async(monkeypatch: pytest.MonkeyPatch) -> None:
    index_task = _DummyTask()
    delete_task = _DummyTask()

    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", True)
    monkeypatch.setattr(async_tasks, "_async_enabled", lambda: True)
    monkeypatch.setattr(async_tasks, "index_instance_task", index_task)
    monkeypatch.setattr(async_tasks, "delete_instance_task", delete_task)

    async_tasks.dispatch_index_update(
        action="index",
        manager_path="tests.unit.test_search_async_tasks._DummyManager",
        identification={"id": 3},
        instance=_DummyInstance(3),
    )
    async_tasks.dispatch_index_update(
        action="delete",
        manager_path="tests.unit.test_search_async_tasks._DummyManager",
        identification={"id": 4},
        instance=_DummyInstance(4),
    )

    assert index_task.calls == [
        ("tests.unit.test_search_async_tasks._DummyManager", {"id": 3})
    ]
    assert delete_task.calls == [
        ("tests.unit.test_search_async_tasks._DummyManager", {"id": 4})
    ]


def test_dispatch_index_update_async_ignores_inline_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async routing wins over a provided instance when Celery is available."""
    index_task = _DummyTask()

    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", True)
    monkeypatch.setattr(async_tasks, "_async_enabled", lambda: True)
    monkeypatch.setattr(async_tasks, "index_instance_task", index_task)

    async_tasks.dispatch_index_update(
        action="index",
        manager_path="tests.unit.test_search_async_tasks._DummyManager",
        identification={"id": 9},
        instance=_DummyInstance(9),
    )

    assert index_task.calls == [
        ("tests.unit.test_search_async_tasks._DummyManager", {"id": 9})
    ]


def test_dispatch_index_update_rejects_invalid_action() -> None:
    """Dispatch rejects unsupported search index action strings."""
    with pytest.raises(ValueError):
        async_tasks.dispatch_index_update(
            action="archive",
            manager_path="tests.unit.test_search_async_tasks._DummyManager",
            identification={"id": 1},
        )


def test_index_and_delete_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_search_indexer import Project

    indexer = _DummyIndexer()

    def _noop_init(_self: SearchIndexer, _backend: object) -> None:
        """
        No-op initializer that accepts a SearchIndexer instance and a backend but performs no initialization.

        Parameters:
            _self (SearchIndexer): The SearchIndexer instance to initialize (ignored).
            _backend (object): The backend that would normally be used for initialization (ignored).
        """
        return None

    monkeypatch.setattr(async_tasks, "get_search_backend", lambda: object())
    monkeypatch.setattr(SearchIndexer, "__init__", _noop_init)
    monkeypatch.setattr(
        SearchIndexer,
        "index_instance",
        lambda _self, instance: indexer.index_instance(instance),
    )
    monkeypatch.setattr(
        SearchIndexer,
        "delete_instance",
        lambda _self, instance: indexer.delete_instance(instance),
    )
    monkeypatch.setattr(async_tasks, "_resolve_manager_class", lambda _path: Project)

    import importlib
    import sys
    import types

    fake_celery = types.SimpleNamespace(shared_task=lambda func: func)
    sys.modules["celery"] = fake_celery
    reloaded = importlib.reload(async_tasks)
    try:
        reloaded.get_search_backend = lambda: object()
        reloaded._resolve_manager_class = lambda _path: Project
        reloaded.index_instance_task(
            "tests.unit.test_search_async_tasks._DummyManager", {"id": 10}
        )
        reloaded.delete_instance_task(
            "tests.unit.test_search_async_tasks._DummyManager", {"id": 11}
        )
    finally:
        sys.modules.pop("celery", None)
        importlib.reload(async_tasks)

    assert indexer.indexed
    assert indexer.deleted


def test_resolve_manager_rejects_non_callable_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manager resolution rejects paths that do not resolve to callables."""
    monkeypatch.setattr(async_tasks, "import_string", lambda _path: object())

    with pytest.raises(TypeError):
        async_tasks._resolve_manager("tests.not_callable")


def test_index_task_propagates_manager_construction_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task execution propagates errors while reconstructing an instance."""

    from tests.unit.test_search_indexer import Project

    class BrokenManager(Project):
        """Manager constructor that fails."""

        def __init__(self, **_kwargs: object) -> None:
            raise RuntimeError

    monkeypatch.setattr(
        async_tasks, "_resolve_manager_class", lambda _path: BrokenManager
    )

    with pytest.raises(RuntimeError):
        async_tasks.index_instance_task("tests.BrokenManager", {"id": 1})


def test_resolve_manager_class_rejects_callable_non_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async manager paths must resolve to GeneralManager subclasses."""
    monkeypatch.setattr(async_tasks, "import_string", lambda _path: _DummyManager)

    with pytest.raises(async_tasks.InvalidSearchManagerPathError):
        async_tasks._resolve_manager_class("tests.DummyManager")


def test_index_instance_task_targets_one_named_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker can index one exact manager/index pair."""
    from tests.unit.test_search_indexer import Project

    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(async_tasks, "_resolve_manager_class", lambda _path: Project)
    monkeypatch.setattr(async_tasks, "get_search_backend", lambda: object())
    monkeypatch.setattr(
        SearchIndexer,
        "__init__",
        lambda self, backend: setattr(self, "backend", backend),
    )
    monkeypatch.setattr(
        SearchIndexer,
        "index_instance_index",
        lambda _self, instance, index_name: calls.append((instance, index_name)),
        raising=False,
    )

    async_tasks.index_instance_task("tests.Project", {"id": 1}, "global")

    assert calls[0][0].identification == {"id": 1}
    assert calls[0][1] == "global"


def test_delete_documents_task_never_reconstructs_deleted_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete workers consume captured IDs instead of constructing a manager."""
    from tests.unit.test_search_indexer import Project

    deleted: list[object] = []
    monkeypatch.setattr(async_tasks, "_resolve_manager_class", lambda _path: Project)
    monkeypatch.setattr(async_tasks, "get_search_backend", lambda: object())
    monkeypatch.setattr(
        SearchIndexer,
        "__init__",
        lambda self, backend: setattr(self, "backend", backend),
    )
    monkeypatch.setattr(
        SearchIndexer,
        "delete_documents",
        lambda _self, targets: deleted.extend(targets),
        raising=False,
    )

    async_tasks.delete_documents_task(
        "tests.Project",
        [{"index_name": "global", "document_id": "custom-1"}],
    )

    assert len(deleted) == 1
    assert deleted[0].document_id == "custom-1"


def test_dispatch_index_update_keeps_two_argument_async_shape_for_all_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy all-index enqueue shape remains unchanged."""
    task = _DummyTask()
    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", True)
    monkeypatch.setattr(async_tasks, "_async_enabled", lambda: True)
    monkeypatch.setattr(async_tasks, "index_instance_task", task)

    async_tasks.dispatch_index_update(
        action="index",
        manager_path="tests.Project",
        identification={"id": 1},
    )

    assert task.calls == [("tests.Project", {"id": 1})]


def test_dispatch_index_update_serializes_named_index_for_async_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact-pair dispatch includes the index name and no manager instance."""
    calls: list[tuple[object, ...]] = []

    class NamedIndexTask:
        def delay(self, *args: object) -> None:
            calls.append(args)

    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", True)
    monkeypatch.setattr(async_tasks, "_async_enabled", lambda: True)
    monkeypatch.setattr(async_tasks, "index_instance_task", NamedIndexTask())

    async_tasks.dispatch_index_update(
        action="index",
        manager_path="tests.Project",
        identification={"id": 1},
        index_name="global",
    )

    assert calls == [("tests.Project", {"id": 1}, "global")]


def test_dispatch_delete_documents_preserves_legacy_two_argument_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unfenced external delete callers keep the original task call shape."""
    task = _DummyTask()
    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", True)
    monkeypatch.setattr(async_tasks, "_async_enabled", lambda: True)
    monkeypatch.setattr(async_tasks, "delete_documents_task", task)

    async_tasks.dispatch_delete_documents(
        "tests.Project",
        [{"index_name": "global", "document_id": "one"}],
    )

    assert task.calls == [
        (
            "tests.Project",
            [{"index_name": "global", "document_id": "one"}],
        )
    ]


def test_dispatch_delete_documents_strict_async_requires_every_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifecycle async deletes reject missing fences before broker enqueue."""
    task = _DummyTask()
    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", True)
    monkeypatch.setattr(async_tasks, "_async_enabled", lambda: True)
    monkeypatch.setattr(async_tasks, "delete_documents_task", task)

    with pytest.raises(async_tasks.MissingSearchDeleteGenerationFenceError):
        async_tasks.dispatch_delete_documents(
            "tests.Project",
            [{"index_name": "global", "document_id": "one"}],
            require_generation_fence=True,
        )

    assert task.calls == []


def test_dispatch_delete_documents_strict_sync_keeps_unfenced_immediate_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inline lifecycle deletion may proceed when durable marking is unavailable."""
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", False)
    monkeypatch.setattr(
        async_tasks,
        "delete_documents_task",
        lambda *args: calls.append(args),
    )

    async_tasks.dispatch_delete_documents(
        "tests.Project",
        [{"index_name": "global", "document_id": "one"}],
        require_generation_fence=True,
    )

    assert calls == [
        (
            "tests.Project",
            [{"index_name": "global", "document_id": "one"}],
        )
    ]


def test_named_index_worker_failure_redirties_exact_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An accepted exact-pair job restores its dirty marker on worker failure."""
    from tests.unit.test_search_indexer import Project

    marked: list[tuple[object, str]] = []
    monkeypatch.setattr(async_tasks, "_resolve_manager_class", lambda _path: Project)
    monkeypatch.setattr(async_tasks, "get_search_backend", lambda: object())
    monkeypatch.setattr(
        SearchIndexer,
        "index_instance_index",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("backend down")),
    )
    monkeypatch.setattr(
        "general_manager.search.reconciliation.mark_search_index_dirty",
        lambda manager_class, index_name: marked.append((manager_class, index_name)),
    )

    with pytest.raises(RuntimeError, match="backend down"):
        async_tasks.index_instance_task("tests.Project", {"id": 1}, "global")

    assert marked == [(Project, "global")]


def test_index_manager_index_batch_task_passes_exact_payload_and_returns_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The batch worker resolves one manager and forwards exact batch metadata."""
    from tests.unit.test_search_indexer import Project

    calls: list[tuple[object, str, object]] = []
    monkeypatch.setattr(async_tasks, "_resolve_manager_class", lambda _path: Project)
    monkeypatch.setattr(async_tasks, "get_search_backend", lambda: object())
    monkeypatch.setattr(
        SearchIndexer,
        "index_manager_index_batch",
        lambda _self, manager, index_name, identifications: (
            calls.append((manager, index_name, identifications)) or 2
        ),
        raising=False,
    )

    result = async_tasks.index_manager_index_batch_task(
        "tests.Project", "global", [{"id": 1}, {"id": 2}]
    )

    assert result == 2
    assert calls == [(Project, "global", [{"id": 1}, {"id": 2}])]


def test_dispatch_index_manager_batch_async_copies_payload_and_returns_accepted_len(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async dispatch serializes list/dicts and reports broker-accepted item count."""
    calls: list[tuple[object, ...]] = []

    class BatchTask:
        def delay(self, *args: object) -> None:
            calls.append(args)

    first = {"id": 1}
    identifications = (first, {"id": 1})
    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", True)
    monkeypatch.setattr(async_tasks, "_async_enabled", lambda: True)
    monkeypatch.setattr(async_tasks, "index_manager_index_batch_task", BatchTask())

    result = async_tasks.dispatch_index_manager_batch(
        "tests.Project", "global", identifications
    )
    first["id"] = 99

    assert result == 2
    assert calls == [("tests.Project", "global", [{"id": 1}, {"id": 1}])]


def test_dispatch_index_manager_batch_sync_returns_actual_unique_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inline dispatch returns the worker's actual deduplicated count."""
    calls: list[tuple[object, ...]] = []

    def task(*args: object) -> int:
        calls.append(args)
        return 1

    monkeypatch.setattr(async_tasks, "CELERY_AVAILABLE", False)
    monkeypatch.setattr(async_tasks, "index_manager_index_batch_task", task)

    result = async_tasks.dispatch_index_manager_batch(
        "tests.Project", "global", ({"id": 1}, {"id": 1})
    )

    assert result == 1
    assert calls == [("tests.Project", "global", [{"id": 1}, {"id": 1}])]


def test_index_manager_index_batch_worker_failure_redirties_exact_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch worker failures redirty their exact manager/index pair and re-raise."""
    from tests.unit.test_search_indexer import Project

    marked: list[tuple[object, str]] = []
    monkeypatch.setattr(async_tasks, "_resolve_manager_class", lambda _path: Project)
    monkeypatch.setattr(async_tasks, "get_search_backend", lambda: object())
    monkeypatch.setattr(
        SearchIndexer,
        "index_manager_index_batch",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("backend down")),
        raising=False,
    )
    monkeypatch.setattr(
        "general_manager.search.reconciliation.mark_search_index_dirty",
        lambda manager_class, index_name: marked.append((manager_class, index_name)),
    )

    with pytest.raises(RuntimeError, match="backend down"):
        async_tasks.index_manager_index_batch_task(
            "tests.Project", "global", [{"id": 1}]
        )

    assert marked == [(Project, "global")]


def test_index_manager_index_batch_worker_success_does_not_redirty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful batch workers do not touch reconciliation dirty state."""
    from tests.unit.test_search_indexer import Project

    marked: list[tuple[object, str]] = []
    monkeypatch.setattr(async_tasks, "_resolve_manager_class", lambda _path: Project)
    monkeypatch.setattr(async_tasks, "get_search_backend", lambda: object())
    monkeypatch.setattr(
        SearchIndexer,
        "index_manager_index_batch",
        lambda *_args: 1,
        raising=False,
    )
    monkeypatch.setattr(
        "general_manager.search.reconciliation.mark_search_index_dirty",
        lambda manager_class, index_name: marked.append((manager_class, index_name)),
    )

    assert (
        async_tasks.index_manager_index_batch_task(
            "tests.Project", "global", [{"id": 1}]
        )
        == 1
    )
    assert marked == []


def test_index_manager_index_batch_task_rejects_malformed_manager_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch workers preserve strict manager-path resolution behavior."""
    monkeypatch.setattr(async_tasks, "import_string", lambda _path: object())

    with pytest.raises(async_tasks.InvalidSearchManagerPathError):
        async_tasks.index_manager_index_batch_task(
            "tests.NotManager", "global", [{"id": 1}]
        )


def test_delete_worker_failure_redirties_every_captured_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete worker errors restore all exact manager/index dirty markers."""
    from tests.unit.test_search_indexer import Project

    marked: list[tuple[object, str]] = []
    monkeypatch.setattr(async_tasks, "_resolve_manager_class", lambda _path: Project)
    monkeypatch.setattr(async_tasks, "get_search_backend", lambda: object())
    monkeypatch.setattr(
        SearchIndexer,
        "delete_documents",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("backend down")),
    )
    monkeypatch.setattr(
        "general_manager.search.reconciliation.mark_search_index_dirty",
        lambda manager_class, index_name: marked.append((manager_class, index_name)),
    )

    with pytest.raises(RuntimeError, match="backend down"):
        async_tasks.delete_documents_task(
            "tests.Project",
            [
                {"index_name": "global", "document_id": "one"},
                {"index_name": "private", "document_id": "one"},
            ],
        )

    assert marked == [(Project, "global"), (Project, "private")]


class SearchDeleteGenerationFenceTests(TestCase):
    """Exercise durable generation fencing around immutable delete workers."""

    def setUp(self) -> None:
        """Create one clean durable state row and an indexed project."""
        from general_manager.apps import GeneralmanagerConfig
        from general_manager.search.backends.dev import DevSearchBackend
        from general_manager.search.models import SearchIndexState
        from general_manager.search.reconciliation import (
            build_search_schema_fingerprint,
        )
        from general_manager.search.utils import build_document_id
        from tests.unit.test_search_indexer import Project

        GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])
        self.Project = Project
        self.backend = DevSearchBackend()
        self.manager_path = f"{Project.__module__}.{Project.__name__}"
        index_config = Project.SearchConfig.indexes[0]
        SearchIndexState.objects.create(
            manager_path=self.manager_path,
            index_name="global",
            schema_fingerprint=build_search_schema_fingerprint(Project, index_config),
        )
        self.target = {
            "index_name": "global",
            "document_id": build_document_id("Project", {"id": 1}),
        }
        SearchIndexer(self.backend).index_instance(Project(id=1))

    def _mark_and_ack(self):
        """Simulate lifecycle enqueue acceptance for one generation."""
        from general_manager.search.reconciliation import (
            acknowledge_search_index_dirty,
            mark_search_index_dirty,
        )

        token = mark_search_index_dirty(self.Project, "global")
        assert token is not None
        assert acknowledge_search_index_dirty(token) is True
        return token

    def test_old_delete_skips_newer_recreated_generation(self) -> None:
        """A redelivered old delete cannot remove a newer recreated document."""
        from general_manager.search.models import SearchIndexState

        old_token = self._mark_and_ack()
        newer_token = self._mark_and_ack()
        SearchIndexer(self.backend).index_instance(self.Project(id=1))

        with patch.object(async_tasks, "get_search_backend", return_value=self.backend):
            async_tasks.delete_documents_task(
                self.manager_path,
                [self.target],
                {"global": old_token.generation},
            )

        assert self.backend.list_document_ids("global") == {self.target["document_id"]}
        state = SearchIndexState.objects.get(
            manager_path=self.manager_path, index_name="global"
        )
        assert state.dirty_generation > newer_token.generation
        assert state.dirty_since is not None

    def test_batch_import_failure_redirties_existing_path_pair(self) -> None:
        """An accepted batch is recovered even when its manager import later fails."""
        from general_manager.search.models import SearchIndexState

        acknowledged = self._mark_and_ack()
        clean_state = SearchIndexState.objects.get(
            manager_path=self.manager_path,
            index_name="global",
        )
        assert clean_state.dirty_since is None

        with (
            patch.object(
                async_tasks,
                "import_string",
                side_effect=ImportError("manager module removed"),
            ),
            pytest.raises(ImportError, match="manager module removed"),
        ):
            async_tasks.index_manager_index_batch_task(
                self.manager_path,
                "global",
                [{"id": 1}],
            )

        recovered = SearchIndexState.objects.get(
            manager_path=self.manager_path,
            index_name="global",
        )
        assert recovered.dirty_since is not None
        assert recovered.dirty_reason == "data_changed"
        assert recovered.dirty_generation > acknowledged.generation

    def test_delete_import_failure_redirties_every_existing_path_pair(self) -> None:
        """An accepted delete is recovered by path for every captured index."""
        from general_manager.search.models import SearchIndexState
        from general_manager.search.reconciliation import (
            DirtySearchIndex,
            acknowledge_search_index_dirty,
        )

        global_token = self._mark_and_ack()
        private_state = SearchIndexState.objects.create(
            manager_path=self.manager_path,
            index_name="private",
            schema_fingerprint="private-schema",
        )
        private_state.mark_dirty("data_changed")
        private_token = DirtySearchIndex(
            state_id=private_state.pk,
            manager_path=self.manager_path,
            index_name="private",
            generation=private_state.dirty_generation,
            acknowledgeable=True,
        )
        assert acknowledge_search_index_dirty(private_token) is True

        with (
            patch.object(
                async_tasks,
                "import_string",
                side_effect=ImportError("manager module removed"),
            ),
            pytest.raises(ImportError, match="manager module removed"),
        ):
            async_tasks.delete_documents_task(
                self.manager_path,
                [
                    {"index_name": "global", "document_id": "one"},
                    {"index_name": "private", "document_id": "two"},
                    {"index_name": "global", "document_id": "three"},
                ],
                {
                    "global": global_token.generation,
                    "private": private_token.generation,
                },
            )

        recovered = {
            state.index_name: state
            for state in SearchIndexState.objects.filter(
                manager_path=self.manager_path,
                index_name__in=("global", "private"),
            )
        }
        assert recovered["global"].dirty_since is not None
        assert recovered["private"].dirty_since is not None
        assert recovered["global"].dirty_generation == global_token.generation + 1
        assert recovered["private"].dirty_generation == private_token.generation + 1

    def test_batch_import_failure_preserves_stronger_existing_dirty_reason(
        self,
    ) -> None:
        """Path recovery advances generation without weakening pending work."""
        from general_manager.search.models import (
            SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED,
            SearchIndexState,
        )

        state = SearchIndexState.objects.get(
            manager_path=self.manager_path,
            index_name="global",
        )
        state.mark_dirty(SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED)
        original_dirty_since = state.dirty_since
        original_generation = state.dirty_generation

        with (
            patch.object(
                async_tasks,
                "import_string",
                side_effect=ImportError("manager module removed"),
            ),
            pytest.raises(ImportError, match="manager module removed"),
        ):
            async_tasks.index_manager_index_batch_task(
                self.manager_path,
                "global",
                [{"id": 1}],
            )

        state.refresh_from_db()
        assert state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED
        assert state.dirty_since == original_dirty_since
        assert state.dirty_generation == original_generation + 1

    def test_batch_import_failure_does_not_create_missing_path_state(self) -> None:
        """Path-only recovery never invents schema state for an unknown pair."""
        from general_manager.search.models import SearchIndexState

        missing_path = "removed.module.Manager"
        original_count = SearchIndexState.objects.count()

        with (
            patch.object(
                async_tasks,
                "import_string",
                side_effect=ImportError("manager module removed"),
            ),
            pytest.raises(ImportError, match="manager module removed"),
        ):
            async_tasks.index_manager_index_batch_task(
                missing_path,
                "global",
                [{"id": 1}],
            )

        assert SearchIndexState.objects.count() == original_count
        assert not SearchIndexState.objects.filter(
            manager_path=missing_path,
            index_name="global",
        ).exists()

    def test_skipped_old_delete_redirties_after_unrelated_pair_mutation(self) -> None:
        """Pair-wide generation changes retain fallback cleanup for stale A."""
        from general_manager.search.models import SearchIndexState
        from general_manager.search.utils import build_document_id

        old_token = self._mark_and_ack()
        newer_token = self._mark_and_ack()
        SearchIndexer(self.backend).index_instance(self.Project(id=2))

        with patch.object(async_tasks, "get_search_backend", return_value=self.backend):
            async_tasks.delete_documents_task(
                self.manager_path,
                [self.target],
                {"global": old_token.generation},
            )

        assert self.backend.list_document_ids("global") == {
            self.target["document_id"],
            build_document_id("Project", {"id": 2}),
        }
        state = SearchIndexState.objects.get(
            manager_path=self.manager_path, index_name="global"
        )
        assert state.dirty_generation > newer_token.generation
        assert state.dirty_since is not None

    def test_generation_change_during_delete_redirties_after_backend_work(
        self,
    ) -> None:
        """A concurrent recreate forces reconciliation after backend deletion."""
        from general_manager.search.models import SearchIndexState

        old_token = self._mark_and_ack()
        original_delete = self.backend.delete
        concurrent_generations: list[int] = []

        def racing_delete(index_name: str, ids: list[str]) -> None:
            original_delete(index_name, ids)
            newer_token = self._mark_and_ack()
            concurrent_generations.append(newer_token.generation)

        with (
            patch.object(self.backend, "delete", side_effect=racing_delete),
            patch.object(async_tasks, "get_search_backend", return_value=self.backend),
        ):
            async_tasks.delete_documents_task(
                self.manager_path,
                [self.target],
                {"global": old_token.generation},
            )

        state = SearchIndexState.objects.get(
            manager_path=self.manager_path, index_name="global"
        )
        assert state.dirty_since is not None
        assert state.dirty_generation > concurrent_generations[0]
