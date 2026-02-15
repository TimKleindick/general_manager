from __future__ import annotations

from typing import ClassVar

from django.db import models

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLMutationRelationAliases(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

        class TestPlant(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)

                class Meta:
                    app_label = "general_manager"
                    use_soft_delete = True

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

        class TestTag(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)

                class Meta:
                    app_label = "general_manager"
                    use_soft_delete = True

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

        class TestAsset(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                _plant = models.ForeignKey(
                    "general_manager.TestPlant", on_delete=models.CASCADE
                )
                tags = models.ManyToManyField("general_manager.TestTag", blank=True)

                class Meta:
                    app_label = "general_manager"
                    use_soft_delete = True

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

        cls.TestPlant = TestPlant
        cls.TestTag = TestTag
        cls.TestAsset = TestAsset
        cls.general_manager_classes = [TestPlant, TestTag, TestAsset]

    def test_create_and_update_mutations_accept_relation_aliases(self) -> None:
        plant_a = self.TestPlant.create(ignore_permission=True, name="Plant A")
        plant_b = self.TestPlant.create(ignore_permission=True, name="Plant B")
        tag_a = self.TestTag.create(ignore_permission=True, name="Tag A")
        tag_b = self.TestTag.create(ignore_permission=True, name="Tag B")

        create_mutation = """
        mutation CreateAsset($name: String!, $Plant: ID!, $tagsList: [ID]) {
            createTestAsset(name: $name, Plant: $Plant, tagsList: $tagsList) {
                success
                TestAsset { id name }
            }
        }
        """
        create_response = self.query(
            create_mutation,
            variables={
                "name": "Asset 1",
                "Plant": str(plant_a.id),
                "tagsList": [str(tag_a.id)],
            },
        )
        self.assertResponseNoErrors(create_response)
        create_data = create_response.json()["data"]["createTestAsset"]
        self.assertTrue(create_data["success"])
        asset_id = int(create_data["TestAsset"]["id"])

        update_mutation = """
        mutation UpdateAsset($id: Int!, $name: String!, $Plant: ID!, $tagsList: [ID]) {
            updateTestAsset(id: $id, name: $name, Plant: $Plant, tagsList: $tagsList) {
                success
                TestAsset { id name }
            }
        }
        """
        update_response = self.query(
            update_mutation,
            variables={
                "id": asset_id,
                "name": "Asset 1 Updated",
                "Plant": str(plant_b.id),
                "tagsList": [str(tag_a.id), str(tag_b.id)],
            },
        )
        self.assertResponseNoErrors(update_response)
        update_data = update_response.json()["data"]["updateTestAsset"]
        self.assertTrue(update_data["success"])

        db_asset = self.TestAsset.Interface._model.objects.get(pk=asset_id)
        self.assertEqual(db_asset._plant_id, plant_b.id)
        self.assertSetEqual(
            set(db_asset.tags.values_list("id", flat=True)),
            {tag_a.id, tag_b.id},
        )
