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
        Defines a dynamic `TestProject` model with required `name`, optional `number`, and `budget` fields for use in integration tests.
        
        The `budget` field uses a measurement with EUR as the base unit. Registers the model for use in test cases.
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
        Test creation of a TestProject instance via GraphQL mutation with all fields provided.
        
        Verifies that the mutation succeeds, the returned data matches the input, and the created database record has correct values and is attributed to the logged-in user.
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
        Test creating a TestProject without the optional 'number' field.
        
        Ensures that omitting 'number' in the create mutation succeeds, with 'number' set to None and other fields correctly populated in the response.
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
        """
        Test that creating a project with an invalid budget value fails and returns an appropriate error for the budget field.
        """
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
        Defines two dynamic `TestProject` models with specified fields for integration testing.
        
        `TestProject` includes required and optional fields for name, number, and budget. `TestProject2` is similar but allows public creation via a custom permission class. Both models are registered for use in test cases.
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
        Prepares GraphQL mutation strings for creating TestProject and TestProject2 instances in tests.
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

    def test_create_project_without_login(self):
        """
        Tests that creating a TestProject instance via GraphQL mutation without logging in fails with a permission denied error.
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

    def test_create_project_without_login_and_public_permissions(self):
        """
        Test creation of a TestProject2 instance via GraphQL mutation without login when public create permission is enabled.
        
        Verifies that the mutation succeeds, the returned data matches the input, and the created database record has correct field values with `changed_by` set to None.
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
        Defines a dynamic `TestProject` model with required `name`, optional non-editable `number`, and `budget` fields for use in integration tests.
        
        Registers the model on the test class for use in update mutation test cases.
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
        Prepares the test case by creating and logging in a test user, initializing a TestProject instance, and defining the GraphQL mutation for updating a project.
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
        self.update_mutation_without_budget = """
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

    def test_update_project(self):
        """
        Test updating a TestProject instance via GraphQL mutation and verify that all updated fields are correctly persisted.
        
        Ensures the mutation succeeds, the response data reflects the new values, the non-editable `number` field remains unchanged, and the database record is updated with the correct user attribution.
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
        Verify that updating a TestProject instance without specifying the budget field succeeds and leaves the budget unchanged.
        
        Ensures that only the provided fields are updated, while omitted fields retain their previous values.
        """

        variables = {
            "id": self.project.id,
            "name": "Updated Project Without Budget",
        }

        response = self.query(self.update_mutation_without_budget, variables=variables)
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

        updated_project = self.TestProject(self.project.id)
        self.assertEqual(updated_project.name, "Updated Project Without Budget")
        self.assertEqual(updated_project.number, 1)
        self.assertEqual(updated_project.budget, "1000 EUR")
        self.assertEqual(updated_project.changed_by, self.user)


class DefaultDeleteMutationTest(GeneralManagerTransactionTestCase):

    @classmethod
    def setUpClass(cls):
        """
        Defines a dynamic `TestProject` model with required `name`, optional `number`, and `budget` fields for use in integration tests.
        
        The `budget` field uses a measurement with EUR as the base unit. Registers the model for use in test cases.
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
        Prepares the test case by creating and logging in a test user, initializing a TestProject instance, and defining the GraphQL mutation for deactivating the project.
        """
        User = get_user_model()
        self.user = User.objects.create_user(username="tester", password="geheim")
        self.client.force_login(self.user)

        self.project = self.TestProject.create(
            name="Project to Deactivate",
            number=1,
            budget="1000 EUR",
            creator_id=self.user.id,
        )

        self.deactivate_mutation = """
        mutation DeactivateProject($id: Int!) {
            deleteTestProject(id: $id) {
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

    def test_deactivate_project(self):
        """
        Test that a TestProject instance can be deactivated via GraphQL mutation and that the response and database reflect the correct deactivation state and attribution to the test user.
        """
        variables = {
            "id": self.project.id,
        }

        response = self.query(self.deactivate_mutation, variables=variables)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertTrue(data["deleteTestProject"]["success"])

        data = data["deleteTestProject"]["TestProject"]
        self.assertEqual(data["name"], "Project to Deactivate")
        self.assertEqual(data["number"], 1)
        self.assertEqual(data["budget"]["value"], 1000)
        self.assertEqual(data["budget"]["unit"], "EUR")

        deactivated_project = self.TestProject(self.project.id)
        self.assertFalse(deactivated_project.is_active)
        self.assertEqual(deactivated_project.changed_by, self.user)
