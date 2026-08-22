from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from general_manager.search import backend_registry
from general_manager.search.backend_registry import (
    configure_search_backend,
    configure_search_backend_from_settings,
    get_search_backend,
    _resolve_backend,
)
from general_manager.search.backend import SearchResult
from general_manager.search.backends.dev import DevSearchBackend


class _CallableBackend:
    def __call__(self) -> DevSearchBackend:
        """
        Create a new DevSearchBackend instance.

        Returns:
            backend (DevSearchBackend): A new DevSearchBackend instance.
        """
        return DevSearchBackend()


class _ConfigurableBackend(DevSearchBackend):
    def __init__(self, *, label: str) -> None:
        """
        Initialize a configurable development search backend with a human-readable label.

        Parameters:
            label (str): Human-readable label to attach to the backend instance.
        """
        super().__init__()
        self.label = label


class _ExternalProtocolBackend:
    """Minimal non-Dev implementation accepted by the search backend protocol."""

    def ensure_index(self, index_name: str, settings: object) -> None:
        return None

    def upsert(self, index_name: str, documents: object) -> None:
        return None

    def delete(self, index_name: str, ids: object) -> None:
        return None

    def list_document_ids(
        self,
        index_name: str,
        *,
        types: object = None,
    ) -> set[str]:
        return set()

    def search(self, index_name: str, query: str, **kwargs: object) -> SearchResult:
        return SearchResult(hits=[], total=0)


class BackendRegistryTests(SimpleTestCase):
    def tearDown(self) -> None:
        """
        Reset the configured search backend to its default state after a test.

        Clears any test-specific search backend configuration by calling configure_search_backend(None),
        ensuring subsequent tests start with the registry unmodified.
        """
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

    def test_dev_auto_reindex_requires_setting(self) -> None:
        """The settings-selected DevSearch honors its truthy lifecycle setting."""
        enabled = SimpleNamespace(
            DEBUG=True,
            GENERAL_MANAGER={
                "SEARCH_BACKEND": DevSearchBackend,
                "SEARCH_AUTO_REINDEX": True,
            },
        )

        configure_search_backend_from_settings(enabled)

        assert get_search_backend().auto_reindex_enabled is True

    def test_dev_auto_reindex_is_enabled_outside_debug(self) -> None:
        """DevSearch hydration follows its explicit setting outside DEBUG."""
        enabled = SimpleNamespace(
            DEBUG=False,
            GENERAL_MANAGER={
                "SEARCH_BACKEND": DevSearchBackend,
                "SEARCH_AUTO_REINDEX": True,
            },
        )

        configure_search_backend_from_settings(enabled)

        assert get_search_backend().auto_reindex_enabled is True

    def test_dev_auto_reindex_is_disabled_when_setting_is_missing(self) -> None:
        """DevSearch remains inert unless auto-reindex is explicitly configured."""
        django_settings = SimpleNamespace(
            DEBUG=False,
            GENERAL_MANAGER={"SEARCH_BACKEND": DevSearchBackend},
        )

        configure_search_backend_from_settings(django_settings)

        assert get_search_backend().auto_reindex_enabled is False

    def test_dev_auto_reindex_is_disabled_when_setting_is_false(self) -> None:
        """A false lifecycle setting disables hydration in every environment."""
        django_settings = SimpleNamespace(
            DEBUG=True,
            GENERAL_MANAGER={
                "SEARCH_BACKEND": DevSearchBackend,
                "SEARCH_AUTO_REINDEX": False,
            },
        )

        configure_search_backend_from_settings(django_settings)

        assert get_search_backend().auto_reindex_enabled is False

    def test_dev_auto_reindex_honors_nested_setting_precedence(self) -> None:
        """The nested lifecycle setting overrides a conflicting top-level value."""
        django_settings = SimpleNamespace(
            DEBUG=True,
            SEARCH_AUTO_REINDEX=True,
            GENERAL_MANAGER={"SEARCH_AUTO_REINDEX": False},
        )

        assert backend_registry._dev_auto_reindex_enabled(django_settings) is False

    def test_non_dev_backend_ignores_auto_reindex_setting(self) -> None:
        """External protocol backends configure without DevSearch mutation."""
        django_settings = SimpleNamespace(
            DEBUG=True,
            GENERAL_MANAGER={
                "SEARCH_BACKEND": _ExternalProtocolBackend,
                "SEARCH_AUTO_REINDEX": True,
            },
        )

        configure_search_backend_from_settings(django_settings)

        backend = get_search_backend()
        assert isinstance(backend, _ExternalProtocolBackend)
        assert not hasattr(backend, "auto_reindex_enabled")

    @override_settings(DEBUG=False, SEARCH_AUTO_REINDEX=True, GENERAL_MANAGER={})
    def test_dev_fallback_honors_auto_reindex_outside_debug(self) -> None:
        """The settings fallback follows the same DevSearch lifecycle setting."""
        configure_search_backend(None)

        assert get_search_backend().auto_reindex_enabled is True

    def test_configure_search_backend_from_settings_nested_none_disables(self) -> None:
        from general_manager.search import backend_registry

        dummy_settings = SimpleNamespace(
            GENERAL_MANAGER={"SEARCH_BACKEND": None},
            SEARCH_BACKEND=_ConfigurableBackend,
        )

        configure_search_backend_from_settings(dummy_settings)

        assert backend_registry._backend is None

    def test_configure_search_backend_from_settings_rejects_invalid_options(
        self,
    ) -> None:
        dummy_settings = SimpleNamespace(
            GENERAL_MANAGER={
                "SEARCH_BACKEND": {
                    "class": _ConfigurableBackend,
                    "options": ["not", "a", "mapping"],
                }
            }
        )

        with self.assertRaisesRegex(TypeError, "SEARCH_BACKEND options"):
            configure_search_backend_from_settings(dummy_settings)

    @override_settings(GENERAL_MANAGER={"SEARCH_BACKEND": DevSearchBackend})
    def test_get_search_backend_uses_settings(self) -> None:
        """
        Ensure that when no custom backend is configured, get_search_backend returns the default DevSearchBackend.
        """
        configure_search_backend(None)
        backend = get_search_backend()
        assert isinstance(backend, DevSearchBackend)

    def test_get_search_backend_defaults(self) -> None:
        configure_search_backend(None)
        backend = get_search_backend()
        assert isinstance(backend, DevSearchBackend)


def test_resolve_backend_invalid_mapping() -> None:
    assert _resolve_backend({"options": {}}) is None
