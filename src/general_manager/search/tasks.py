"""Celery tasks and schedule setup for search reconciliation."""

from __future__ import annotations

from typing import Any, Mapping, cast

from django.conf import settings

from general_manager.logging import get_logger
from general_manager.search.reconciliation import reconcile_search_indexes

logger = get_logger("search.tasks")

try:
    from celery import current_app, shared_task

    CELERY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency boundary
    CELERY_AVAILABLE = False
    current_app = cast(Any | None, None)  # type: ignore[assignment, no-redef]

    def shared_task(func: Any | None = None, **_kwargs: Any):  # type: ignore[no-redef]
        def decorator(inner):
            return inner

        if func is None:
            return decorator
        return decorator(func)


SEARCH_RECONCILE_BEAT_SCHEDULE_KEY = "general_manager.search.reconcile"


def _config(django_settings: Any = settings) -> Mapping[str, Any]:
    value = getattr(django_settings, "GENERAL_MANAGER", {})
    if isinstance(value, Mapping):
        return value
    return {}


def search_reconcile_enabled(django_settings: Any = settings) -> bool:
    """Return whether periodic search reconciliation is enabled."""
    config = _config(django_settings)
    value = config.get(
        "SEARCH_RECONCILE_ENABLED",
        getattr(django_settings, "SEARCH_RECONCILE_ENABLED", False),
    )
    return bool(value)


def search_reconcile_interval_seconds(django_settings: Any = settings) -> int:
    """Return the periodic reconciliation interval in seconds."""
    config = _config(django_settings)
    raw = config.get(
        "SEARCH_RECONCILE_INTERVAL_SECONDS",
        getattr(django_settings, "SEARCH_RECONCILE_INTERVAL_SECONDS", 60),
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 60


def configure_search_reconcile_beat_schedule_from_settings(
    django_settings: Any = settings,
) -> bool:
    """Register periodic search reconciliation in Celery Beat."""
    if not search_reconcile_enabled(django_settings):
        return False
    if not CELERY_AVAILABLE or current_app is None:
        logger.warning(
            "search reconciliation beat schedule skipped; celery unavailable"
        )
        return False

    schedule: dict[str, Any] = dict(
        getattr(current_app.conf, "beat_schedule", {}) or {}
    )
    interval_seconds = float(search_reconcile_interval_seconds(django_settings))
    schedule[SEARCH_RECONCILE_BEAT_SCHEDULE_KEY] = {
        "task": "general_manager.search.tasks.reconcile_search_indexes_task",
        "schedule": interval_seconds,
        "options": {"queue": "search.reconciliation"},
    }
    current_app.conf.beat_schedule = schedule
    logger.info(
        "search reconciliation beat schedule configured",
        context={
            "schedule_key": SEARCH_RECONCILE_BEAT_SCHEDULE_KEY,
            "interval_seconds": interval_seconds,
        },
    )
    return True


@shared_task(queue="search.reconciliation")
def reconcile_search_indexes_task() -> dict[str, int]:
    """Run one search reconciliation sweep."""
    result = reconcile_search_indexes()
    return {
        "reconciled": result.reconciled,
        "failed": result.failed,
        "documents": result.documents,
    }
