# type: ignore
from __future__ import annotations

import os
from contextlib import suppress
from typing import ClassVar
from uuid import uuid4

from django.db.models import CharField

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from general_manager.utils.testing import GeneralManagerTransactionTestCase

try:
    import meilisearch  # type: ignore[import]
except ImportError:  # pragma: no cover - optional dependency
    meilisearch = None


MEILI_URL = os.getenv("MEILISEARCH_URL")
MEILI_API_KEY = os.getenv("MEILISEARCH_API_KEY")
MEILI_AVAILABLE = meilisearch is not None and bool(MEILI_URL)


class TestGraphQLSearchMeilisearchIntegration(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        cls.index_name = f"gm_test_{uuid4().hex}"

        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=200)
                status = CharField(max_length=50)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

            class SearchConfig:
                indexes: ClassVar[list[IndexConfig]] = [
                    IndexConfig(
                        name=cls.index_name,
                        fields=["name", "status"],
                        filters=["status"],
                    )
                ]

        cls.general_manager_classes = [Project]
        cls.Project = Project
        GeneralManagerMeta.all_classes = cls.general_manager_classes

    def setUp(self):
        super().setUp()
        if not MEILI_AVAILABLE:
            self.skipTest("Meilisearch not available; set MEILISEARCH_URL to run.")
        from general_manager.search.backends.meilisearch import MeilisearchBackend

        self.backend = MeilisearchBackend(url=MEILI_URL, api_key=MEILI_API_KEY)
        try:
            self.backend.ensure_index(self.index_name, {})
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"Meilisearch not reachable: {exc}")
        configure_search_backend(self.backend)
        self.Project.Factory.create(name="Alpha Project", status="public")
        indexer = SearchIndexer(self.backend)
        indexer.reindex_manager(self.Project)

    def tearDown(self):
        backend = getattr(self, "backend", None)
        if backend is not None:
            with suppress(Exception):
                backend._client.delete_index(self.index_name)
        configure_search_backend(None)
        super().tearDown()

    def test_graphql_search_meilisearch_backend(self):
        query = f"""
        query {{
            search(index: "{self.index_name}", query: "Alpha") {{
                total
                results {{ __typename ... on ProjectType {{ name status }} }}
            }}
        }}
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["search"]
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["results"][0]["name"], "Alpha Project")
