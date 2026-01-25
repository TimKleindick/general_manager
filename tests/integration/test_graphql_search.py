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
        super().setUpClass()
        """
        Register two GeneralManager test classes (Project and ProjectTeam) on the test class and set them as the global managers.

        Both managers declare `name` and `status` fields, grant public CRUD permissions, and configure a "global" search index over `name` and `status` with `status` as a filter and `name` as a sortable field. The created manager classes are assigned to `cls.Project`, `cls.ProjectTeam`, and `cls.general_manager_classes`, and `GeneralManagerMeta.all_classes` is updated accordingly.
        """

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
        """
        Prepare a DevSearchBackend, create sample Project and ProjectTeam records with varied statuses, and reindex both managers so the search index contains the seeded data.

        Creates:
        - Projects: "Alpha Project" (status "public"), "Beta Project" (status "private")
        - ProjectTeams: "Alpha Team", "Beta Team", "Gamma Team" (all status "public")
        """
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
        """
        Reset the test search backend and perform superclass teardown.

        This clears the configured search backend by setting it to None, then invokes the base class's teardown to complete test cleanup.
        """
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
        """
        Verify the GraphQL search returns results sorted by the `name` field in ascending order.

        Executes a search query against the "global" index with sorting by name and asserts the returned result names match the expected ascending sequence.
        """
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
        super().setUpClass()
        """
        Prepare test class by defining and registering a SecuredProject GeneralManager with interface, permissions, and search configuration.

        Defines an inner GeneralManager subclass named `SecuredProject` with:
        - Interface fields `name` and `status`.
        - Permission that allows read only when `matches:status:public` and allows create/update/delete publicly.
        - SearchConfig that registers a "global" index on fields `name` and `status` with `status` as a filter.

        Assigns `cls.general_manager_classes` and `cls.SecuredProject`, and sets `GeneralManagerMeta.all_classes` to include the new manager.
        """

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
        """
        Prepare the test environment by configuring a development search backend, creating one public and one private SecuredProject, and indexing them into the search backend.

        Creates:
        - "Public Project" with status "public"
        - "Private Project" with status "private"

        Then reindexes the SecuredProject manager into the configured DevSearchBackend so search queries operate on the seeded data.
        """
        super().setUp()
        backend = DevSearchBackend()
        configure_search_backend(backend)
        self.SecuredProject.Factory.create(name="Public Project", status="public")
        self.SecuredProject.Factory.create(name="Private Project", status="private")
        indexer = SearchIndexer(backend)
        indexer.reindex_manager(self.SecuredProject)

    def tearDown(self):
        """
        Reset the test search backend and perform superclass teardown.

        This clears the configured search backend by setting it to None, then invokes the base class's teardown to complete test cleanup.
        """
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
        super().setUpClass()
        """
        Define two test GeneralManager classes (PublicProject and InternalProject) with fields, permissions, and search indexes, and register them for use by the test class.

        Each manager exposes `name` and `status` fields, a SearchConfig containing a "global" index on `name` and `status` (with `status` as a filter), and permissions that restrict read access to either `status:public` (PublicProject) or `status:internal` (InternalProject). Assigns the classes to `cls.general_manager_classes`, exposes them as `cls.PublicProject` and `cls.InternalProject`, and sets `GeneralManagerMeta.all_classes` to the list of created manager classes.
        """

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
        """
        Prepare the test environment by configuring a DevSearchBackend, creating sample objects for PublicProject and InternalProject, and reindexing both managers.

        Creates sample project instances with varying statuses under the two managers and ensures the search backend is populated by reindexing PublicProject and InternalProject.
        """
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
        """
        Reset the test search backend and perform superclass teardown.

        This clears the configured search backend by setting it to None, then invokes the base class's teardown to complete test cleanup.
        """
        configure_search_backend(None)
        super().tearDown()

    def test_graphql_search_respects_permission_filters_per_manager(self):
        """
        Verifies that a GraphQL search applies each manager's read permission filters when querying across multiple managers.

        Asserts the search returns only items allowed by each manager's permissions (expecting "Public Alpha" from PublicProject and "Internal Alpha" from InternalProject).
        """
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
        super().setUpClass()
        """
        Define and register a CommandProject GeneralManager subclass for use by the test class.

        This class method creates an inner GeneralManager named CommandProject with a DatabaseInterface containing `name` and `status` fields, permissive ManagerBasedPermission rules for all CRUD operations, and a SearchConfig that declares a single "global" index on `["name", "status"]` with `["status"]` as a filter. It then stores the created manager class on the test class as `CommandProject`, collects it in `general_manager_classes`, and sets GeneralManagerMeta.all_classes to that collection.
        """

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
        """
        Prepare the test environment by configuring a DevSearchBackend as the active search backend and creating a CommandProject instance named "Indexed Project" with status "public" for indexing tests.
        """
        super().setUp()
        self.backend = DevSearchBackend()
        configure_search_backend(self.backend)
        self.CommandProject.Factory.create(name="Indexed Project", status="public")

    def tearDown(self):
        """
        Reset the test search backend and perform superclass teardown.

        This clears the configured search backend by setting it to None, then invokes the base class's teardown to complete test cleanup.
        """
        configure_search_backend(None)
        super().tearDown()

    def test_search_index_command_reindexes(self):
        call_command("search_index", "--reindex")
        result = self.backend.search("global", query="")
        self.assertEqual(result.total, 1)
