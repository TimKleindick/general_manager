from __future__ import annotations

from dataclasses import dataclass

import pytest
from django.test import override_settings

import general_manager.search.async_tasks as async_tasks
from general_manager.search.indexer import SearchIndexer


@dataclass
class _DummyInstance:
    value: int


class _DummyManager:
    def __init__(self, **kwargs):
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
    with override_settings(SEARCH_ASYNC=True):
        assert async_tasks._async_enabled() is True
    with override_settings(SEARCH_ASYNC=False):
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


def test_index_and_delete_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(async_tasks, "_resolve_manager", lambda _path: _DummyManager)

    import importlib
    import sys
    import types

    fake_celery = types.SimpleNamespace(shared_task=lambda func: func)
    sys.modules["celery"] = fake_celery
    reloaded = importlib.reload(async_tasks)
    try:
        reloaded.get_search_backend = lambda: object()
        reloaded._resolve_manager = lambda _path: _DummyManager
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
