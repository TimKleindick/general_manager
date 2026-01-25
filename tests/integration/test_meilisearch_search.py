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
        """
        Prepare class-level state for the Meilisearch integration test by registering a dynamic Project GeneralManager and creating a unique index name.

        Sets the following class attributes:
        - _orig_gm_classes: original GeneralManagerMeta.all_classes value for later restoration.
        - index_name: a unique index name generated for the test.
        - Project: a dynamically defined GeneralManager subclass with an Interface (name, status), permissive ManagerBasedPermission, and a SearchConfig containing an IndexConfig that uses the generated index_name.
        - general_manager_classes: list containing the dynamic Project class.

        As a side effect, replaces GeneralManagerMeta.all_classes with the test's general_manager_classes so the test framework recognizes the dynamic manager.
        """
        super().setUpClass()
        cls._orig_gm_classes = GeneralManagerMeta.all_classes
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

    @classmethod
    def tearDownClass(cls) -> None:
        """
        Restore GeneralManagerMeta.all_classes to the value saved in setUpClass and run the superclass class-level teardown.

        This ensures any modifications to the global registry of manager classes performed by the test are reverted before the test class is torn down.
        """
        GeneralManagerMeta.all_classes = getattr(cls, "_orig_gm_classes", [])
        super().tearDownClass()

    def setUp(self):
        """
        Prepare a Meilisearch-backed test environment for each test.

        Ensures Meilisearch is importable and reachable (skips the test if not), creates/ensures the test index, configures the search backend to use the Meilisearch instance, creates a sample Project with name "Alpha Project" and status "public", and triggers reindexing for the Project manager.
        """
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
        """
        Clean up the test search backend and remove the test Meilisearch index if present.

        If a backend instance exists on the test case, attempts to delete the index created for the test and ignores any errors raised during deletion. Resets the configured search backend to None and invokes the superclass tearDown.
        """
        backend = getattr(self, "backend", None)
        if backend is not None:
            with suppress(Exception):
                backend._client.delete_index(self.index_name)
        configure_search_backend(None)
        super().tearDown()

    def test_graphql_search_meilisearch_backend(self):
        """
        Executes a GraphQL search against the Meilisearch-backed test index and verifies the expected result.

        Asserts that the GraphQL response contains no errors, that the search reports a total of 1 result, and that the first result's name is "Alpha Project".
        """
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
