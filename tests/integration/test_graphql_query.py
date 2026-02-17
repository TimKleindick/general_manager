# type: ignore
from django.contrib.auth import get_user_model
from django.db.models import CharField, DateField, ForeignKey, CASCADE
from django.utils.crypto import get_random_string
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface import DatabaseInterface
from general_manager.measurement.measurement_field import MeasurementField
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
            softfamilyList {
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
        default_data = default_response.json()["data"]["softfamilyList"]
        default_names = {item["name"] for item in default_data["items"]}
        self.assertEqual(default_names, {"Active Family"})
        self.assertEqual(default_data["pageInfo"]["totalCount"], 1)

        query_with_inactive = """
        query {
            softfamilyList(includeInactive: true) {
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
        include_data = include_response.json()["data"]["softfamilyList"]
        include_names = {item["name"] for item in include_data["items"]}
        self.assertEqual(include_names, {"Active Family", "Inactive Family"})
        self.assertEqual(include_data["pageInfo"]["totalCount"], 2)


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
            hardfamilyList(includeInactive: true) {
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
