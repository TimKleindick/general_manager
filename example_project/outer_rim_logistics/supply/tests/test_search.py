from __future__ import annotations

from django.core.management import call_command
from django.test import TestCase

from general_manager.search.backend_registry import get_search_backend
from general_manager.utils.testing import run_registered_startup_hooks
from supply.managers import HazardClass, PartCatalog, VendorCatalog


class SearchIndexTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        run_registered_startup_hooks(
            managers=[HazardClass, PartCatalog, VendorCatalog]
        )
        call_command("seed_outer_rim")

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
