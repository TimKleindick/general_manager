"""Workflow configuration helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import SupportsIndex, SupportsInt, TypeAlias, cast

from django.conf import settings

_SETTINGS_KEY = "GENERAL_MANAGER"

type WorkflowConfigValue = object
type WorkflowConfig = Mapping[str, WorkflowConfigValue]
SettingsLike: TypeAlias = object
IntCoercible: TypeAlias = str | bytes | bytearray | SupportsInt | SupportsIndex


def _setting(django_settings: SettingsLike, name: str, default: object) -> object:
    return getattr(django_settings, name, default)


def _config(django_settings: SettingsLike = settings) -> WorkflowConfig:
    value = getattr(django_settings, _SETTINGS_KEY, {})
    if isinstance(value, Mapping):
        return cast(WorkflowConfig, value)
    return {}


def _config_or_setting(
    django_settings: SettingsLike,
    name: str,
    default: object,
) -> object:
    return _config(django_settings).get(name, _setting(django_settings, name, default))


def _bounded_int(
    django_settings: SettingsLike,
    name: str,
    *,
    default: int,
    minimum: int,
) -> int:
    raw = _config_or_setting(django_settings, name, default)
    try:
        return max(minimum, int(cast(IntCoercible, raw)))
    except (TypeError, ValueError):
        return default


def workflow_mode(django_settings: SettingsLike = settings) -> str:
    """Return the configured workflow mode.

    Nested `GENERAL_MANAGER["WORKFLOW_MODE"]` takes precedence over the top-level
    setting. Values are coerced with `str(...).strip().lower()`. Only `"local"`
    and `"production"` are accepted; all other values fall back to `"local"`.
    """
    value = _config(django_settings).get(
        "WORKFLOW_MODE", _setting(django_settings, "WORKFLOW_MODE", "local")
    )
    mode = str(value).strip().lower()
    if mode not in {"local", "production"}:
        return "local"
    return mode


def workflow_async_enabled(django_settings: SettingsLike = settings) -> bool:
    """Return whether workflow handlers should be dispatched asynchronously.

    Explicit non-`None` nested or top-level `WORKFLOW_ASYNC` values are coerced
    with `bool(...)`; omitted or `None` values fall back to production mode.
    """
    explicit = _config_or_setting(django_settings, "WORKFLOW_ASYNC", None)
    if explicit is not None:
        return bool(explicit)
    return workflow_mode(django_settings) == "production"


def workflow_beat_enabled(django_settings: SettingsLike = settings) -> bool:
    """Return whether workflow Celery Beat scheduling is enabled.

    Explicit non-`None` nested or top-level `WORKFLOW_BEAT_ENABLED` values are
    coerced with `bool(...)`; omitted or `None` values fall back to production
    mode.
    """
    explicit = _config_or_setting(django_settings, "WORKFLOW_BEAT_ENABLED", None)
    if explicit is not None:
        return bool(explicit)
    return workflow_mode(django_settings) == "production"


def workflow_beat_outbox_interval_seconds(
    django_settings: SettingsLike = settings,
) -> int:
    """Return the workflow outbox Beat interval, clamped to at least 1 second."""
    return _bounded_int(
        django_settings,
        "WORKFLOW_BEAT_OUTBOX_INTERVAL_SECONDS",
        default=5,
        minimum=1,
    )


def workflow_beat_max_jitter_seconds(django_settings: SettingsLike = settings) -> int:
    """Return the workflow Beat jitter window, clamped to at least 0 seconds."""
    return _bounded_int(
        django_settings,
        "WORKFLOW_BEAT_MAX_JITTER_SECONDS",
        default=2,
        minimum=0,
    )


def workflow_outbox_batch_size(django_settings: SettingsLike = settings) -> int:
    """Return async outbox publish batch size, clamped to at least 1."""
    return _bounded_int(
        django_settings, "WORKFLOW_OUTBOX_BATCH_SIZE", default=100, minimum=1
    )


def workflow_outbox_process_chunk_size(
    django_settings: SettingsLike = settings,
) -> int:
    """Return outbox processing chunk size, clamped to at least 1."""
    return _bounded_int(
        django_settings,
        "WORKFLOW_OUTBOX_PROCESS_CHUNK_SIZE",
        default=50,
        minimum=1,
    )


def workflow_outbox_claim_ttl_seconds(django_settings: SettingsLike = settings) -> int:
    """Return outbox claim lease TTL, clamped to at least 1 second."""
    return _bounded_int(
        django_settings,
        "WORKFLOW_OUTBOX_CLAIM_TTL_SECONDS",
        default=300,
        minimum=1,
    )


def workflow_max_retries(django_settings: SettingsLike = settings) -> int:
    """Return max failed outbox processing calls before dead-lettering.

    Values are parsed with `int(...)`, clamped to at least 0, and default to 3
    when missing or invalid.
    """
    return _bounded_int(
        django_settings, "WORKFLOW_MAX_RETRIES", default=3, minimum=0
    )


def workflow_retry_backoff_seconds(django_settings: SettingsLike = settings) -> int:
    """Return retry backoff multiplier in seconds, clamped to at least 1."""
    return _bounded_int(
        django_settings,
        "WORKFLOW_RETRY_BACKOFF_SECONDS",
        default=5,
        minimum=1,
    )


def workflow_dead_letter_enabled(django_settings: SettingsLike = settings) -> bool:
    """Return whether max-retry outbox failures move to dead-letter status.

    Nested or top-level `WORKFLOW_DEAD_LETTER_ENABLED` values are always coerced
    with `bool(...)`; an explicit `None` therefore disables dead letters.
    """
    return bool(
        _config_or_setting(django_settings, "WORKFLOW_DEAD_LETTER_ENABLED", True)
    )


def workflow_delivery_running_timeout_seconds(
    django_settings: SettingsLike = settings,
) -> int:
    """Return stale running delivery-attempt timeout, clamped to at least 1."""
    return _bounded_int(
        django_settings,
        "WORKFLOW_DELIVERY_RUNNING_TIMEOUT_SECONDS",
        default=300,
        minimum=1,
    )
