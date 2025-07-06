# type: ignore
from django.contrib.auth import get_user_model
from django.db.models import CharField, IntegerField
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.measurement.measurementField import MeasurementField
from general_manager.utils.testing import (
    GeneralManagerTransactionTestCase,
)
from general_manager.permission.managerBasedPermission import ManagerBasedPermission


class DefaultCreateMutationTest(GeneralManagerTransactionTestCase):

    @classmethod
    def setUpClass(cls):
        """
        Defines a dynamic `TestProject` model with specified fields for use in integration tests.

        The model includes a required `name`, an optional `number`, and a `budget` field with a base unit of EUR. Registers the model for use in test cases.
        """

        class TestProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                number = IntegerField(null=True, blank=True)
                budget = MeasurementField(
                    base_unit="EUR",
                )

                class Meta:
                    app_label = "general_manager"

        cls.TestProject = TestProject
        cls.general_manager_classes = [TestProject]

    def setUp(self):
        """
        Sets up the test environment by creating and logging in a test user and defining the GraphQL mutation string for creating a TestProject instance.
        """
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
        """
        Tests successful creation of a TestProject instance via GraphQL mutation with all required and optional fields.

        Verifies that the mutation response indicates success, the returned data matches the input values, and the created database record has the correct field values and is attributed to the test user.
        """
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
        self.assertEqual(project.changed_by, self.user)

    def test_create_project_without_budget(self):
        """
        Test that creating a TestProject without a budget fails and returns errors in the GraphQL response.
        """
        variables = {
            "name": "Test Project",
            "number": 42,
            "budget": None,
        }

        response = self.query(self.create_mutation, variables=variables)
        self.assertResponseHasErrors(response)

    def test_create_project_without_name(self):
        """
        Tests that creating a TestProject without a name results in an error response from the GraphQL mutation.
        """
        variables = {
            "name": None,
            "number": 42,
            "budget": "2000 EUR",
        }

        response = self.query(self.create_mutation, variables=variables)
        self.assertResponseHasErrors(response)

    def test_create_project_without_number(self):
        """
        Test that a TestProject can be created without specifying the optional 'number' field.

        Verifies that omitting the 'number' field in the create mutation results in a successful creation, with 'number' set to None and other fields correctly populated in the response.
        """
        variables = {
            "name": "Test Project",
            "number": None,
            "budget": "2000 EUR",
        }

        response = self.query(self.create_mutation, variables=variables)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertTrue(data["createTestProject"]["success"])

        data = data["createTestProject"]["TestProject"]
        self.assertEqual(data["name"], "Test Project")
        self.assertIsNone(data["number"])
        self.assertEqual(data["budget"]["value"], 2000)
        self.assertEqual(data["budget"]["unit"], "EUR")

    def test_create_project_with_invalid_budget(self):
        variables = {
            "name": "Test Project",
            "number": 42,
            "budget": "invalid amount",
        }

        response = self.query(self.create_mutation, variables=variables)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertFalse(data["createTestProject"]["success"])
        self.assertIn("budget", data["createTestProject"]["errors"][0])


