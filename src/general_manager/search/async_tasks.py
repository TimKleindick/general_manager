"""Async indexing helpers using Celery when available."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string

from general_manager.logging import get_logger
from general_manager.search.backend_registry import get_search_backend

logger = get_logger("search.async")

try:
    from celery import shared_task

    CELERY_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional dependency
    CELERY_AVAILABLE = False

    def shared_task(func: Any | None = None, **_kwargs: Any):  # type: ignore[no-redef]
        """
        A no-op decorator compatible with Celery's `shared_task` that returns the original function unchanged.
        
        Parameters:
            func (callable | None): The function being decorated when used as `@shared_task`. If `None`, the function is being called with keyword arguments and this returns a decorator.
            **_kwargs: Any:
                Keyword arguments accepted for compatibility with Celery's `shared_task` but ignored.
        
        Returns:
            callable: If `func` is provided, returns `func` unchanged; otherwise returns a decorator that returns its input function unchanged.
        """
        def decorator(inner):
            return inner

        if func is None:
            return decorator
        return decorator(func)


def _async_enabled() -> bool:
    """
    Determine whether asynchronous indexing is enabled via Django settings.
    
    Checks GENERAL_MANAGER['SEARCH_ASYNC'] first, falling back to the top-level SEARCH_ASYNC setting.
    
    Returns:
        bool: `True` if GENERAL_MANAGER.SEARCH_ASYNC or SEARCH_ASYNC is truthy, `False` otherwise.
    """
    config = getattr(settings, "GENERAL_MANAGER", {})
    return bool(
        config.get("SEARCH_ASYNC", False) or getattr(settings, "SEARCH_ASYNC", False)
    )


def _resolve_manager(manager_path: str):
    """
    Resolve a dotted Python import path to the referenced manager object.
    
    Parameters:
    	manager_path (str): Dotted import path pointing to the manager (e.g., "myapp.managers.MyManager").
    
    Returns:
    	The object referenced by `manager_path` (typically a manager class or callable).
    """
    return import_string(manager_path)


@shared_task
def index_instance_task(manager_path: str, identification: dict[str, Any]) -> None:
    """
    Index the instance represented by the given manager path and identification in the configured search backend.
    
    Parameters:
        manager_path (str): Dotted import path to the manager/class used to construct the instance.
        identification (dict[str, Any]): Mapping of attributes used to instantiate or identify the target instance.
    """
    manager_class = _resolve_manager(manager_path)
    instance = manager_class(**identification)
    from general_manager.search.indexer import SearchIndexer

    SearchIndexer(get_search_backend()).index_instance(instance)


@shared_task
def delete_instance_task(manager_path: str, identification: dict[str, Any]) -> None:
    """
    Remove the search index document for an instance identified by a manager path and identification data.
    
    Parameters:
        manager_path (str): Dotted import path to the manager class used to construct the target instance.
        identification (dict[str, Any]): Constructor keyword arguments that identify the instance to be deleted from the index.
    """
    manager_class = _resolve_manager(manager_path)
    instance = manager_class(**identification)
    from general_manager.search.indexer import SearchIndexer

    SearchIndexer(get_search_backend()).delete_instance(instance)


def dispatch_index_update(
    *,
    action: str,
    manager_path: str,
    identification: dict[str, Any],
    instance: Any | None = None,
) -> None:
    """
    Dispatches an index or delete operation either asynchronously or inline based on configuration.
    
    When asynchronous updates are enabled and Celery is available, enqueues a task to perform the action.
    If an actual model instance is provided, performs the action immediately using SearchIndexer and the current backend.
    If neither asynchronous execution nor an instance is used, invokes the task function synchronously (inline).
    
    Parameters:
    	action (str): Either "delete" to remove the instance from the index or any other value to index/update it.
    	manager_path (str): Python import path to the manager class used to resolve the instance when running tasks.
    	identification (dict[str, Any]): Mapping used by the manager to locate or identify the instance (e.g., primary key fields).
    	instance (Any | None): Optional in-memory instance; if provided, the operation is executed directly against it.
    """
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