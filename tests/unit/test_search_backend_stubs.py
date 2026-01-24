from __future__ import annotations

import pytest
from django.test import SimpleTestCase

from general_manager.search.backend import SearchBackendNotImplementedError
from general_manager.search.backends.opensearch import OpenSearchBackend
from general_manager.search.backends.typesense import TypesenseBackend


class SearchBackendStubTests(SimpleTestCase):
    def test_opensearch_backend_methods_raise(self) -> None:
        backend = object.__new__(OpenSearchBackend)
        with pytest.raises(SearchBackendNotImplementedError):
            OpenSearchBackend.__init__(backend)
        with pytest.raises(SearchBackendNotImplementedError):
            backend.ensure_index("index", {})
        with pytest.raises(SearchBackendNotImplementedError):
            backend.upsert("index", [])
        with pytest.raises(SearchBackendNotImplementedError):
            backend.delete("index", [])
        with pytest.raises(SearchBackendNotImplementedError):
            backend.search("index", "query")

    def test_typesense_backend_methods_raise(self) -> None:
        backend = object.__new__(TypesenseBackend)
        with pytest.raises(SearchBackendNotImplementedError):
            TypesenseBackend.__init__(backend)
        with pytest.raises(SearchBackendNotImplementedError):
            backend.ensure_index("index", {})
        with pytest.raises(SearchBackendNotImplementedError):
            backend.upsert("index", [])
        with pytest.raises(SearchBackendNotImplementedError):
            backend.delete("index", [])
        with pytest.raises(SearchBackendNotImplementedError):
            backend.search("index", "query")
