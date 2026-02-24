"""Workflow configuration helpers."""

from __future__ import annotations

from typing import Any, Mapping

from django.conf import settings

_SETTINGS_KEY = "GENERAL_MANAGER"


def _config(django_settings: Any = settings) -> Mapping[str, Any]:
    value = getattr(django_settings, _SETTINGS_KEY, {})
    if isinstance(value, Mapping):
        return value
    return {}


def workflow_mode(django_settings: Any = settings) -> str:
    value = _config(django_settings).get(
        "WORKFLOW_MODE", getattr(django_settings, "WORKFLOW_MODE", "local")
    )
    mode = str(value).strip().lower()
    if mode not in {"local", "production"}:
        return "local"
    return mode


def workflow_async_enabled(django_settings: Any = settings) -> bool:
    config = _config(django_settings)
    explicit = config.get(
        "WORKFLOW_ASYNC", getattr(django_settings, "WORKFLOW_ASYNC", None)
    )
    if explicit is not None:
        return bool(explicit)
    return workflow_mode(django_settings) == "production"


def workflow_outbox_batch_size(django_settings: Any = settings) -> int:
    config = _config(django_settings)
    raw = config.get(
        "WORKFLOW_OUTBOX_BATCH_SIZE",
        getattr(django_settings, "WORKFLOW_OUTBOX_BATCH_SIZE", 100),
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 100


def workflow_outbox_claim_ttl_seconds(django_settings: Any = settings) -> int:
    config = _config(django_settings)
    raw = config.get(
        "WORKFLOW_OUTBOX_CLAIM_TTL_SECONDS",
        getattr(django_settings, "WORKFLOW_OUTBOX_CLAIM_TTL_SECONDS", 300),
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 300


def workflow_max_retries(django_settings: Any = settings) -> int:
    config = _config(django_settings)
    raw = config.get(
        "WORKFLOW_MAX_RETRIES",
        getattr(django_settings, "WORKFLOW_MAX_RETRIES", 3),
    )
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 3


def workflow_retry_backoff_seconds(django_settings: Any = settings) -> int:
    config = _config(django_settings)
    raw = config.get(
        "WORKFLOW_RETRY_BACKOFF_SECONDS",
        getattr(django_settings, "WORKFLOW_RETRY_BACKOFF_SECONDS", 5),
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 5


def workflow_dead_letter_enabled(django_settings: Any = settings) -> bool:
    config = _config(django_settings)
    return bool(
        config.get(
            "WORKFLOW_DEAD_LETTER_ENABLED",
            getattr(django_settings, "WORKFLOW_DEAD_LETTER_ENABLED", True),
        )
    )
