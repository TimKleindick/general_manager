# type: ignore
from typing import ClassVar

from django.db.models import CharField
from django.core.management import call_command

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
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
                    IndexConfig(
                        name="global",
                        fields=["name", "status"],
                        filters=["status"],
                        sorts=["name"],
                    )
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
                    IndexConfig(
                        name="global",
                        fields=["name", "status"],
                        filters=["status"],
                        sorts=["name"],
                    )
                ]

        cls.general_manager_classes = [Project, ProjectTeam]
        cls.Project = Project
        cls.ProjectTeam = ProjectTeam
        GeneralManagerMeta.all_classes = cls.general_manager_classes

    def setUp(self):
        super().setUp()
        backend = DevSearchBackend()
        configure_search_backend(backend)
        self.Project.Factory.create(name="Alpha Project", status="public")
        self.Project.Factory.create(name="Beta Project", status="private")
        self.ProjectTeam.Factory.create(name="Alpha Team", status="public")
        self.ProjectTeam.Factory.create(name="Beta Team", status="public")
        self.ProjectTeam.Factory.create(name="Gamma Team", status="public")
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

    def test_graphql_search_filters_dict(self):
        query = """
        query {
            search(index: "global", query: "", filters: "{\\"status\\": \\"public\\"}") {
                total
                results { __typename ... on ProjectType { name status } ... on ProjectTeamType { name status } }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["search"]
        self.assertEqual(payload["total"], 4)
        statuses = {item["status"] for item in payload["results"]}
        self.assertEqual(statuses, {"public"})

    def test_graphql_search_filters_list(self):
        query = """
        query {
            search(index: "global", query: "", filters: "[{\\"field\\": \\"status\\", \\"value\\": \\"public\\"}]") {
                total
                results { __typename ... on ProjectType { name status } ... on ProjectTeamType { name status } }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["search"]
        self.assertEqual(payload["total"], 4)
        statuses = {item["status"] for item in payload["results"]}
        self.assertEqual(statuses, {"public"})

    def test_graphql_search_sorting(self):
        query = """
        query {
            search(index: "global", query: "", sortBy: "name") {
                results {
                    __typename
                    ... on ProjectType { name }
                    ... on ProjectTeamType { name }
                }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["search"]
        names = [item["name"] for item in payload["results"]]
        self.assertEqual(
            names,
            ["Alpha Project", "Alpha Team", "Beta Project", "Beta Team", "Gamma Team"],
        )

    def test_graphql_search_pagination(self):
        query = """
        query {
            search(index: "global", query: "", sortBy: "name", page: 2, pageSize: 2) {
                results {
                    __typename
                    ... on ProjectType { name }
                    ... on ProjectTeamType { name }
                }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["search"]
        names = [item["name"] for item in payload["results"]]
        self.assertEqual(names, ["Beta Project", "Beta Team"])

    def test_graphql_search_sort_desc(self):
        query = """
        query {
            search(index: "global", query: "", sortBy: "name", sortDesc: true) {
                results {
                    __typename
                    ... on ProjectType { name }
                    ... on ProjectTeamType { name }
                }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["search"]
        names = [item["name"] for item in payload["results"]]
        self.assertEqual(
            names,
            ["Gamma Team", "Beta Team", "Beta Project", "Alpha Team", "Alpha Project"],
        )

    def test_graphql_search_types_filter(self):
        query = """
        query {
            search(index: "global", query: "Alpha", types: ["Project"]) {
                total
                results { __typename ... on ProjectType { name } }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["search"]
        self.assertEqual(payload["total"], 1)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["__typename"], "ProjectType")


class TestGraphQLSearchPermissionIntegration(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class SecuredProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=200)
                status = CharField(max_length=50)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["matches:status:public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

            class SearchConfig:
                indexes: ClassVar[list[IndexConfig]] = [
                    IndexConfig(
                        name="global",
                        fields=["name", "status"],
                        filters=["status"],
                    )
                ]

        cls.general_manager_classes = [SecuredProject]
        cls.SecuredProject = SecuredProject
        GeneralManagerMeta.all_classes = cls.general_manager_classes

    def setUp(self):
        super().setUp()
        backend = DevSearchBackend()
        configure_search_backend(backend)
        self.SecuredProject.Factory.create(name="Public Project", status="public")
        self.SecuredProject.Factory.create(name="Private Project", status="private")
        indexer = SearchIndexer(backend)
        indexer.reindex_manager(self.SecuredProject)

    def tearDown(self):
        configure_search_backend(None)
        super().tearDown()

    def test_graphql_search_applies_permission_filters(self):
        query = """
        query {
            search(index: "global", query: "") {
                total
                results { __typename ... on SecuredProjectType { name status } }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["search"]
        self.assertEqual(payload["total"], 1)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["status"], "public")


class TestGraphQLSearchPermissionAcrossManagersIntegration(
    GeneralManagerTransactionTestCase
):
    @classmethod
    def setUpClass(cls):
        class PublicProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=200)
                status = CharField(max_length=50)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["matches:status:public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

            class SearchConfig:
                indexes: ClassVar[list[IndexConfig]] = [
                    IndexConfig(
                        name="global",
                        fields=["name", "status"],
                        filters=["status"],
                    )
                ]

        class InternalProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=200)
                status = CharField(max_length=50)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["matches:status:internal"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

            class SearchConfig:
                indexes: ClassVar[list[IndexConfig]] = [
                    IndexConfig(
                        name="global",
                        fields=["name", "status"],
                        filters=["status"],
                    )
                ]

        cls.general_manager_classes = [PublicProject, InternalProject]
        cls.PublicProject = PublicProject
        cls.InternalProject = InternalProject
        GeneralManagerMeta.all_classes = cls.general_manager_classes

    def setUp(self):
        super().setUp()
        backend = DevSearchBackend()
        configure_search_backend(backend)
        self.PublicProject.Factory.create(name="Public Alpha", status="public")
        self.PublicProject.Factory.create(name="Public Beta", status="private")
        self.InternalProject.Factory.create(name="Internal Alpha", status="internal")
        self.InternalProject.Factory.create(name="Internal Beta", status="public")
        indexer = SearchIndexer(backend)
        indexer.reindex_manager(self.PublicProject)
        indexer.reindex_manager(self.InternalProject)

    def tearDown(self):
        configure_search_backend(None)
        super().tearDown()

    def test_graphql_search_respects_permission_filters_per_manager(self):
        query = """
        query {
            search(index: "global", query: "") {
                total
                results {
                    __typename
                    ... on PublicProjectType { name status }
                    ... on InternalProjectType { name status }
                }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["search"]
        names = {item["name"] for item in payload["results"]}
        self.assertEqual(names, {"Public Alpha", "Internal Alpha"})


class TestSearchIndexCommandIntegration(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class CommandProject(GeneralManager):
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
                        name="global",
                        fields=["name", "status"],
                        filters=["status"],
                    )
                ]

        cls.general_manager_classes = [CommandProject]
        cls.CommandProject = CommandProject
        GeneralManagerMeta.all_classes = cls.general_manager_classes

    def setUp(self):
        super().setUp()
        self.backend = DevSearchBackend()
        configure_search_backend(self.backend)
        self.CommandProject.Factory.create(name="Indexed Project", status="public")

    def tearDown(self):
        configure_search_backend(None)
        super().tearDown()

    def test_search_index_command_reindexes(self):
        call_command("search_index", "--reindex")
        result = self.backend.search("global", query="")
        self.assertEqual(result.total, 1)
