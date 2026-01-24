from django.test import SimpleTestCase

from general_manager.search.backend_registry import (
    configure_search_backend,
    get_search_backend,
)
from general_manager.search.backends.dev import DevSearchBackend


class SearchBackendRegistryTests(SimpleTestCase):
    def tearDown(self) -> None:
        configure_search_backend(None)

    def test_default_backend_is_devsearch(self) -> None:
        configure_search_backend(None)
        backend = get_search_backend()
        assert isinstance(backend, DevSearchBackend)
