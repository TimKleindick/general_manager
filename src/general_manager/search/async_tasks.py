"""Async indexing helpers using Celery when available."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal, ParamSpec, Protocol, TypeVar, cast, overload

from django.utils.module_loading import import_string

from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.search.backend_registry import get_search_backend

logger = get_logger("search.async")
P = ParamSpec("P")
R = TypeVar("R")
R_co = TypeVar("R_co", covariant=True)
SearchIndexAction = Literal["index", "delete"]
SearchIdentification = Mapping[str, object]


class _TaskCallable(Protocol[P, R_co]):
    """Callable task wrapper returned by Celery's shared_task decorator."""

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R_co: ...

    def delay(self, *args: P.args, **kwargs: P.kwargs) -> object: ...


class _RawSharedTask(Protocol):
    """Runtime callable shape of Celery's shared_task decorator."""

    def __call__(self, func: object = None, **kwargs: object) -> object: ...


class _ManagerFactory(Protocol):
    """Callable manager class surface required by indexing tasks."""

    def __call__(self, **kwargs: object) -> GeneralManager: ...


class InvalidSearchIndexActionError(ValueError):
    """Raised when async search indexing receives an unsupported action."""

    def __init__(self, action: str) -> None:
        """Build the validation error message."""
        super().__init__(f"Unsupported search index action: {action}")


class InvalidSearchManagerPathError(TypeError):
    """Raised when a manager import path resolves to a non-callable object."""

    def __init__(self) -> None:
        """Build the validation error message."""
        super().__init__("manager_path must resolve to a callable manager")


_raw_shared_task: object | None = None

try:
    from celery import shared_task as _celery_shared_task

    CELERY_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional dependency
    CELERY_AVAILABLE = False
else:
    _raw_shared_task = _celery_shared_task


@overload
def shared_task(
    func: Callable[P, R],
    **kwargs: object,
) -> _TaskCallable[P, R]: ...


@overload
def shared_task(
    func: None = None,
    **kwargs: object,
) -> Callable[[Callable[P, R]], _TaskCallable[P, R]]: ...


def shared_task(
    func: Callable[P, R] | None = None,
    **kwargs: object,
) -> Callable[[Callable[P, R]], _TaskCallable[P, R]] | _TaskCallable[P, R]:
    """Return Celery's task decorator or a typed no-op fallback."""

    def decorator(inner: Callable[P, R]) -> _TaskCallable[P, R]:
        """Return the wrapped callable unchanged."""
        return cast(_TaskCallable[P, R], inner)

    if _raw_shared_task is None:
        if func is None:
            return decorator
        return decorator(func)
    celery_shared_task = cast(_RawSharedTask, _raw_shared_task)
    if func is None:
        return cast(
            Callable[[Callable[P, R]], _TaskCallable[P, R]],
            celery_shared_task(**kwargs),
        )
    return cast(_TaskCallable[P, R], celery_shared_task(func, **kwargs))


def _async_enabled() -> bool:
    """
    Return whether search index updates should be queued through Celery.

    `GENERAL_MANAGER["SEARCH_ASYNC"]` takes precedence over the top-level
    `SEARCH_ASYNC` setting through GeneralManager's settings resolver. Missing
    values default to `False`; configured values use normal Python truthiness.
    """
    from general_manager.conf import get_setting

    return bool(get_setting("SEARCH_ASYNC", False))


def _resolve_manager(manager_path: str) -> _ManagerFactory:
    """
    Resolve a dotted Python import path to an importable manager class/callable.

    Import errors from Django's `import_string(...)` propagate. The returned
    object must be callable with identification keyword arguments; constructor
    errors propagate from task execution.
    """
    manager_class = import_string(manager_path)
    if not callable(manager_class):
        raise InvalidSearchManagerPathError()
    return cast(_ManagerFactory, manager_class)


@shared_task
def index_instance_task(
    manager_path: str, identification: SearchIdentification
) -> None:
    """
    Index the instance represented by the given manager path and identification in the configured search backend.

    `manager_path` must resolve to a callable manager class. `identification`
    supplies keyword arguments for that class and must contain values accepted by
    the active Celery serializer when queued. Import, construction, backend
    lookup, and indexing errors propagate to the Celery worker or direct caller.
    """
    manager_class = _resolve_manager(manager_path)
    instance = manager_class(**identification)
    from general_manager.search.indexer import SearchIndexer

    SearchIndexer(get_search_backend()).index_instance(instance)


@shared_task
def delete_instance_task(
    manager_path: str, identification: SearchIdentification
) -> None:
    """
    Remove the search index document for an instance identified by a manager path and identification data.

    `manager_path` must resolve to a callable manager class. `identification`
    supplies keyword arguments for that class and must contain values accepted by
    the active Celery serializer when queued. Import, construction, backend
    lookup, and deletion errors propagate to the Celery worker or direct caller.
    """
    manager_class = _resolve_manager(manager_path)
    instance = manager_class(**identification)
    from general_manager.search.indexer import SearchIndexer

    SearchIndexer(get_search_backend()).delete_instance(instance)


def dispatch_index_update(
    *,
    action: SearchIndexAction,
    manager_path: str,
    identification: SearchIdentification,
    instance: GeneralManager | None = None,
) -> None:
    """
    Dispatch one search index update or delete operation.

    `action` must be `"index"` or `"delete"`. When async indexing is enabled and
    Celery is available, a Celery task is enqueued and any provided `instance` is
    ignored. Otherwise, an explicit `instance` runs inline against the current
    backend. When no instance is supplied, the task function is called
    synchronously and reconstructs the manager from `manager_path` and
    `identification`. Task enqueue, import, construction, backend, and indexer
    errors propagate.
    """
    _validate_action(action)
    if _async_enabled() and CELERY_AVAILABLE:
        if action == "delete":
            delete_instance_task.delay(manager_path, identification)
        else:
            index_instance_task.delay(manager_path, identification)
        return

    if instance is not None:
        from general_manager.search.indexer import SearchIndexer

        indexer = SearchIndexer(get_search_backend())
        if action == "delete":
            indexer.delete_instance(instance)
        else:
            indexer.index_instance(instance)
        return

    if action == "delete":
        delete_instance_task(manager_path, identification)
    else:
        index_instance_task(manager_path, identification)


def _validate_action(action: str) -> None:
    """Validate the public dispatch action value."""
    if action not in {"index", "delete"}:
        raise InvalidSearchIndexActionError(action)
