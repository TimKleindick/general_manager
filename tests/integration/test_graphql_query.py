# type: ignore
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import CharField, DateField, ForeignKey, CASCADE
from django.utils.crypto import get_random_string
from general_manager.api.property import graph_ql_property
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface import DatabaseInterface
from general_manager.measurement.measurement_field import MeasurementField
from general_manager.permission.manager_based_permission import (
    AdditiveManagerPermission,
)
from general_manager.utils.testing import (
    GeneralManagerTransactionTestCase,
)


class TestGraphQLQueryPagination(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Register two test GeneralManager models, Commercials and Project, with their database interfaces and relationship.

        Commercials exposes name, capex, opex, and a nullable date. Project exposes name, optional description, and a ForeignKey to Commercials. Stores the created classes on the test class as `general_manager_classes`, `project`, and `commercials`.
        """

        class Commercials(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                capex = MeasurementField("USD")
                opex = MeasurementField("USD")
                date = DateField(null=True, blank=True)

        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                description = CharField(max_length=500, null=True, blank=True)
                commercials = ForeignKey(
                    "general_manager.Commercials",
                    on_delete=CASCADE,
                )

            @graph_ql_property(sortable=True)
            def name_length(self) -> int:
                return len(self.name)

        cls.general_manager_classes = [Commercials, Project]
        cls.project = Project
        cls.commercials = Commercials

    def setUp(self):
        """
        Set up the test environment by creating and logging in a test user and creating 10 Commercials instances.

        Creates a user with a randomly generated 12-character password, logs the test client in as that user, and populates the Commercials model via its Factory with 10 instances.
        """
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="testuser", password=password
        )
        self.client.login(username="testuser", password=password)

        self.commercials.Factory.create_batch(10)

    def _create_projects_for_relation_sorting(self):
        zulu = self.commercials.create(
            creator_id=self.user.id,
            name="Zulu",
            capex="1 USD",
            opex="1 USD",
        )
        alpha = self.commercials.create(
            creator_id=self.user.id,
            name="Alpha",
            capex="1 USD",
            opex="1 USD",
        )
        beta = self.project.create(
            creator_id=self.user.id,
            name="Beta",
            description=None,
            commercials=zulu,
        )
        zed = self.project.create(
            creator_id=self.user.id,
            name="Zed",
            description=None,
            commercials=alpha,
        )
        able = self.project.create(
            creator_id=self.user.id,
            name="Able",
            description=None,
            commercials=alpha,
        )
        return alpha, zulu, (beta, zed, able)

    def test_query_commercials(self):
        """
        Tests that the GraphQL query for `commercialsList` returns all commercial items with correct fields and pagination metadata.

        Verifies that 10 commercial items are returned, each with expected fields, and that pagination info includes the correct total count.
        """
        query = """
        query {
            commercialsList {
                items {
                    id
                    name
                    capex {
                        value
                        unit
                    }
                    opex {
                        value
                        unit
                    }
                    date
                }
                pageInfo {
                    totalCount
                    currentPage
                    totalPages
                }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertIn("commercialsList", data)
        self.assertIn("items", data["commercialsList"])
        self.assertEqual(len(data["commercialsList"]["items"]), 10)
        self.assertIn("pageInfo", data["commercialsList"])
        self.assertIn("totalCount", data["commercialsList"]["pageInfo"])
        self.assertEqual(data["commercialsList"]["pageInfo"]["totalCount"], 10)

    def test_query_commercials_with_pagination(self):
        """
        Test that the GraphQL query for `commercialsList` with pagination returns the correct number of items and pagination metadata.

        Verifies that requesting page 1 with a page size of 5 returns 5 items, and that pagination info reflects the total count and total number of pages.
        """
        query = """
        query {
            commercialsList(page: 1, pageSize: 5) {
                items {
                    id
                    name
                    capex {
                        value
                        unit
                    }
                    opex {
                        value
                        unit
                    }
                    date
                }
                pageInfo {
                    totalCount
                    currentPage
                    totalPages
                }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertIn("commercialsList", data)
        self.assertIn("items", data["commercialsList"])
        self.assertEqual(len(data["commercialsList"]["items"]), 5)
        self.assertIn("pageInfo", data["commercialsList"])
        self.assertIn("totalCount", data["commercialsList"]["pageInfo"])
        self.assertEqual(data["commercialsList"]["pageInfo"]["totalCount"], 10)
        self.assertEqual(data["commercialsList"]["pageInfo"]["totalPages"], 2)

    def test_page_without_size_uses_effective_default_size(self):
        response = self.query(
            """
            query {
              commercialsList(page: 2) {
                items { id }
                pageInfo { pageSize currentPage totalPages totalCount }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["commercialsList"]
        self.assertEqual(payload["items"], [])
        self.assertEqual(
            payload["pageInfo"],
            {"pageSize": 10, "currentPage": 2, "totalPages": 1, "totalCount": 10},
        )

    def test_list_rejects_zero_pagination_values(self):
        for arguments in ("page: 0", "pageSize: 0"):
            with self.subTest(arguments=arguments):
                response = self.query(
                    f"""
                    query {{
                      commercialsList({arguments}) {{ items {{ id }} }}
                    }}
                    """
                )

                self.assertResponseHasErrors(response)
                self.assertIn(
                    "must be a positive integer",
                    response.json()["errors"][0]["message"],
                )

    def test_project_list_sorts_direct_manager_by_identifier(self):
        alpha, zulu, _projects = self._create_projects_for_relation_sorting()
        query = """
        query {
          projectList(orderBy: [{field: commercials}]) {
            items { commercials { id } }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["projectList"]["items"]
        commercial_ids = [int(item["commercials"]["id"]) for item in items]
        self.assertEqual(
            commercial_ids,
            sorted([alpha.id, alpha.id, zulu.id]),
        )

    def test_project_list_sorts_by_related_scalar_then_root_field(self):
        self._create_projects_for_relation_sorting()
        query = """
        query {
          projectList(orderBy: [{field: commercials__name}, {field: name}]) {
            items { name }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["projectList"]["items"]
        self.assertEqual([item["name"] for item in items], ["Able", "Zed", "Beta"])

    def test_project_list_reverses_every_compound_sort_key(self):
        self._create_projects_for_relation_sorting()
        query = """
        query {
          projectList(
            orderBy: [
              {field: commercials__name, direction: DESC}
              {field: name, direction: DESC}
            ]
          ) {
            items { name }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["projectList"]["items"]
        self.assertEqual([item["name"] for item in items], ["Beta", "Zed", "Able"])

    def test_project_list_sorts_python_property_then_related_scalar(self):
        self._create_projects_for_relation_sorting()
        query = """
        query {
          projectList(orderBy: [{field: nameLength}, {field: commercials__name}]) {
            items { name }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["projectList"]["items"]
        self.assertEqual([item["name"] for item in items], ["Zed", "Able", "Beta"])

    def test_project_list_reverses_python_property_and_related_scalar(self):
        self._create_projects_for_relation_sorting()
        query = """
        query {
          projectList(
            orderBy: [
              {field: nameLength, direction: DESC}
              {field: commercials__name, direction: DESC}
            ]
          ) {
            items { name }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["projectList"]["items"]
        self.assertEqual([item["name"] for item in items], ["Beta", "Able", "Zed"])

    def test_project_list_python_property_fallback_resolves_manager_identifier(self):
        self._create_projects_for_relation_sorting()
        query = """
        query {
          projectList(orderBy: [{field: nameLength}, {field: commercials}]) {
            items { name }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["projectList"]["items"]
        self.assertEqual([item["name"] for item in items], ["Zed", "Beta", "Able"])

    def test_project_list_accepts_compound_sort_variable(self):
        self._create_projects_for_relation_sorting()
        query = """
        query SortedProjects($order: [ProjectOrderBy!]) {
          projectList(orderBy: $order) {
            items { name }
          }
        }
        """

        response = self.query(
            query,
            variables={
                "order": [
                    {"field": "commercials__name"},
                    {"field": "name"},
                ]
            },
        )

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["projectList"]["items"]
        self.assertEqual([item["name"] for item in items], ["Able", "Zed", "Beta"])

    def test_null_empty_and_omitted_sort_inputs_preserve_the_same_rows(self):
        _alpha, _zulu, projects = self._create_projects_for_relation_sorting()
        query = """
        query {
          nullSort: projectList(orderBy: null) { items { id } }
          emptySort: projectList(orderBy: []) { items { id } }
          omittedSort: projectList { items { id } }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        data = response.json()["data"]
        returned_ids = {
            key: [item["id"] for item in data[key]["items"]]
            for key in ("nullSort", "emptySort", "omittedSort")
        }
        self.assertEqual(returned_ids["nullSort"], returned_ids["omittedSort"])
        self.assertEqual(returned_ids["emptySort"], returned_ids["omittedSort"])
        self.assertCountEqual(
            returned_ids["omittedSort"],
            [project.id for project in projects],
        )

    def test_sort_list_rejects_null_elements(self):
        response = self.query(
            """
            query {
              projectList(orderBy: [{field: name}, null]) { items { id } }
            }
            """
        )

        self.assertResponseHasErrors(response)
        errors = response.json()["errors"]
        self.assertIn("Expected value of type 'ProjectOrderBy!'", errors[0]["message"])
        self.assertIn("found null", errors[0]["message"])

    def test_project_order_enum_exposes_only_direct_relation_scalars(self):
        query = """
        query {
          projectOptions: __type(name: "ProjectOrderField") {
            enumValues { name }
          }
          commercialsOptions: __type(name: "CommercialsOrderField") {
            enumValues { name }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        data = response.json()["data"]
        project_values = {
            value["name"] for value in data["projectOptions"]["enumValues"]
        }
        commercials_values = {
            value["name"] for value in data["commercialsOptions"]["enumValues"]
        }
        self.assertIn("commercials", project_values)
        self.assertIn("commercials__name", project_values)
        self.assertFalse(
            any(
                value.startswith("commercials__") and value.count("__") > 1
                for value in project_values
            )
        )
        self.assertNotIn("project_list", commercials_values)

    def test_relation_list_uses_scoped_typed_order_input(self):
        query = """
        query {
          __type(name: "CommercialsType") {
            fields {
              name
              args {
                name
                type {
                  kind
                  ofType {
                    kind
                    ofType { name }
                  }
                }
              }
            }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        fields = response.json()["data"]["__type"]["fields"]
        project_list = next(field for field in fields if field["name"] == "projectList")
        order_by = next(arg for arg in project_list["args"] if arg["name"] == "orderBy")
        self.assertEqual(order_by["type"]["kind"], "LIST")
        self.assertEqual(order_by["type"]["ofType"]["kind"], "NON_NULL")
        self.assertEqual(
            order_by["type"]["ofType"]["ofType"]["name"],
            "ProjectRelationOrderBy",
        )

    def test_project_order_input_requires_field_and_defaults_direction_to_asc(self):
        response = self.query(
            """
            query {
              __type(name: "ProjectOrderBy") {
                inputFields {
                  name
                  defaultValue
                  type { kind name ofType { kind name } }
                }
              }
              queryType: __type(name: "Query") {
                fields { name args { name } }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        fields = response.json()["data"]["__type"]["inputFields"]
        field = next(item for item in fields if item["name"] == "field")
        direction = next(item for item in fields if item["name"] == "direction")
        self.assertEqual(field["type"]["kind"], "NON_NULL")
        self.assertEqual(field["type"]["ofType"]["name"], "ProjectOrderField")
        self.assertEqual(direction["type"]["kind"], "NON_NULL")
        self.assertEqual(direction["type"]["ofType"]["name"], "OrderDirection")
        self.assertEqual(direction["defaultValue"], "ASC")
        query_fields = response.json()["data"]["queryType"]["fields"]
        project_list = next(
            field for field in query_fields if field["name"] == "projectList"
        )
        argument_names = {argument["name"] for argument in project_list["args"]}
        self.assertIn("orderBy", argument_names)
        self.assertNotIn("sortBy", argument_names)
        self.assertNotIn("reverse", argument_names)
        self.assertNotIn("groupBy", argument_names)

    def test_query_commercials_with_project_list(self):
        """
        Tests that querying the commercials list with nested project lists returns correct items and pagination metadata for both levels.

        Verifies that each commercial includes its related projects, and that the number of items matches the reported total counts in the pagination info for both commercials and projects.
        """
        self.project.Factory.create_batch(5)

        query = """
        query {
            commercialsList {
                items {
                    id
                    name
                    capex {
                        value
                        unit
                    }
                    opex {
                        value
                        unit
                    }
                    date
                    projectList {
                        items {
                            id
                            name
                        }
                        pageInfo {
                            totalCount
                            currentPage
                            totalPages
                        }
                    }
                }
                pageInfo {
                    totalCount
                    currentPage
                    totalPages
                }
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertIn("commercialsList", data)
        self.assertIn("items", data["commercialsList"])
        self.assertEqual(
            len(data["commercialsList"]["items"]),
            data["commercialsList"]["pageInfo"]["totalCount"],
        )
        for item in data["commercialsList"]["items"]:
            self.assertIn("projectList", item)
            self.assertIn("items", item["projectList"])
            self.assertEqual(
                len(item["projectList"]["items"]),
                item["projectList"]["pageInfo"]["totalCount"],
            )

    def test_generated_relation_list_executes_compound_sort(self):
        parent = self.commercials.create(
            creator_id=self.user.id,
            name="Nested Parent",
            capex="1 USD",
            opex="1 USD",
        )
        for name in ("Zed", "Able", "Bob"):
            self.project.create(
                creator_id=self.user.id,
                name=name,
                description=None,
                commercials=parent,
            )
        query = """
        query {
          commercialsList(filter: {name: "Nested Parent"}) {
            items {
              projectList(orderBy: [{field: nameLength}, {field: commercials__name}, {field: name}]) {
                items { name }
              }
            }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        parents = response.json()["data"]["commercialsList"]["items"]
        self.assertEqual(len(parents), 1)
        items = parents[0]["projectList"]["items"]
        self.assertEqual([item["name"] for item in items], ["Bob", "Zed", "Able"])


class TestGraphQLIncludeInactive(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class SoftFamily(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

                class Meta:
                    use_soft_delete = True

        cls.general_manager_classes = [SoftFamily]
        cls.soft_family = SoftFamily

    def setUp(self):
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="inactive-user", password=password
        )
        self.client.login(username="inactive-user", password=password)

        self.active_family = self.soft_family.create(
            creator_id=None,
            name="Active Family",
            ignore_permission=True,
        )
        self.inactive_family = self.soft_family.create(
            creator_id=None,
            name="Inactive Family",
            ignore_permission=True,
        )
        self.inactive_family.delete(ignore_permission=True)

    def test_query_include_inactive_returns_soft_deleted_rows(self):
        query_default = """
        query {
            softFamilyList {
                items {
                    id
                    name
                }
                pageInfo {
                    totalCount
                }
            }
        }
        """
        default_response = self.query(query_default)
        self.assertResponseNoErrors(default_response)
        default_data = default_response.json()["data"]["softFamilyList"]
        default_names = {item["name"] for item in default_data["items"]}
        self.assertEqual(default_names, {"Active Family"})
        self.assertEqual(default_data["pageInfo"]["totalCount"], 1)

        query_with_inactive = """
        query {
            softFamilyList(includeInactive: true) {
                items {
                    id
                    name
                }
                pageInfo {
                    totalCount
                }
            }
        }
        """
        include_response = self.query(query_with_inactive)
        self.assertResponseNoErrors(include_response)
        include_data = include_response.json()["data"]["softFamilyList"]
        include_names = {item["name"] for item in include_data["items"]}
        self.assertEqual(include_names, {"Active Family", "Inactive Family"})
        self.assertEqual(include_data["pageInfo"]["totalCount"], 2)


class TestGraphQLQueryReadHardening(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class InternalRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["isAdmin"]

        cls.general_manager_classes = [InternalRecord]
        cls.internal_record = InternalRecord

    def setUp(self):
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="read-hardening-user", password=password
        )
        self.client.login(username="read-hardening-user", password=password)
        self.internal_record.Factory.create_batch(2)

    def test_non_admin_list_query_hides_rows_and_total_count(self):
        query = """
        query {
            internalRecordList {
                items {
                    id
                    name
                }
                pageInfo {
                    totalCount
                }
            }
        }
        """

        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["internalRecordList"]
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["pageInfo"]["totalCount"], 0)

    def test_non_admin_list_query_skips_row_permission_checks_and_logging(self):
        query = """
        query {
            internalRecordList {
                pageInfo {
                    totalCount
                }
            }
        }
        """

        with (
            patch("general_manager.api.graphql_resolvers.logger") as logger_mock,
            patch.object(
                self.internal_record.Permission,
                "can_read_instance",
                side_effect=AssertionError("deny_all must not check candidate rows"),
            ),
        ):
            response = self.query(query)

        self.assertResponseNoErrors(response)
        contexts = [call.kwargs["context"] for call in logger_mock.info.call_args_list]
        matching = [
            context
            for context in contexts
            if context.get("source") == "list"
            and context.get("manager") == "InternalRecord"
        ]
        self.assertEqual(matching, [])

    def test_admin_list_query_skips_row_permission_checks_and_logging(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        query = """
        query {
            internalRecordList {
                items {
                    id
                }
                pageInfo {
                    totalCount
                }
            }
        }
        """

        with (
            patch("general_manager.api.graphql_resolvers.logger") as logger_mock,
            patch.object(
                self.internal_record.Permission,
                "can_read_instance",
                side_effect=AssertionError("allow_all must not check candidate rows"),
            ),
        ):
            response = self.query(query)

        self.assertResponseNoErrors(response)
        contexts = [call.kwargs["context"] for call in logger_mock.info.call_args_list]
        matching = [
            context
            for context in contexts
            if context.get("source") == "list"
            and context.get("manager") == "InternalRecord"
        ]
        self.assertEqual(matching, [])


class TestGraphQLQueryBasedOnReadHardening(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class RestrictedProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["isAdmin"]

        class DelegatedDocument(GeneralManager):
            class Interface(DatabaseInterface):
                title = CharField(max_length=100)
                project = ForeignKey(
                    "general_manager.RestrictedProject",
                    on_delete=CASCADE,
                )

            class Permission(AdditiveManagerPermission):
                __based_on__: ClassVar[str] = "project"
                __read__: ClassVar[list[str]] = ["public"]

        cls.general_manager_classes = [RestrictedProject, DelegatedDocument]
        cls.restricted_project = RestrictedProject
        cls.delegated_document = DelegatedDocument

    def setUp(self):
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="based-on-read-hardening-user",
            password=password,
        )
        self.client.login(username="based-on-read-hardening-user", password=password)
        project = self.restricted_project.Factory.create(name="Hidden Project")
        self.delegated_document.Factory.create(
            title="Hidden Spec",
            project=project,
        )

    def test_non_admin_list_query_hides_based_on_denied_rows_and_total_count(self):
        query = """
        query {
            delegatedDocumentList {
                items {
                    id
                    title
                }
                pageInfo {
                    totalCount
                }
            }
        }
        """

        response = self.query(query)
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["delegatedDocumentList"]
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["pageInfo"]["totalCount"], 0)

    def test_admin_list_query_skips_static_based_on_row_checks_and_logging(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        query = """
        query {
            delegatedDocumentList {
                items {
                    id
                }
                pageInfo {
                    totalCount
                }
            }
        }
        """

        with (
            patch("general_manager.api.graphql_resolvers.logger") as logger_mock,
            patch.object(
                self.delegated_document.Permission,
                "can_read_instance",
                side_effect=AssertionError("allow_all must not check candidate rows"),
            ),
        ):
            response = self.query(query)

        self.assertResponseNoErrors(response)
        contexts = [call.kwargs["context"] for call in logger_mock.info.call_args_list]
        matching = [
            context
            for context in contexts
            if context.get("source") == "list"
            and context.get("manager") == "DelegatedDocument"
        ]
        self.assertEqual(matching, [])


class TestGraphQLIncludeInactiveValidation(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class HardFamily(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        cls.general_manager_classes = [HardFamily]
        cls.hard_family = HardFamily

    def setUp(self):
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="hard-family-user", password=password
        )
        self.client.login(username="hard-family-user", password=password)
        self.hard_family.create(
            creator_id=None,
            name="Only Active",
            ignore_permission=True,
        )

    def test_query_include_inactive_fails_without_soft_delete(self):
        query = """
        query {
            hardFamilyList(includeInactive: true) {
                items {
                    id
                    name
                }
                pageInfo {
                    totalCount
                }
            }
        }
        """
        response = self.query(query)
        self.assertResponseHasErrors(response)
        errors = response.json().get("errors", [])
        self.assertTrue(errors)
        self.assertIn("Unknown argument", errors[0].get("message", ""))
        self.assertIn("includeInactive", errors[0].get("message", ""))
