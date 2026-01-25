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
    """
    Set the active search backend instance.
    
    Parameters:
        backend (SearchBackend | None): Instance to set as the global search backend. Pass `None` to clear any configured backend.
    """
    global _backend
    _backend = backend


def _resolve_backend(value: Any) -> SearchBackend | None:
    """
    Resolve various backend specifications into a concrete SearchBackend instance or None.
    
    Accepts:
    - None: resolved as None.
    - A string: treated as an import path and imported.
    - A Mapping with keys "class" (required) and optional "options" (dict): "class" may be an import path or a direct class/callable; the resulting class/callable is invoked with the provided options to produce the backend instance.
    - A class, callable, or already-instantiated backend: classes and callables are invoked with no arguments to produce an instance; other values are returned as-is.
    
    Parameters:
        value (Any): Backend specification in one of the forms described above.
    
    Returns:
        SearchBackend | None: A concrete SearchBackend instance when resolution succeeds, `None` otherwise.
    """
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
    """
    Configure the active search backend using values from Django settings.
    
    Reads backend configuration from the GENERAL_MANAGER mapping's SEARCH_BACKEND key if present; otherwise falls back to the top-level SEARCH_BACKEND setting. Resolves the configured value into a concrete backend instance and sets it as the active backend.
    
    Parameters:
        django_settings (Any): Django settings module or object to read configuration from.
    """
    config: Mapping[str, Any] | None = getattr(django_settings, _SETTINGS_KEY, None)
    backend_setting: Any = None
    if isinstance(config, Mapping):
        backend_setting = config.get(_SEARCH_BACKEND_KEY)
    if backend_setting is None:
        backend_setting = getattr(django_settings, _SEARCH_BACKEND_KEY, None)

    backend_instance = _resolve_backend(backend_setting)
    configure_search_backend(backend_instance)


def get_search_backend() -> SearchBackend:
    """
    Retrieve the active search backend, falling back to a development backend if none is configured.
    
    Returns:
        SearchBackend: The configured search backend instance.
    
    Raises:
        SearchBackendNotConfiguredError: If no backend can be resolved after attempting configuration and fallback.
    """
    global _backend
    if _backend is not None:
        return _backend

    configure_search_backend_from_settings(settings)
    if _backend is None:
        _backend = DevSearchBackend()
    if _backend is None:
        raise SearchBackendNotConfiguredError()
    return _backend