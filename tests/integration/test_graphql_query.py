# type: ignore
from django.contrib.auth import get_user_model
from django.db.models import CharField, IntegerField, DateField, ForeignKey, CASCADE
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.measurement.measurementField import MeasurementField
from general_manager.utils.testing import (
    GeneralManagerTransactionTestCase,
)
from general_manager.permission.managerBasedPermission import ManagerBasedPermission


class TestGraphQLQueryPagination(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
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
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="testuser", password="testpassword"
        )
        self.client.login(username="testuser", password="testpassword")

        self.commercials.Factory.create_batch(10)

    def test_query_commercials(self):
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
