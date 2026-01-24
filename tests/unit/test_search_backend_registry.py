from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from general_manager.search.backend_registry import (
    configure_search_backend,
    configure_search_backend_from_settings,
    get_search_backend,
    _resolve_backend,
)
from general_manager.search.backends.dev import DevSearchBackend


class _CallableBackend:
    def __call__(self) -> DevSearchBackend:
        return DevSearchBackend()


class _ConfigurableBackend(DevSearchBackend):
    def __init__(self, *, label: str) -> None:
        super().__init__()
        self.label = label


class BackendRegistryTests(SimpleTestCase):
    def tearDown(self) -> None:
        configure_search_backend(None)

    def test_resolve_backend_none(self) -> None:
        assert _resolve_backend(None) is None

    def test_resolve_backend_type(self) -> None:
        resolved = _resolve_backend(DevSearchBackend)
        assert isinstance(resolved, DevSearchBackend)

    def test_resolve_backend_callable(self) -> None:
        resolved = _resolve_backend(_CallableBackend())
        assert isinstance(resolved, DevSearchBackend)

    def test_resolve_backend_mapping_with_class_path(self) -> None:
        resolved = _resolve_backend(
            {
                "class": "general_manager.search.backends.dev.DevSearchBackend",
                "options": {},
            }
        )
        assert isinstance(resolved, DevSearchBackend)

    def test_resolve_backend_mapping_with_class(self) -> None:
        resolved = _resolve_backend({"class": DevSearchBackend, "options": {}})
        assert isinstance(resolved, DevSearchBackend)

    def test_resolve_backend_mapping_with_callable(self) -> None:
        resolved = _resolve_backend({"class": _CallableBackend(), "options": {}})
        assert isinstance(resolved, DevSearchBackend)

    def test_resolve_backend_mapping_with_options(self) -> None:
        resolved = _resolve_backend(
            {"class": _ConfigurableBackend, "options": {"label": "demo"}}
        )
        assert isinstance(resolved, _ConfigurableBackend)
        assert resolved.label == "demo"

    def test_configure_search_backend_from_settings(self) -> None:
        dummy_settings = SimpleNamespace(SEARCH_BACKEND=DevSearchBackend)
        configure_search_backend_from_settings(dummy_settings)
        backend = get_search_backend()
        assert isinstance(backend, DevSearchBackend)

    @override_settings(GENERAL_MANAGER={"SEARCH_BACKEND": DevSearchBackend})
    def test_get_search_backend_uses_settings(self) -> None:
        configure_search_backend(None)
        backend = get_search_backend()
        assert isinstance(backend, DevSearchBackend)

    def test_get_search_backend_defaults(self) -> None:
        configure_search_backend(None)
        backend = get_search_backend()
        assert isinstance(backend, DevSearchBackend)


def test_resolve_backend_invalid_mapping() -> None:
    assert _resolve_backend({"options": {}}) is None
