"""Unified access to General Manager settings from Django configuration."""

from __future__ import annotations

from typing import Any

from django.conf import settings


_SENTINEL = object()


def get_setting(key: str, default: Any = None) -> Any:
    """
    Look up a General Manager configuration value.

    Resolution order:
    1. ``settings.GENERAL_MANAGER[key]``  (preferred)
    2. ``settings.GENERAL_MANAGER_<key>``  (legacy prefixed, e.g. ``GENERAL_MANAGER_VALIDATE_INPUT_VALUES``)
    3. ``settings.<key>``  (legacy top-level, e.g. ``AUTOCREATE_GRAPHQL``)
    4. *default*

    Parameters:
        key: Setting name to look up (e.g. ``"VALIDATE_INPUT_VALUES"``).
        default: Value returned when the key is absent everywhere.

    Returns:
        The resolved setting value or *default*.
    """
    config = getattr(settings, "GENERAL_MANAGER", {})
    if isinstance(config, dict) and key in config:
        return config[key]
    # Legacy: check for prefixed setting (e.g. settings.GENERAL_MANAGER_VALIDATE_INPUT_VALUES)
    prefixed = getattr(settings, f"GENERAL_MANAGER_{key}", _SENTINEL)
    if prefixed is not _SENTINEL:
        return prefixed
    # Legacy: check for top-level setting (e.g. settings.AUTOCREATE_GRAPHQL)
    top_level = getattr(settings, key, _SENTINEL)
    if top_level is not _SENTINEL:
        return top_level
    return default
