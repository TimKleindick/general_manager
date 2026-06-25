"""Backend configuration and lookup for search providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

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


class InvalidSearchBackendOptionsError(TypeError):
    """Raised when a SEARCH_BACKEND mapping uses non-mapping options."""

    def __init__(self) -> None:
        super().__init__("SEARCH_BACKEND options must be a mapping.")


def configure_search_backend(backend: SearchBackend | None) -> None:
    """
    Set the active search backend instance.

    Parameters:
        backend: Instance to set as the process-local active search backend.
            Pass `None` to clear the configured backend so the next
            `get_search_backend()` call reads settings and may install the
            development fallback.
    """
    global _backend
    _backend = backend


def _instantiate_backend_reference(
    value: object,
    options: Mapping[str, object] | None = None,
) -> object:
    """Instantiate a backend class or factory while preserving backend instances."""
    if isinstance(value, type):
        factory = cast(Callable[..., object], value)
        return factory(**dict(options or {}))
    if callable(value) and not isinstance(value, SearchBackend):
        factory = cast(Callable[..., object], value)
        return factory(**dict(options or {}))
    return value


def _resolve_backend(value: object) -> SearchBackend | None:
    """Resolve a backend setting value into a concrete backend instance."""
    if value is None:
        return None
    if isinstance(value, str):
        resolved: object = import_string(value)
    elif isinstance(value, Mapping):
        config = cast(Mapping[str, object], value)
        backend_reference = config.get("class")
        options_value = config.get("options", {})
        if backend_reference is None:
            return None
        if options_value is None:
            options: Mapping[str, object] = {}
        elif isinstance(options_value, Mapping):
            options = cast(Mapping[str, object], options_value)
        else:
            raise InvalidSearchBackendOptionsError
        resolved_reference = (
            import_string(backend_reference)
            if isinstance(backend_reference, str)
            else backend_reference
        )
        resolved = _instantiate_backend_reference(resolved_reference, options)
    else:
        resolved = value

    resolved = _instantiate_backend_reference(resolved)
    return resolved if isinstance(resolved, SearchBackend) else None


def configure_search_backend_from_settings(django_settings: object) -> None:
    """
    Configure the active search backend using values from Django settings.

    `GENERAL_MANAGER["SEARCH_BACKEND"]` takes precedence over a top-level
    `SEARCH_BACKEND` setting, including explicit `None` to clear the configured
    backend. Values may be:

    - `None` or missing to clear the active backend.
    - A `SearchBackend` instance.
    - A dotted import path to a `SearchBackend` instance, class, or factory.
    - A zero-argument callable returning a `SearchBackend`.
    - A mapping with `{"class": <path-or-callable>, "options": {...}}`; options
      are passed as keyword arguments when constructing/calling the reference.

    Import, factory, and constructor exceptions propagate. Resolved objects that
    do not satisfy `SearchBackend` raise `SearchBackendNotConfiguredError`.

    Parameters:
        django_settings: Django settings module or object to read configuration
            from.

    Raises:
        TypeError: If a mapping configuration provides an `options` value that
            is not a mapping.
        SearchBackendNotConfiguredError: If a non-`None` backend setting cannot
            be resolved to a `SearchBackend`.
    """
    config_candidate: object = getattr(django_settings, _SETTINGS_KEY, None)
    backend_setting: object = None
    if isinstance(config_candidate, Mapping):
        config = cast(Mapping[str, object], config_candidate)
        if _SEARCH_BACKEND_KEY in config:
            backend_setting = config[_SEARCH_BACKEND_KEY]
        else:
            backend_setting = getattr(django_settings, _SEARCH_BACKEND_KEY, None)
    else:
        backend_setting = getattr(django_settings, _SEARCH_BACKEND_KEY, None)

    backend_instance = _resolve_backend(backend_setting)
    if backend_setting is not None and backend_instance is None:
        raise SearchBackendNotConfiguredError.from_setting(backend_setting)
    configure_search_backend(backend_instance)


def get_search_backend() -> SearchBackend:
    """
    Return the active search backend, configuring it from Django settings first.

    If no backend has been configured, this function calls
    `configure_search_backend_from_settings(django.conf.settings)`. If settings
    still leave the backend unset, it creates one `DevSearchBackend`, stores it
    as the process-local active backend, and returns that same fallback instance
    on later calls.

    Returns:
        The configured or development fallback search backend.

    Raises:
        SearchBackendNotConfiguredError: If backend configuration cannot be
            resolved to a valid `SearchBackend`.
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
