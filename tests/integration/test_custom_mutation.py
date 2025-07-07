from django.contrib.auth import get_user_model
from django.db.models import CharField
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.api.mutation import graphQlMutation
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class CustomMutationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class TestMaterial(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

                class Meta:
                    app_label = "general_manager"

        cls.TestMaterial = TestMaterial
        cls.general_manager_classes = [TestMaterial]

        @graphQlMutation(auth_required=True)
        def create_material(info, name: str) -> TestMaterial:
            return TestMaterial.create(name=name, creator_id=info.context.user.id)

        cls.create_material = create_material

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="tester", password="secret")
        self.client.force_login(self.user)
        self.mutation = """
        mutation($name: String!) {
            createMaterial(name: $name) {
                testMaterial {
                    name
                }
                success
                errors
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