class DefaultCreateMutationTestWithoutLogin(GeneralManagerTransactionTestCase):

    @classmethod
    def setUpClass(cls):
        """
        Defines a dynamic `TestProject` model with specified fields for use in integration tests.

        The model includes a required `name`, an optional `number`, and a `budget` field with a base unit of EUR. Registers the model for use in test cases.
        """

        class TestProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                number = IntegerField(null=True, blank=True)
                budget = MeasurementField(
                    base_unit="EUR",
                )

                class Meta:
                    app_label = "general_manager"

        class TestProject2(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                number = IntegerField(null=True, blank=True)
                budget = MeasurementField(
                    base_unit="EUR",
                )

                class Meta:
                    app_label = "general_manager"

            class Permission(ManagerBasedPermission):
                __create__ = ["public"]

        cls.TestProject = TestProject
        cls.TestProject2 = TestProject2
        cls.general_manager_classes = [TestProject, TestProject2]

    def setUp(self):
        """
        Sets up the test environment by creating and logging in a test user and defining the GraphQL mutation string for creating a TestProject instance.
        """
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

        self.create_mutation2 = """
        mutation CreateProject($name: String!, $number: Int, $budget: String) {
            createTestProject2(name: $name, number: $number, budget: $budget) {
                TestProject2 {
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

    def test_create_project_without_loggin(self):
        """
        Tests successful creation of a TestProject instance via GraphQL mutation with all required and optional fields.

        Verifies that the mutation response indicates success, the returned data matches the input values, and the created database record has the correct field values and is attributed to the test user.
        """
        variables = {
            "name": "Test Project",
            "number": 42,
            "budget": "2000 EUR",
        }

        response = self.query(self.create_mutation, variables=variables)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertFalse(data["createTestProject"]["success"])
        self.assertIn(
            "Permission denied",
            data["createTestProject"]["errors"][0],
        )

    def test_create_project_without_loggin_and_public_permissions(self):
        """
        Tests successful creation of a TestProject instance via GraphQL mutation with all required and optional fields.

        Verifies that the mutation response indicates success, the returned data matches the input values, and the created database record has the correct field values and is attributed to the test user.
        """
        variables = {
            "name": "Test Project",
            "number": 42,
            "budget": "2000 EUR",
        }

        response = self.query(self.create_mutation2, variables=variables)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertTrue(data["createTestProject2"]["success"])

        data = data["createTestProject2"]["TestProject2"]
        self.assertEqual(data["name"], "Test Project")
        self.assertEqual(data["number"], 42)
        self.assertEqual(data["budget"]["value"], 2000)
        self.assertEqual(data["budget"]["unit"], "EUR")

        self.assertEqual(len(self.TestProject2.all()), 1)
        project = self.TestProject2.all().first()
        self.assertEqual(project.name, "Test Project")
        self.assertEqual(project.number, 42)
        self.assertEqual(project.budget, "2000 EUR")
        self.assertEqual(project.changed_by, None)


class DefaultUpdateMutationTest(GeneralManagerTransactionTestCase):

    @classmethod
    def setUpClass(cls):
        """
        Defines a dynamic `TestProject` model with specified fields for use in integration tests.

        The model includes a required `name`, an optional `number`, and a `budget` field with a base unit of EUR. Registers the model for use in test cases.
        """

        class TestProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                number = IntegerField(null=True, blank=True, editable=False)
                budget = MeasurementField(
                    base_unit="EUR",
                )

                class Meta:
                    app_label = "general_manager"

        cls.TestProject = TestProject
        cls.general_manager_classes = [TestProject]

    def setUp(self):
        """
        Sets up the test environment by creating and logging in a test user and defining the GraphQL mutation string for updating a TestProject instance.
        """
        User = get_user_model()
        self.user = User.objects.create_user(username="tester", password="geheim")
        self.client.force_login(self.user)

        self.project = self.TestProject.create(
            name="Initial Project",
            number=1,
            budget="1000 EUR",
            creator_id=self.user.id,
        )

        self.update_mutation = """
        mutation UpdateProject($id: Int!, $name: String, $budget: String) {
            updateTestProject(id: $id, name: $name, budget: $budget) {
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

    def test_update_project(self):
        """
        Tests successful update of a TestProject instance via GraphQL mutation with all fields.
        Verifies that the mutation response indicates success, the returned data matches the updated values, and the updated database record has the correct field values and is attributed to the test user.
        """
        variables = {
            "id": self.project.id,
            "name": "Updated Project",
            "budget": "2000 EUR",
        }

        response = self.query(self.update_mutation, variables=variables)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertTrue(data["updateTestProject"]["success"])

        data = data["updateTestProject"]["TestProject"]
        self.assertEqual(data["name"], "Updated Project")
        self.assertEqual(data["number"], 1)  # Number should not change
        self.assertEqual(data["budget"]["value"], 2000)
        self.assertEqual(data["budget"]["unit"], "EUR")

        updated_project = self.TestProject(self.project.id)
        self.assertEqual(updated_project.name, "Updated Project")
        self.assertEqual(updated_project.number, 1)
        self.assertEqual(updated_project.budget, "2000 EUR")
        self.assertEqual(updated_project.changed_by, self.user)

    def test_update_project_without_budget(self):
        """
        Tests that updating a TestProject instance without changing the budget field results in a successful update and the budget remains unchanged.
        """
        update_mutation = """
            mutation UpdateProject($id: Int!, $name: String) {
                updateTestProject(id: $id, name: $name) {
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

        variables = {
            "id": self.project.id,
            "name": "Updated Project Without Budget",
        }

        response = self.query(update_mutation, variables=variables)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertTrue(data["updateTestProject"]["success"])

        data = data["updateTestProject"]["TestProject"]
        self.assertEqual(data["name"], "Updated Project Without Budget")
        self.assertEqual(data["number"], 1)
        # Budget should remain unchanged
        self.assertEqual(data["budget"]["value"], 1000)
        self.assertEqual(data["budget"]["unit"], "EUR")
