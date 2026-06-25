"""Celery tasks and schedule setup for search reconciliation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import ParamSpec, Protocol, TypeVar, cast, overload

from django.conf import settings

from general_manager.logging import get_logger
from general_manager.search.reconciliation import reconcile_search_indexes

logger = get_logger("search.tasks")
P = ParamSpec("P")
R = TypeVar("R")
R_co = TypeVar("R_co", covariant=True)


class _TaskCallable(Protocol[P, R_co]):
    """Callable task wrapper returned by Celery's shared_task decorator."""

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R_co: ...

    def delay(self, *args: P.args, **kwargs: P.kwargs) -> object: ...


class _RawSharedTask(Protocol):
    """Runtime callable shape of Celery's shared_task decorator."""

    def __call__(self, func: object = None, **kwargs: object) -> object: ...


class _CeleryConf(Protocol):
    """Celery app config attributes used by the Beat helper."""

    beat_schedule: object


class _CeleryApp(Protocol):
    """Celery app attributes used by the Beat helper."""

    conf: _CeleryConf


_raw_shared_task: object | None = None

try:
    from celery import current_app as _celery_current_app
    from celery import shared_task as _celery_shared_task

    CELERY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency boundary
    CELERY_AVAILABLE = False
    current_app: _CeleryApp | None = None
else:
    current_app = cast(_CeleryApp | None, _celery_current_app)
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


SEARCH_RECONCILE_BEAT_SCHEDULE_KEY = "general_manager.search.reconcile"


def _config(django_settings: object = settings) -> Mapping[str, object]:
    """Return the GENERAL_MANAGER settings mapping when configured."""
    value = getattr(django_settings, "GENERAL_MANAGER", {})
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def search_reconcile_enabled(django_settings: object = settings) -> bool:
    """
    Return whether periodic search reconciliation is enabled.

    `GENERAL_MANAGER["SEARCH_RECONCILE_ENABLED"]` takes precedence over the
    top-level setting. Missing values default to `False`; configured values use
    normal Python truthiness.
    """
    config = _config(django_settings)
    value = config.get(
        "SEARCH_RECONCILE_ENABLED",
        getattr(django_settings, "SEARCH_RECONCILE_ENABLED", False),
    )
    return bool(value)


def search_reconcile_interval_seconds(django_settings: object = settings) -> int:
    """
    Return the periodic reconciliation interval in seconds.

    `GENERAL_MANAGER["SEARCH_RECONCILE_INTERVAL_SECONDS"]` takes precedence over
    the top-level setting. Missing or invalid values fall back to `60`; valid
    integer-like values are clamped to at least one second.
    """
    config = _config(django_settings)
    raw = config.get(
        "SEARCH_RECONCILE_INTERVAL_SECONDS",
        getattr(django_settings, "SEARCH_RECONCILE_INTERVAL_SECONDS", 60),
    )
    if isinstance(raw, bool):
        return 60
    if isinstance(raw, int | float | str):
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 60
    return 60


def configure_search_reconcile_beat_schedule_from_settings(
    django_settings: object = settings,
) -> bool:
    """
    Register periodic search reconciliation in Celery Beat.

    Returns `False` when reconciliation is disabled, Celery is unavailable, or
    the module-level Celery `current_app` is `None`. When configured, this writes
    or replaces `SEARCH_RECONCILE_BEAT_SCHEDULE_KEY` with task path
    `general_manager.search.tasks.reconcile_search_indexes_task`, a float
    seconds schedule, no args or kwargs, and `options={"queue":
    "search.reconciliation"}`. Existing mapping schedule entries are preserved;
    malformed non-mapping schedule values are treated as an empty schedule.

    Exceptions from Celery app configuration access or assignment propagate.
    """
    if not search_reconcile_enabled(django_settings):
        return False
    if not CELERY_AVAILABLE or current_app is None:
        logger.warning(
            "search reconciliation beat schedule skipped; celery unavailable"
        )
        return False

    raw_schedule = getattr(current_app.conf, "beat_schedule", {}) or {}
    schedule: dict[str, object] = dict(
        cast(Mapping[str, object], raw_schedule)
        if isinstance(raw_schedule, Mapping)
        else {}
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
    """
    Run one search reconciliation sweep.

    Returns a dictionary with `reconciled`, `failed`, and `documents` counts from
    `reconcile_search_indexes()`. Exceptions from the reconciliation service
    propagate to the Celery worker or direct caller.
    """
    result = reconcile_search_indexes()
    return {
        "reconciled": result.reconciled,
        "failed": result.failed,
        "documents": result.documents,
    }
