"""Celery-compatible tasks for GraphQL property warm-up."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import ParamSpec, Protocol, TypeVar, cast, overload

from django.conf import settings
from django.utils.module_loading import import_string

from general_manager.api.graphql_warmup import (
    GraphQLWarmUpManagerClass,
    refresh_due_graphql_warmup_recipes,
    warm_up_graphql_properties,
    warm_up_graphql_recipe,
)
from general_manager.logging import get_logger

logger = get_logger("api.graphql_warmup_tasks")
P = ParamSpec("P")
R = TypeVar("R")
R_co = TypeVar("R_co", covariant=True)
ManagerClass = GraphQLWarmUpManagerClass


class _TaskCallable(Protocol[P, R_co]):
    """Callable task wrapper returned by Celery's shared_task decorator."""

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R_co: ...

    def delay(self, *args: P.args, **kwargs: P.kwargs) -> object: ...


class _SharedTask(Protocol):
    """Typed subset of Celery's shared_task decorator used in this module."""

    @overload
    def __call__(
        self,
        func: Callable[P, R],
        **kwargs: object,
    ) -> _TaskCallable[P, R]: ...

    @overload
    def __call__(
        self,
        func: None = None,
        **kwargs: object,
    ) -> Callable[[Callable[P, R]], _TaskCallable[P, R]]: ...


class _RawSharedTask(Protocol):
    """Runtime callable shape of Celery's shared_task decorator."""

    def __call__(self, func: object = None, **kwargs: object) -> object: ...


class _CeleryConf(Protocol):
    """Celery app config attributes used by the beat helper."""

    beat_schedule: object


class _CeleryApp(Protocol):
    """Celery app attributes used by the beat helper."""

    conf: _CeleryConf


class _InvalidManagerClassError(TypeError):
    """Raised when dispatch receives a non-class manager entry."""

    def __init__(self) -> None:
        """Build the validation error message."""
        super().__init__("manager_classes entries must be classes")


class _InvalidCacheKeyIterableError(TypeError):
    """Raised when recipe dispatch receives one string instead of an iterable."""

    def __init__(self) -> None:
        """Build the validation error message."""
        super().__init__("cache_keys must be an iterable of strings, not a string")


class _InvalidCacheKeyError(TypeError):
    """Raised when recipe dispatch receives a non-string cache key."""

    def __init__(self) -> None:
        """Build the validation error message."""
        super().__init__("cache_keys entries must be strings")


class _InvalidStringListError(TypeError):
    """Raised when a task argument is not a list of strings."""

    def __init__(self, label: str, *, entries: bool = False) -> None:
        """Build the validation error message."""
        if entries:
            super().__init__(f"{label} entries must be strings")
        else:
            super().__init__(f"{label} must be a list of strings")


class _InvalidLimitError(TypeError):
    """Raised when a due-refresh limit is not `None` or an integer."""

    def __init__(self) -> None:
        """Build the validation error message."""
        super().__init__("limit must be None or an integer")


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
        """Return the wrapped function unchanged."""
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
    return cast(
        _TaskCallable[P, R],
        celery_shared_task(func, **kwargs),
    )


GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY = "general_manager.graphql_warmup.refresh_due"
GRAPHQL_WARMUP_QUEUE = "graphql.warmup"
FALSE_SETTING_STRINGS = {"", "0", "false", "no", "off", "none", "null"}
TRUE_SETTING_STRINGS = {"1", "true", "yes", "on"}


