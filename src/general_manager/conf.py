"""Unified access to General Manager settings from Django configuration."""

from __future__ import annotations

from typing import TypeVar, overload

from django.conf import settings


_SENTINEL = object()
_T = TypeVar("_T")


@overload
def get_setting(key: str) -> object | None: ...


@overload
def get_setting(key: str, default: _T) -> object | _T: ...


def get_setting(key: str, default: object = None) -> object:
    """Look up a GeneralManager configuration value.

    Resolution order:
    1. `settings.GENERAL_MANAGER[key]`, when `GENERAL_MANAGER` is a `dict`
    2. `settings.GENERAL_MANAGER_<key>` legacy prefixed setting
    3. `settings.<key>` legacy top-level setting
    4. `default`

    Non-dict `GENERAL_MANAGER` values are ignored. Attribute-access errors from
    Django settings are not wrapped.
    """
    config = getattr(settings, "GENERAL_MANAGER", {})
    if isinstance(config, dict) and key in config:
        return config[key]
    prefixed = getattr(settings, f"GENERAL_MANAGER_{key}", _SENTINEL)
    if prefixed is not _SENTINEL:
        return prefixed
    top_level = getattr(settings, key, _SENTINEL)
    if top_level is not _SENTINEL:
        return top_level
    return default
