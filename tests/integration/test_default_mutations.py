from django.contrib.auth import get_user_model
from django.db.models import CharField, IntegerField
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.measurement.measurementField import MeasurementField
from general_manager.utils.testing import (
    GeneralManagerTransactionTestCase,
)


class DefaultCreateMutationTest(GeneralManagerTransactionTestCase):

    @classmethod
    def setUpClass(cls):

        class TestProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                number = IntegerField(null=True)
                budget = MeasurementField(
                    base_unit="EUR",
                )

                class Meta:
                    app_label = "general_manager"

        cls.TestProject = TestProject
        cls.general_manager_classes = [TestProject]

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="tester", password="geheim")
        self.client.force_login(self.user)
        self.create_mutation = """
        mutation CreateProject($name: String!, $number: Int, $budget: String) {
            createTestProject(name: $name, number: $number, budget: $budget) {
                TestProject {
                    name
                    number
                    budget {
                        value
                        unit
                    }
                }
                errors
                success
            }
        }
        """

    def test_create_project(self):
        variables = {
            "name": "Test Project",
            "number": 42,
            "budget": "2000 EUR",
        }

        response = self.query(self.create_mutation, variables=variables)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertTrue(data["createTestProject"]["success"])

        data = data["createTestProject"]["TestProject"]
        self.assertEqual(data["name"], "Test Project")
        self.assertEqual(data["number"], 42)
        self.assertEqual(data["budget"]["value"], 2000)
        self.assertEqual(data["budget"]["unit"], "EUR")

        self.assertEqual(len(self.TestProject.all()), 1)
        project = self.TestProject.all().first()
        self.assertEqual(project.name, "Test Project")
        self.assertEqual(project.number, 42)
        self.assertEqual(project.budget, "2000 EUR")
