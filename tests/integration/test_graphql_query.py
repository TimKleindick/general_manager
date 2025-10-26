# type: ignore
from django.contrib.auth import get_user_model
from django.db.models import CharField, DateField, ForeignKey, CASCADE
from django.utils.crypto import get_random_string
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface.database_interface import DatabaseInterface
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