def configure_graphql_warmup_beat_schedule_from_settings(
    django_settings: object = settings,
) -> bool:
    """
    Register the periodic timeout refresh task in Celery Beat.

    Settings may live either in `GENERAL_MANAGER` or as top-level Django
    settings; nested keys take precedence over top-level settings when both are
    present. Returns `False` when `GRAPHQL_WARMUP_BEAT_ENABLED` is false, Celery
    is not importable, or the module-level Celery `current_app` is `None`. When
    configured, the helper writes or replaces one beat schedule entry named
    `GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY`. The entry has task path
    `general_manager.api.graphql_warmup_tasks.refresh_due_graphql_warmup_recipes_task`,
    a float seconds `schedule`, no args or kwargs, and `options={"queue":
    GRAPHQL_WARMUP_QUEUE}`. `GRAPHQL_WARMUP_BEAT_INTERVAL_SECONDS` is parsed as
    a positive integer and defaults to `60` when missing, non-positive, or
    invalid. Existing mapping beat schedule entries are copied and preserved;
    non-mapping schedule values are replaced by a fresh schedule dictionary.
    Boolean settings accept normal truthiness plus string false values `""`,
    `"0"`, `"false"`, `"no"`, `"off"`, `"none"`, and `"null"`, and string true
    values `"1"`, `"true"`, `"yes"`, and `"on"`; unrecognized non-empty strings
    are treated as enabled.

    Exceptions from Celery app configuration access or assignment propagate.
    """
    if not _bool_setting(django_settings, "GRAPHQL_WARMUP_BEAT_ENABLED", False):
        return False
    if not CELERY_AVAILABLE or current_app is None:
        logger.warning("graphql warm-up beat schedule skipped; celery unavailable")
        return False
    raw_schedule = getattr(current_app.conf, "beat_schedule", {}) or {}
    schedule: dict[str, object] = dict(
        cast(Mapping[str, object], raw_schedule)
        if isinstance(raw_schedule, Mapping)
        else {}
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
    """
    Resolve optional manager paths and run all-entry GraphQL warm-up.

    `manager_paths=None` warms every discoverable manager. Otherwise each dotted
    path is resolved before calling `warm_up_graphql_properties(...)`. Import
    errors and executor errors propagate to the Celery worker/caller. The return
    value contains `evaluated` successful property reads, `failed` property
    reads that raised inside the executor, and `recipes` persisted warm-up
    recipes; no other keys are added by this adapter. An empty path list
    delegates an empty manager list and returns zero counts unless the executor
    is patched/customized.

    Raises:
        TypeError: If `manager_paths` is not `None` or a list of strings.
    """
    manager_classes = None
    if manager_paths is not None:
        manager_classes = [
            import_string(path)
            for path in _validate_string_list(manager_paths, "manager_paths")
        ]
    summary = warm_up_graphql_properties(manager_classes)
    return {
        "evaluated": summary.evaluated,
        "failed": summary.failed,
        "recipes": summary.recipes,
    }


@shared_task(queue=GRAPHQL_WARMUP_QUEUE)
def warm_up_graphql_recipes_task(cache_keys: list[str]) -> int:
    """
    Warm recipe-backed cache entries with per-key failure isolation.

    Each cache key is attempted in list order. `warm_up_graphql_recipe(...)`
    returning `True` increments the returned count; `False` does not. Exceptions
    for one key are logged with the cache key and do not stop later keys. Empty
    input returns `0`.

    Raises:
        TypeError: If `cache_keys` is not a list of strings.
    """
    warmed = 0
    for cache_key in _validate_string_list(cache_keys, "cache_keys"):
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
    """
    Refresh due timeout recipes and return the number refreshed.

    `limit` is forwarded unchanged to `refresh_due_graphql_warmup_recipes(...)`.
    `None` means no cap, `0` and negative values refresh no recipes, and
    positive values cap the sorted due-key list in the registry. Exceptions from
    the executor propagate to the Celery worker/caller.

    Raises:
        TypeError: If `limit` is not `None` or an integer. Boolean values are
            rejected even though `bool` is an `int` subclass.
    """
    _validate_limit(limit)
    return int(refresh_due_graphql_warmup_recipes(limit=limit))


def dispatch_graphql_warmup(
    manager_classes: Iterable[ManagerClass] | None = None,
) -> bool:
    """
    Dispatch all-entry warm-up to Celery when available.

    Returns `False` when Celery is unavailable, when every supplied manager class
    lacks an importable worker path, when the supplied iterable is empty, or when
    enqueueing raises. When `manager_classes` is `None`, the task is enqueued
    with `None` so the worker warms all managers. Local/nested manager classes
    are skipped because workers cannot import them. Duplicate import paths are
    removed while preserving first-seen order.

    Raises:
        TypeError: If an item in `manager_classes` is not a class.
        Exception: Propagates errors raised while iterating `manager_classes`.
    """
    if not CELERY_AVAILABLE:
        return False
    manager_paths = None
    if manager_classes is not None:
        manager_paths = _dedupe_manager_paths(manager_classes)
        if not manager_paths:
            return False
    try:
        warm_up_graphql_properties_task.delay(manager_paths)
    except Exception:
        logger.exception("failed to enqueue graphql warm-up task")
        return False
    return True


def dispatch_graphql_recipe_warmup(cache_keys: Iterable[str]) -> bool:
    """
    Dispatch recipe re-warm to Celery when available.

    Duplicate cache keys are removed while preserving first-seen order. Returns
    `False` when Celery is unavailable, the deduplicated key list is empty, or
    enqueueing raises.

    Raises:
        TypeError: If `cache_keys` is a single string or contains non-string
            values.
        Exception: Propagates errors raised while iterating `cache_keys`.
    """
    keys = _dedupe_cache_keys(cache_keys)
    if not (CELERY_AVAILABLE and keys):
        return False
    try:
        warm_up_graphql_recipes_task.delay(keys)
    except Exception:
        logger.exception("failed to enqueue graphql recipe warm-up task")
        return False
    return True


def _config(django_settings: object) -> dict[str, object]:
    """Return the nested GENERAL_MANAGER settings dictionary."""
    value = getattr(django_settings, "GENERAL_MANAGER", {})
    return dict(value) if isinstance(value, dict) else {}


def _bool_setting(django_settings: object, key: str, default: bool) -> bool:
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


def _int_setting(django_settings: object, key: str, default: int) -> int:
    """Read a positive integer setting with a fallback."""
    config = _config(django_settings)
    raw = config.get(key, getattr(django_settings, key, default))
    if isinstance(raw, bool | int | float | str):
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return default
    return default


def _validate_limit(limit: int | None) -> None:
    """Validate the due-refresh task limit."""
    if limit is None:
        return
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise _InvalidLimitError


def _dedupe_manager_paths(manager_classes: Iterable[ManagerClass]) -> list[str]:
    """Return distinct importable manager paths in input order."""
    manager_paths: list[str] = []
    seen: set[str] = set()
    for manager_class in manager_classes:
        if not isinstance(manager_class, type):
            raise _InvalidManagerClassError
        path = _manager_path(manager_class)
        if path is not None and path not in seen:
            manager_paths.append(path)
            seen.add(path)
    return manager_paths


def _dedupe_cache_keys(cache_keys: Iterable[str]) -> list[str]:
    """Return distinct cache key strings in input order."""
    if isinstance(cache_keys, str):
        raise _InvalidCacheKeyIterableError
    keys: list[str] = []
    seen: set[str] = set()
    for cache_key in cache_keys:
        if not isinstance(cache_key, str):
            raise _InvalidCacheKeyError
        if cache_key not in seen:
            keys.append(cache_key)
            seen.add(cache_key)
    return keys


def _validate_string_list(value: object, label: str) -> list[str]:
    """Return `value` after validating it is a list of strings."""
    if not isinstance(value, list):
        raise _InvalidStringListError(label)
    for item in value:
        if not isinstance(item, str):
            raise _InvalidStringListError(label, entries=True)
    return value


def _manager_path(manager_class: ManagerClass) -> str | None:
    """Return an import path for manager classes that workers can resolve."""
    qualname = manager_class.__qualname__
    if "<locals>" in qualname:
        return None
    return f"{manager_class.__module__}.{qualname}"
