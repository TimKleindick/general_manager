from __future__ import annotations

from django.core.management import call_command
from django.test import TestCase

from general_manager.interface.capabilities.read_only.management import (
    ReadOnlyManagementCapability,
)
from general_manager.search.backend_registry import get_search_backend
from outer_rim_logistics.supply.managers import HazardClass, PartCatalog, VendorCatalog


class SearchIndexTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        capability = ReadOnlyManagementCapability()
        capability.sync_data(HazardClass.Interface)
        capability.sync_data(PartCatalog.Interface)
        capability.sync_data(VendorCatalog.Interface)
        call_command("seed_outer_rim")
        call_command("search_index", reindex=True)

    def test_global_search_returns_hits(self) -> None:
        backend = get_search_backend()
        results = backend.search("global", "Corellian")
        self.assertGreater(results.total, 0)
        types = {hit.type for hit in results.hits}
        self.assertTrue(types)

    def test_orderable_search_returns_vendor(self) -> None:
        backend = get_search_backend()
        results = backend.search("orderable", "Bespin")
        self.assertGreater(results.total, 0)
        types = {hit.type for hit in results.hits}
        self.assertIn("VendorCatalog", types)
