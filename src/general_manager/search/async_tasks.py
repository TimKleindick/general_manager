"""Async indexing helpers using Celery when available."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
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


class InvalidSearchIndexActionError(ValueError):
    """Raised when async search indexing receives an unsupported action."""

    def __init__(self, action: str) -> None:
        """Build the validation error message."""
        super().__init__(f"Unsupported search index action: {action}")


class InvalidSearchManagerPathError(TypeError):
    """Raised when a manager import path does not resolve to a manager class."""

    def __init__(self) -> None:
        """Build the validation error message."""
        super().__init__("manager_path must resolve to a GeneralManager subclass")


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


def _resolve_manager_class(manager_path: str) -> type[GeneralManager]:
    """
    Resolve a dotted Python import path to a GeneralManager subclass.

    Import errors from Django's `import_string(...)` propagate. The returned
    object must be a `GeneralManager` subclass; constructor errors propagate
    from task execution.
    """
    manager_class = import_string(manager_path)
    if not isinstance(manager_class, type) or not issubclass(
        manager_class, GeneralManager
    ):
        raise InvalidSearchManagerPathError()
    return manager_class


def _resolve_manager(manager_path: str) -> type[GeneralManager]:
    """Backward-compatible alias for strict manager-class resolution."""
    return _resolve_manager_class(manager_path)


def _mark_worker_failure(
    manager_class: type[GeneralManager],
    index_names: Sequence[str],
) -> None:
    """Best-effort redirty exact pairs after accepted work fails in a worker."""
    from general_manager.search.reconciliation import mark_search_index_dirty

    for index_name in dict.fromkeys(index_names):
        try:
            mark_search_index_dirty(manager_class, index_name)
        except Exception:
            logger.exception(
                "search worker failed to restore dirty state",
                context={
                    "manager": manager_class.__name__,
                    "index": index_name,
                },
            )


def _mark_worker_path_failure(manager_path: str, index_name: str) -> None:
    """Best-effort redirty when worker manager resolution itself fails."""
    from general_manager.search.reconciliation import (
        mark_existing_search_index_dirty,
    )

    try:
        restored = mark_existing_search_index_dirty(manager_path, index_name)
    except Exception:
        logger.exception(
            "search worker failed to restore dirty state by path",
            context={"manager_path": manager_path, "index": index_name},
        )
        return
    if not restored:
        logger.warning(
            "search worker found no durable state to restore by path",
            context={"manager_path": manager_path, "index": index_name},
        )


def _configured_index_names(manager_class: type[GeneralManager]) -> tuple[str, ...]:
    """Return configured index names for worker recovery metadata."""
    from general_manager.search.registry import get_search_config

    config = get_search_config(manager_class)
    if config is None:
        return ()
    return tuple(dict.fromkeys(index.name for index in config.indexes))


def _generation_matches(
    manager_path: str,
    index_name: str,
    expected_generation: int,
) -> bool:
    """Check one lifecycle generation without holding a lock across backend I/O."""
    from general_manager.search.models import SearchIndexState

    return SearchIndexState.objects.filter(
        manager_path=manager_path,
        index_name=index_name,
        dirty_generation=expected_generation,
    ).exists()


@shared_task
def index_instance_task(
    manager_path: str,
    identification: SearchIdentification,
    index_name: str | None = None,
) -> None:
    """
    Index the instance represented by the given manager path and identification in the configured search backend.

    `manager_path` must resolve to a callable manager class. `identification`
    supplies keyword arguments for that class and must contain values accepted by
    the active Celery serializer when queued. Import, construction, backend
    lookup, and indexing errors propagate to the Celery worker or direct caller.
    """
    manager_class = _resolve_manager_class(manager_path)
    affected_indexes = (
        (index_name,)
        if index_name is not None
        else _configured_index_names(manager_class)
    )
    try:
        instance = manager_class(**identification)
        from general_manager.search.indexer import SearchIndexer

        indexer = SearchIndexer(get_search_backend())
        if index_name is None:
            indexer.index_instance(instance)
        else:
            indexer.index_instance_index(instance, index_name)
    except Exception:
        _mark_worker_failure(manager_class, affected_indexes)
        raise


@shared_task
def index_manager_index_batch_task(
    manager_path: str,
    index_name: str,
    identifications: Sequence[dict[str, object]],
) -> int:
    """Index a serialized identity batch for one exact manager/index pair."""
    try:
        manager_class = _resolve_manager_class(manager_path)
    except Exception:
        _mark_worker_path_failure(manager_path, index_name)
        raise
    try:
        from general_manager.search.indexer import SearchIndexer

        return SearchIndexer(get_search_backend()).index_manager_index_batch(
            manager_class,
            index_name,
            identifications,
        )
    except Exception:
        _mark_worker_failure(manager_class, (index_name,))
        raise


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
    manager_class = _resolve_manager_class(manager_path)
    instance = manager_class(**identification)
    from general_manager.search.indexer import SearchIndexer

    SearchIndexer(get_search_backend()).delete_instance(instance)


@shared_task
def delete_documents_task(
    manager_path: str,
    targets: Sequence[dict[str, str]],
    expected_generations: Mapping[str, int] | None = None,
) -> None:
    """Delete captured document IDs without reconstructing the deleted row."""
    manager_class = _resolve_manager_class(manager_path)
    index_names = tuple(target["index_name"] for target in targets)
    try:
        from general_manager.search.indexer import SearchDeleteTarget, SearchIndexer

        delete_targets: list[SearchDeleteTarget] = []
        skipped_indexes: list[str] = []
        for target in targets:
            index_name = target["index_name"]
            expected_generation = (
                expected_generations.get(index_name)
                if expected_generations is not None
                else None
            )
            if expected_generation is not None and not _generation_matches(
                manager_path, index_name, expected_generation
            ):
                skipped_indexes.append(index_name)
                continue
            delete_targets.append(
                SearchDeleteTarget(
                    manager_class=manager_class,
                    manager_path=manager_path,
                    index_name=index_name,
                    document_id=target["document_id"],
                )
            )
        if skipped_indexes:
            _mark_worker_failure(manager_class, skipped_indexes)
        if not delete_targets:
            return
        SearchIndexer(get_search_backend()).delete_documents(delete_targets)
        if expected_generations is not None:
            checked_indexes: set[str] = set()
            for delete_target in delete_targets:
                index_name = delete_target.index_name
                if index_name in checked_indexes:
                    continue
                checked_indexes.add(index_name)
                expected_generation = expected_generations.get(index_name)
                if expected_generation is None or _generation_matches(
                    manager_path,
                    index_name,
                    expected_generation,
                ):
                    continue
                _mark_worker_failure(manager_class, (index_name,))
    except Exception:
        _mark_worker_failure(manager_class, index_names)
        raise


def dispatch_index_update(
    *,
    action: SearchIndexAction,
    manager_path: str,
    identification: SearchIdentification,
    instance: GeneralManager | None = None,
    index_name: str | None = None,
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
        elif index_name is not None:
            index_instance_task.delay(manager_path, identification, index_name)
        else:
            index_instance_task.delay(manager_path, identification)
        return

    if instance is not None:
        from general_manager.search.indexer import SearchIndexer

        indexer = SearchIndexer(get_search_backend())
        if action == "delete":
            indexer.delete_instance(instance)
        elif index_name is not None:
            indexer.index_instance_index(instance, index_name)
        else:
            indexer.index_instance(instance)
        return

    if action == "delete":
        delete_instance_task(manager_path, identification)
    elif index_name is not None:
        index_instance_task(manager_path, identification, index_name)
    else:
        index_instance_task(manager_path, identification)


def dispatch_index_manager_batch(
    manager_path: str,
    index_name: str,
    identifications: Sequence[Mapping[str, object]],
) -> int:
    """Dispatch one exact manager/index identity batch inline or via Celery."""
    serialized_identifications = [
        dict(identification) for identification in identifications
    ]
    if _async_enabled() and CELERY_AVAILABLE:
        index_manager_index_batch_task.delay(
            manager_path,
            index_name,
            serialized_identifications,
        )
        return len(serialized_identifications)
    return index_manager_index_batch_task(
        manager_path,
        index_name,
        serialized_identifications,
    )


def dispatch_delete_documents(
    manager_path: str,
    targets: Sequence[dict[str, str]],
    expected_generations: Mapping[str, int] | None = None,
) -> None:
    """Dispatch captured direct-delete targets inline or through Celery."""
    serialized_targets = [dict(target) for target in targets]
    if _async_enabled() and CELERY_AVAILABLE:
        if expected_generations is None:
            delete_documents_task.delay(manager_path, serialized_targets)
        else:
            delete_documents_task.delay(
                manager_path,
                serialized_targets,
                dict(expected_generations),
            )
        return
    if expected_generations is None:
        delete_documents_task(manager_path, serialized_targets)
    else:
        delete_documents_task(
            manager_path,
            serialized_targets,
            dict(expected_generations),
        )


def _validate_action(action: str) -> None:
    """Validate the public dispatch action value."""
    if action not in {"index", "delete"}:
        raise InvalidSearchIndexActionError(action)
