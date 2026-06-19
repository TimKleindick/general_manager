"""Celery-compatible tasks for GraphQL property warm-up."""

from __future__ import annotations

from typing import Any, Iterable, cast

from django.conf import settings
from django.utils.module_loading import import_string

from general_manager.api.graphql_warmup import (
    refresh_due_graphql_warmup_recipes,
    warm_up_graphql_properties,
    warm_up_graphql_recipe,
)
from general_manager.logging import get_logger

logger = get_logger("api.graphql_warmup_tasks")

try:
    from celery import current_app, shared_task

    CELERY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency boundary
    CELERY_AVAILABLE = False
    current_app = cast(Any | None, None)  # type: ignore[assignment, no-redef]

    def shared_task(func: Any | None = None, **_kwargs: Any):  # type: ignore[no-redef]
        """Return a no-op task decorator when Celery is not installed."""

        def decorator(inner):
            """Return the wrapped function unchanged."""
            return inner

        if func is None:
            return decorator
        return decorator(func)


GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY = "general_manager.graphql_warmup.refresh_due"
GRAPHQL_WARMUP_QUEUE = "graphql.warmup"
FALSE_SETTING_STRINGS = {"", "0", "false", "no", "off", "none", "null"}
TRUE_SETTING_STRINGS = {"1", "true", "yes", "on"}


def configure_graphql_warmup_beat_schedule_from_settings(
    django_settings: Any = settings,
) -> bool:
    """Register the periodic timeout refresh task in Celery Beat."""
    if not _bool_setting(django_settings, "GRAPHQL_WARMUP_BEAT_ENABLED", False):
        return False
    if not CELERY_AVAILABLE or current_app is None:
        logger.warning("graphql warm-up beat schedule skipped; celery unavailable")
        return False
    schedule: dict[str, Any] = dict(
        getattr(current_app.conf, "beat_schedule", {}) or {}
    )
    interval_seconds = float(
        _int_setting(django_settings, "GRAPHQL_WARMUP_BEAT_INTERVAL_SECONDS", 60)
    )
    schedule[GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY] = {
        "task": (
            "general_manager.api.graphql_warmup_tasks."
            "refresh_due_graphql_warmup_recipes_task"
        ),
        "schedule": interval_seconds,
        "options": {"queue": GRAPHQL_WARMUP_QUEUE},
    }
    current_app.conf.beat_schedule = schedule
    logger.info(
        "graphql warm-up beat schedule configured",
        context={
            "schedule_key": GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY,
            "interval_seconds": interval_seconds,
        },
    )
    return True


@shared_task(queue=GRAPHQL_WARMUP_QUEUE)
def warm_up_graphql_properties_task(
    manager_paths: list[str] | None = None,
) -> dict[str, int]:
    """Resolve optional manager paths and run all-entry GraphQL warm-up."""
    manager_classes = None
    if manager_paths is not None:
        manager_classes = [import_string(path) for path in manager_paths]
    summary = warm_up_graphql_properties(manager_classes)
    return {
        "evaluated": summary.evaluated,
        "failed": summary.failed,
        "recipes": summary.recipes,
    }


@shared_task(queue=GRAPHQL_WARMUP_QUEUE)
def warm_up_graphql_recipes_task(cache_keys: list[str]) -> int:
    """Warm recipe-backed cache entries with per-key failure isolation."""
    warmed = 0
    for cache_key in cache_keys:
        try:
            if warm_up_graphql_recipe(cache_key):
                warmed += 1
        except Exception:
            logger.exception(
                "graphql warm-up recipe task item failed",
                context={"cache_key": cache_key},
            )
    return warmed


@shared_task(queue=GRAPHQL_WARMUP_QUEUE)
def refresh_due_graphql_warmup_recipes_task(limit: int | None = None) -> int:
    """Refresh due timeout recipes."""
    return refresh_due_graphql_warmup_recipes(limit=limit)


def dispatch_graphql_warmup(
    manager_classes: Iterable[type[Any]] | None = None,
) -> bool:
    """Dispatch all-entry warm-up to Celery when available."""
    if not CELERY_AVAILABLE:
        return False
    manager_paths = None
    if manager_classes is not None:
        manager_paths = [
            path
            for path in (
                _manager_path(manager_class) for manager_class in manager_classes
            )
            if path
        ]
        if not manager_paths:
            return False
    try:
        warm_up_graphql_properties_task.delay(manager_paths)
    except Exception:
        logger.exception("failed to enqueue graphql warm-up task")
        return False
    return True


def dispatch_graphql_recipe_warmup(cache_keys: Iterable[str]) -> bool:
    """Dispatch recipe re-warm to Celery when available."""
    keys = list(dict.fromkeys(cache_keys))
    if not (CELERY_AVAILABLE and keys):
        return False
    try:
        warm_up_graphql_recipes_task.delay(keys)
    except Exception:
        logger.exception("failed to enqueue graphql recipe warm-up task")
        return False
    return True


def _config(django_settings: Any) -> dict[str, Any]:
    """Return the nested GENERAL_MANAGER settings dictionary."""
    value = getattr(django_settings, "GENERAL_MANAGER", {})
    return dict(value) if isinstance(value, dict) else {}


def _bool_setting(django_settings: Any, key: str, default: bool) -> bool:
    """Read a boolean setting while accepting common string values."""
    config = _config(django_settings)
    raw = config.get(key, getattr(django_settings, key, default))
    if isinstance(raw, str):
        normalized = raw.strip().casefold()
        if normalized in FALSE_SETTING_STRINGS:
            return False
        if normalized in TRUE_SETTING_STRINGS:
            return True
    return bool(raw)


def _int_setting(django_settings: Any, key: str, default: int) -> int:
    """Read a positive integer setting with a fallback."""
    config = _config(django_settings)
    raw = config.get(key, getattr(django_settings, key, default))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def _manager_path(manager_class: type[Any]) -> str | None:
    """Return an import path for manager classes that workers can resolve."""
    qualname = getattr(manager_class, "__qualname__", manager_class.__name__)
    if "<locals>" in qualname:
        return None
    return f"{manager_class.__module__}.{qualname}"
