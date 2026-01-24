"""Backend configuration and lookup for search providers."""

from __future__ import annotations

from typing import Any, Mapping

from django.conf import settings
from django.utils.module_loading import import_string

from general_manager.search.backend import (
    SearchBackend,
    SearchBackendNotConfiguredError,
)
from general_manager.search.backends.dev import DevSearchBackend

_SETTINGS_KEY = "GENERAL_MANAGER"
_SEARCH_BACKEND_KEY = "SEARCH_BACKEND"

_backend: SearchBackend | None = None


def configure_search_backend(backend: SearchBackend | None) -> None:
    """Set the active search backend instance."""
    global _backend
    _backend = backend


def _resolve_backend(value: Any) -> SearchBackend | None:
    if value is None:
        return None
    if isinstance(value, str):
        resolved = import_string(value)
    elif isinstance(value, Mapping):
        class_path = value.get("class")
        options = value.get("options", {})
        if class_path is None:
            return None
        resolved = (
            import_string(class_path) if isinstance(class_path, str) else class_path
        )
        if isinstance(resolved, type):
            return resolved(**options)
        if callable(resolved):
            return resolved(**options)
        return None
    else:
        resolved = value

    if isinstance(resolved, type):
        return resolved()
    if callable(resolved):
        return resolved()
    return resolved  # type: ignore[return-value]


def configure_search_backend_from_settings(django_settings: Any) -> None:
    """Resolve and configure the search backend from Django settings."""
    config: Mapping[str, Any] | None = getattr(django_settings, _SETTINGS_KEY, None)
    backend_setting: Any = None
    if isinstance(config, Mapping):
        backend_setting = config.get(_SEARCH_BACKEND_KEY)
    if backend_setting is None:
        backend_setting = getattr(django_settings, _SEARCH_BACKEND_KEY, None)

    backend_instance = _resolve_backend(backend_setting)
    configure_search_backend(backend_instance)


def get_search_backend() -> SearchBackend:
    """Return the configured backend or a DevSearch backend by default."""
    global _backend
    if _backend is not None:
        return _backend

    configure_search_backend_from_settings(settings)
    if _backend is None:
        _backend = DevSearchBackend()
    if _backend is None:
        raise SearchBackendNotConfiguredError()
    return _backend
