# type: ignore
from typing import ClassVar

from django.db.models import CharField

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLSearchIntegration(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
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
                    IndexConfig(name="global", fields=["name"], filters=["status"])
                ]

        class ProjectTeam(GeneralManager):
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
                    IndexConfig(name="global", fields=["name"], filters=["status"])
                ]

        cls.general_manager_classes = [Project, ProjectTeam]
        cls.Project = Project
        cls.ProjectTeam = ProjectTeam

    def setUp(self):
        super().setUp()
        backend = DevSearchBackend()
        configure_search_backend(backend)
        self.Project.Factory.create(name="Alpha Project", status="public")
        self.ProjectTeam.Factory.create(name="Alpha Team", status="public")
        self.ProjectTeam.Factory.create(name="Beta Team", status="public")
        indexer = SearchIndexer(backend)
        indexer.reindex_manager(self.Project)
        indexer.reindex_manager(self.ProjectTeam)

    def tearDown(self):
        configure_search_backend(None)
        super().tearDown()

    def test_graphql_search_returns_union_results(self):
        query = """
        query {
            search(index: "global", query: "Alpha") {
                total
                results {
                    __typename
                    ... on ProjectType { id name status }
                    ... on ProjectTeamType { id name status }
                }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["search"]
        self.assertEqual(payload["total"], 2)
        self.assertEqual(len(payload["results"]), 2)
        type_names = {item["__typename"] for item in payload["results"]}
        self.assertEqual(type_names, {"ProjectType", "ProjectTeamType"})
