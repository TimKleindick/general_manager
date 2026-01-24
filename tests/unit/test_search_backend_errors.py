from __future__ import annotations

from django.test import SimpleTestCase

from general_manager.search.backend import (
    SearchBackendClientMissingError,
    SearchBackendNotConfiguredError,
    SearchBackendNotImplementedError,
)


class SearchBackendErrorTests(SimpleTestCase):
    def test_search_backend_not_implemented_error_message(self) -> None:
        err = SearchBackendNotImplementedError("OpenSearch")
        assert "OpenSearch backend is not implemented yet." == str(err)

    def test_search_backend_not_configured_error_message(self) -> None:
        err = SearchBackendNotConfiguredError()
        assert str(err) == "No search backend configured."

    def test_search_backend_client_missing_error_message(self) -> None:
        err = SearchBackendClientMissingError("Meilisearch")
        assert "Meilisearch client is not installed." in str(err)
