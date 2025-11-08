# type: ignore
from django.contrib.auth import get_user_model
from django.db.models import CharField, IntegerField
from django.utils.crypto import get_random_string
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface import DatabaseInterface
from general_manager.measurement.measurement_field import MeasurementField
from general_manager.utils.testing import (
    GeneralManagerTransactionTestCase,
)
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from typing import ClassVar
from django.core.exceptions import ObjectDoesNotExist


class DefaultCreateMutationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Create and register a dynamic TestProject model on the test class for use in tests.

        The model exposes an Interface with a required `name` (CharField, max_length=100), an optional `number` (IntegerField, null/blank allowed), and a `budget` MeasurementField with base unit "EUR". The model uses app label "general_manager" and enables soft delete. Assigns the model to `cls.TestProject` and sets `cls.general_manager_classes` to a list containing it.
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
                    use_soft_delete = True

        cls.TestProject = TestProject
        cls.general_manager_classes = [TestProject]

    def setUp(self):
        """
        Set up a test user, authenticate the test client, and prepare the GraphQL createTestProject mutation string.

        Creates a test user with a random password, logs the user into the test client, and assigns the GraphQL mutation used to create TestProject instances to self.create_mutation.
        """
        User = get_user_model()
        password = get_random_string(12)
        self.user = User.objects.create_user(username="tester", password=password)
        self.client.force_login(self.user)
        self.create_mutation = """
        mutation CreateProject($name: String!, $number: Int, $budget: MeasurementScalar) {
            createTestProject(name: $name, number: $number, budget: $budget) {
                TestProject {
                    name
                    number
                    budget {
                        value
                        unit
                    }
                }
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
        """
        Test that creating a project with an invalid budget value returns an error indicating the budget field is invalid.
        """
        variables = {
            "name": "Test Project",
            "number": 42,
            "budget": "invalid amount",
        }

        response = self.query(self.create_mutation, variables=variables)
        self.assertResponseHasErrors(response)
        response = response.json()
        data = response.get("errors", {})
        self.assertIn("budget", data[0]["message"])


class DefaultCreateMutationTestWithoutLogin(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Dynamically defines TestProject and TestProject2 models and registers them on the test class for use in integration tests.

        Both models expose an Interface with a required `name` (CharField, max_length=100), an optional `number` (IntegerField, null and blank allowed), and a `budget` (MeasurementField with base_unit "EUR"). TestProject2 additionally declares a Permission class that allows public creation. The created model classes are attached to the test class as `TestProject` and `TestProject2` and listed in `general_manager_classes`.
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
                __create__: ClassVar[list[str]] = ["public"]

        cls.TestProject = TestProject
        cls.TestProject2 = TestProject2
        cls.general_manager_classes = [TestProject, TestProject2]

    def setUp(self):
        """
        Prepares GraphQL mutation strings for creating TestProject and TestProject2 instances in test cases.
        """
        self.create_mutation = """
        mutation CreateProject($name: String!, $number: Int, $budget: MeasurementScalar) {
            createTestProject(name: $name, number: $number, budget: $budget) {
                TestProject {
                    name
                    number
                    budget {
                        value
                        unit
                    }
                }
                success
            }
        }
        """

        self.create_mutation2 = """
        mutation CreateProject($name: String!, $number: Int, $budget: MeasurementScalar) {
            createTestProject2(name: $name, number: $number, budget: $budget) {
                TestProject2 {
                    name
                    number
                    budget {
                        value
                        unit
                    }
                }
                success
            }
        }
        """

    def test_create_project_without_login(self):
        """
        Test that creating a TestProject without authentication fails due to permission restrictions.

        Asserts that the mutation response contains a 'Permission denied' error when no user is logged in.
        """
        variables = {
            "name": "Test Project",
            "number": 42,
            "budget": "2000 EUR",
        }

        response = self.query(self.create_mutation, variables=variables)
        self.assertResponseHasErrors(response)
        response = response.json()
        data = response.get("errors", [])
        self.assertIn("Permission denied", data[0]["message"])

    def test_create_project_without_login_and_public_permissions(self):
        """
        Test that a TestProject2 instance can be created without authentication when public create permissions are enabled.

        Verifies that the mutation succeeds, the created record matches the input data, and the `changed_by` field is set to None.
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
        Prepare test state for update-mutation tests.

        Creates and logs in a test user, creates an initial TestProject instance with name, number, and budget, and defines GraphQL mutation strings for updating a TestProject with and without the budget field.
        """
        User = get_user_model()
        password = get_random_string(12)
        self.user = User.objects.create_user(username="tester", password=password)
        self.client.force_login(self.user)

        self.project = self.TestProject.create(
            name="Initial Project",
            number=1,
            budget="1000 EUR",
            creator_id=self.user.id,
        )

        self.update_mutation = """
        mutation UpdateProject($id: Int!, $name: String, $budget: MeasurementScalar) {
            updateTestProject(id: $id, name: $name, budget: $budget) {
                TestProject {
                    name
                    number
                    budget {
                        value
                        unit
                    }
                }
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
        Create and register a dynamic TestProject model with name, number, and budget fields for use in tests.

        The model defines a required `name`, an optional `number`, and a `budget` MeasurementField with base unit "EUR"; it is assigned to `cls.TestProject` and added to `cls.general_manager_classes`.
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
        Set up test fixtures: create and log in a user, create a TestProject instance, and prepare the delete GraphQL mutation.

        Sets these attributes on the test instance:
            - self.user: the created and authenticated user.
            - self.project: the created TestProject instance to be deleted in tests.
            - self.delete_mutation: GraphQL mutation string for deleting a TestProject.
        """
        User = get_user_model()
        password = get_random_string(12)
        self.user = User.objects.create_user(username="tester", password=password)
        self.client.force_login(self.user)

        self.project = self.TestProject.create(
            name="Project to Deactivate",
            number=1,
            budget="1000 EUR",
            creator_id=self.user.id,
        )

        self.delete_mutation = """
        mutation DeleteProject($id: Int!) {
            deleteTestProject(id: $id) {
                TestProject {
                    name
                    number
                    budget {
                        value
                        unit
                    }
                }
                success
            }
        }
        """

    def test_delete_project(self):
        """
        Verifies that deleting a TestProject via the GraphQL delete mutation reports success and the project is no longer retrievable.

        Asserts the GraphQL response contains no errors, that the mutation's `success` field is true, and that attempting to access the deleted project raises `ObjectDoesNotExist`.
        """
        variables = {
            "id": self.project.id,
        }

        response = self.query(self.delete_mutation, variables=variables)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertTrue(data["deleteTestProject"]["success"])

        with self.assertRaises(ObjectDoesNotExist):
            self.TestProject(self.project.id)
