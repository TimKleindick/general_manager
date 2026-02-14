"""Async GraphQL warmup helpers using Celery when available."""

from __future__ import annotations

from typing import Any, Iterable, Type

from django.conf import settings
from django.utils.module_loading import import_string

from general_manager.api.warmup import warm_up_graphql_properties, warmup_enabled
from general_manager.cache.dependency_index import Dependency
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager

logger = get_logger("api.warmup_async")

try:
    from celery import shared_task

    CELERY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    CELERY_AVAILABLE = False

    def shared_task(func: Any | None = None, **_kwargs: Any):  # type: ignore[no-redef]
        """No-op decorator fallback compatible with Celery's shared_task."""

        def decorator(inner):
            return inner

        if func is None:
            return decorator
        return decorator(func)


def _async_enabled() -> bool:
    """
    Determine whether async GraphQL warmup dispatch is enabled.

    Uses GENERAL_MANAGER["GRAPHQL_WARMUP_ASYNC"] with fallback to GRAPHQL_WARMUP_ASYNC.
    Defaults to False when not configured.
    """
    config = getattr(settings, "GENERAL_MANAGER", {})
    if isinstance(config, dict) and "GRAPHQL_WARMUP_ASYNC" in config:
        return bool(config.get("GRAPHQL_WARMUP_ASYNC"))
    return bool(getattr(settings, "GRAPHQL_WARMUP_ASYNC", False))


def _manager_path(manager_class: Type[GeneralManager]) -> str | None:
    """Return importable manager path, or None when class is not import-addressable."""
    qualname = getattr(manager_class, "__qualname__", manager_class.__name__)
    if "<locals>" in qualname:
        return None
    return f"{manager_class.__module__}.{qualname}"


@shared_task
def warm_up_graphql_properties_task(manager_paths: list[str]) -> None:
    """Celery task that imports manager classes and executes GraphQL property warmup."""
    manager_classes: list[Type[GeneralManager]] = []
    for manager_path in manager_paths:
        resolved = import_string(manager_path)
        if isinstance(resolved, type) and issubclass(resolved, GeneralManager):
            manager_classes.append(resolved)
    if manager_classes:
        warm_up_graphql_properties(manager_classes)


def dispatch_graphql_warmup(
    manager_classes: Iterable[Type[GeneralManager]],
) -> bool:
    """
    Dispatch GraphQL warmup through Celery when available and enabled.

    Returns:
        bool: True when a Celery task was enqueued, False otherwise.
    """
    if not warmup_enabled():
        return False
    if not _async_enabled() or not CELERY_AVAILABLE:
        return False
    manager_paths = [
        path
        for path in (_manager_path(manager_class) for manager_class in manager_classes)
        if path
    ]
    if not manager_paths:
        return False
    try:
        warm_up_graphql_properties_task.delay(manager_paths)
    except Exception:
        logger.exception("failed to enqueue graphql warm-up task")
        return False
    logger.info(
        "queued graphql warm-up task",
        context={"count": len(manager_paths)},
    )
    return True


def dispatch_graphql_warmup_for_dependencies(
    dependency_records: Iterable[Dependency] | None,
) -> None:
    """
    Trigger warmup for managers with ``warm_up`` properties found in dependencies.

    Parameters:
        dependency_records (Iterable[Dependency] | None): Dependency tuples associated
            with invalidated cache keys.
    """
    if not warmup_enabled():
        return
    if not dependency_records:
        return
    from general_manager.api.graphql import GraphQL

    manager_classes: list[Type[GeneralManager]] = []
    seen_names: set[str] = set()
    for manager_name, operation, _identifier in dependency_records:
        if operation != "identification":
            continue
        if manager_name in seen_names:
            continue
        manager_class = GraphQL.manager_registry.get(manager_name)
        if manager_class is None:
            continue
        interface_cls = getattr(manager_class, "Interface", None)
        if interface_cls is None:
            continue
        properties = interface_cls.get_graph_ql_properties()
        if not any(prop.warm_up for prop in properties.values()):
            continue
        seen_names.add(manager_name)
        manager_classes.append(manager_class)

    if not manager_classes:
        return

    if not dispatch_graphql_warmup(manager_classes):
        logger.info(
            "skipping dependency-triggered graphql warm-up; async dispatch unavailable",
            context={"count": len(manager_classes)},
        )
