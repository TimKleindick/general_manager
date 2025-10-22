from django.contrib.auth import get_user_model
from django.db.models import CharField, BooleanField
from django.utils.crypto import get_random_string
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.api.mutation import graphQlMutation
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from general_manager.permission.mutationPermission import MutationPermission
from general_manager.permission.managerBasedPermission import ManagerBasedPermission
from typing import ClassVar


class CustomMutationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Prepare class-level fixtures for integration tests.

        Defines a TestMaterial GeneralManager with a name field, registers it in the test's general_manager_classes, declares an IsAuthenticated mutation permission, and exposes a create_material GraphQL mutation on the test class that creates TestMaterial instances.
        """

        class TestMaterial(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

                class Meta:
                    app_label = "general_manager"

        cls.TestMaterial = TestMaterial
        cls.general_manager_classes = [TestMaterial]

        class IsAuthenticated(MutationPermission):
            __mutate__: ClassVar[list[str]] = ["isAuthenticated"]

        @graphQlMutation(IsAuthenticated)
        def create_material(info, name: str) -> TestMaterial:
            """
            Create a TestMaterial with the given name and assign the calling user as creator.

            Parameters:
                info: GraphQL resolver info object; used to obtain the context user id for creator assignment.
                name (str): Name for the new TestMaterial.

            Returns:
                TestMaterial: The newly created TestMaterial instance.
            """
            return TestMaterial.create(name=name, creator_id=info.context.user.id)

        cls.create_material = create_material

    def setUp(self):
        """
        Creates and logs in a test user, then defines the GraphQL mutation string for creating a material.
        """
        User = get_user_model()
        password = get_random_string(12)
        self.user = User.objects.create_user(username="tester", password=password)
        self.client.force_login(self.user)
        self.mutation = """
        mutation($name: String!) {
            createMaterial(name: $name) {
                testMaterial {
                    name
                }
                success
            }
        }
        """

    def test_create_material(self):
        variables = {"name": "My Material"}
        response = self.query(self.mutation, variables=variables)
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["createMaterial"]
        self.assertTrue(data["success"])
        self.assertEqual(data["testMaterial"]["name"], "My Material")
        self.assertEqual(len(self.TestMaterial.all()), 1)


class CustomProjectMutationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Prepare class-level test fixtures: define a TestProject GeneralManager subclass, register it for tests, create an IsAuthenticated mutation permission, and attach a GraphQL `create_project` mutation function to the test class as `cls.create_project`.

        The created attributes are:
        - `cls.TestProject`: the TestProject manager class with a `title` field.
        - `cls.general_manager_classes`: list containing the TestProject class for test registration.
        - `cls.create_project`: a GraphQL mutation that creates a TestProject with the provided title and sets the creator from the request context.
        """

        class TestProject(GeneralManager):
            class Interface(DatabaseInterface):
                title = CharField(max_length=100)

                class Meta:
                    app_label = "general_manager"

        cls.TestProject = TestProject
        cls.general_manager_classes = [TestProject]

        class IsAuthenticated(MutationPermission):
            __mutate__: ClassVar[list[str]] = ["isAuthenticated"]

        @graphQlMutation(IsAuthenticated)
        def create_project(info, title: str) -> TestProject:
            """
            Create a TestProject with the given title and set its creator to the current user from the GraphQL resolver info context.

            Parameters:
                info: GraphQL resolver info object whose context.user supplies the creator's user.
                title (str): Title for the new project.

            Returns:
                TestProject: The newly created TestProject instance.
            """
            return TestProject.create(title=title, creator_id=info.context.user.id)

        cls.create_project = create_project

    def setUp(self):
        """
        Creates and logs in a test user, then defines a GraphQL mutation string for creating a project.
        """
        User = get_user_model()
        password = get_random_string(12)
        self.user = User.objects.create_user(username="tester", password=password)
        self.client.force_login(self.user)
        self.mutation = """
        mutation($title: String!) {
            createProject(title: $title) {
                testProject {
                    title
                }
                success
            }
        }
        """

    def test_create_project(self):
        variables = {"title": "My Project"}
        response = self.query(self.mutation, variables=variables)
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["createProject"]
        self.assertTrue(data["success"])
        self.assertEqual(data["testProject"]["title"], "My Project")
        self.assertEqual(len(self.TestProject.all()), 1)


