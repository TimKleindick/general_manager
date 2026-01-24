from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from general_manager.manager.general_manager import GeneralManager
from general_manager.search.config import IndexConfig
from tests.utils.simple_manager_interface import BaseTestInterface


class DummyManager(GeneralManager):
    Interface = BaseTestInterface

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(name="global", fields=["name"], filters=["status"])
        ]


class SearchCommandTests(SimpleTestCase):
    @patch("general_manager.management.commands.search_index.iter_searchable_managers")
    @patch("general_manager.management.commands.search_index.get_search_backend")
    @patch("general_manager.management.commands.search_index.SearchIndexer")
    def test_search_index_reindex(self, mock_indexer, mock_backend, mock_iter):
        mock_iter.return_value = [DummyManager]
        backend_instance = MagicMock()
        mock_backend.return_value = backend_instance
        indexer_instance = MagicMock()
        mock_indexer.return_value = indexer_instance

        call_command("search_index", "--reindex", "--index", "global")

        backend_instance.ensure_index.assert_called()
        indexer_instance.reindex_manager.assert_called_with(DummyManager)
