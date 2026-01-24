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

    def shared_task(*_args: Any, **_kwargs: Any):  # type: ignore[no-redef]
        def decorator(func):
            return func

        return decorator


def _async_enabled() -> bool:
    config = getattr(settings, "GENERAL_MANAGER", {})
    return bool(
        config.get("SEARCH_ASYNC", False) or getattr(settings, "SEARCH_ASYNC", False)
    )


def _resolve_manager(manager_path: str):
    return import_string(manager_path)


@shared_task
def index_instance_task(manager_path: str, identification: dict[str, Any]) -> None:
    manager_class = _resolve_manager(manager_path)
    instance = manager_class(**identification)
    from general_manager.search.indexer import SearchIndexer

    SearchIndexer(get_search_backend()).index_instance(instance)


@shared_task
def delete_instance_task(manager_path: str, identification: dict[str, Any]) -> None:
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
    """Dispatch index updates async when configured, otherwise run inline."""
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