class CustomMutationWithoutLogin(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Set up a test ToDo GeneralManager type, register it for tests, and define GraphQL mutations to toggle its finished state.

        Defines an inner ToDo manager with `headline` and `finished` fields and public read/create/update/delete permissions, assigns it to `cls.ToDo` and `cls.general_manager_classes`, and registers two GraphQL mutations:
        - `mark_todo_as_finished`: sets a ToDo's `finished` field to True.
        - `reset_todo`: sets a ToDo's `finished` field to False and requires authentication via `ResetToDoPermission`.

        Both mutations derive `creator_id` from `info.context.user.id` when a user is present and return the updated ToDo.
        """

        class ToDo(GeneralManager):
            class Interface(DatabaseInterface):
                headline = CharField(max_length=100)
                finished = BooleanField(default=False)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

        cls.ToDo = ToDo
        cls.general_manager_classes = [ToDo]

        class ResetToDoPermission(MutationPermission):
            __mutate__: ClassVar[list[str]] = ["isAuthenticated"]

        @graphQlMutation
        def mark_todo_as_finished(info, id: int) -> ToDo:
            """
            Mark a ToDo item as finished and return the updated instance.

            Parameters:
                id (int): Identifier of the ToDo to mark as finished. The resolver's context user, if present, will be recorded as `creator_id`.

            Returns:
                ToDo: The updated ToDo instance with `finished` set to `True` and `creator_id` set to the context user's id when available.
            """
            todo = ToDo(id)
            creator_id = info.context.user.id if info.context.user else None
            return todo.update(finished=True, creator_id=creator_id)

        @graphQlMutation(permission=ResetToDoPermission)
        def reset_todo(info, id: int) -> ToDo:
            todo = ToDo(id)
            creator_id = info.context.user.id if info.context.user else None
            return todo.update(finished=False, creator_id=creator_id)

    def setUp(self):
        """
        Prepares the GraphQL mutation string for marking a ToDo item as finished in test cases.
        """
        self.mutation = """
        mutation($id: Int!) {
            markTodoAsFinished(id: $id) {
                toDo {
                    headline
                    finished
                }
                success
            }
        }
        """

    def test_mark_todo_as_finished(self):
        todo = self.ToDo.create(headline="Test ToDo", finished=False)
        variables = todo.identification
        response = self.query(self.mutation, variables=variables)
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["markTodoAsFinished"]
        self.assertTrue(data["success"])
        self.assertEqual(data["toDo"]["headline"], "Test ToDo")
        self.assertTrue(data["toDo"]["finished"])

    def test_reset_todo(self):
        """
        Tests that marking a ToDo as finished succeeds for any user, while resetting a ToDo requires authentication and fails for unauthenticated users.

        Creates a finished ToDo, verifies the public mutation to mark as finished is idempotent and successful, then attempts to reset the ToDo without authentication and asserts that permission is denied.
        """
        todo = self.ToDo.create(headline="Test ToDo", finished=True)
        variables = todo.identification
        response = self.query(self.mutation, variables=variables)
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["markTodoAsFinished"]
        self.assertTrue(data["success"])
        self.assertEqual(data["toDo"]["headline"], "Test ToDo")
        self.assertTrue(data["toDo"]["finished"])

        reset_mutation = """
        mutation($id: Int!) {
            resetTodo(id: $id) {
                toDo {
                    headline
                    finished
                }
                success
            }
        }
        """
        response = self.query(reset_mutation, variables=variables)
        self.assertResponseHasErrors(response)
        data = response.json()["errors"][0]
        self.assertIn("Permission denied", data["message"])
