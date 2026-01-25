from __future__ import annotations

from django.test import SimpleTestCase

from general_manager.search.backend import (
    SearchBackendClientMissingError,
    SearchBackendNotConfiguredError,
    SearchBackendNotImplementedError,
    _mask_backend_setting,
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

    def test_mask_backend_setting_mapping(self) -> None:
        setting = {
            "class": "backend",
            "options": {
                "api_key": "secret",
                "token": "tok",
                "url": "http://user:pass@example.com:7700",
            },
        }
        masked = _mask_backend_setting(setting)
        assert masked["options"]["api_key"] == "<masked>"
        assert masked["options"]["token"] == "<masked>"  # noqa: S105
        assert masked["options"]["url"] == "http://example.com:7700"

    def test_mask_backend_setting_string(self) -> None:
        assert _mask_backend_setting("user:pass") == "<masked>"
        assert _mask_backend_setting("token=abc") == "<masked>"
        assert _mask_backend_setting("http://user:pass@host:7700") == "http://host:7700"
