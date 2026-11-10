from __future__ import annotations

from typing import ClassVar
from unittest.mock import patch

from django.db import models

from general_manager.api.graphql import GraphQL
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLMutationRelationAliases(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """Build related managers with scalar, nullable, and collection GraphQL inputs."""
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
                owner = models.ForeignKey(
                    "general_manager.TestPlant",
                    on_delete=models.SET_NULL,
                    related_name="+",
                    null=True,
                    blank=True,
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

    def test_generated_mutations_expose_only_canonical_relation_arguments(self) -> None:
        """Expose one public input per relation rather than competing ID aliases."""
        schema = GraphQL.get_schema()
        assert schema is not None
        relation_arguments = {
            "Plant",
            "PlantId",
            "owner",
            "ownerId",
            "tagsList",
            "tagsIdList",
        }
        for mutation_name in ("createTestAsset", "updateTestAsset"):
            with self.subTest(mutation=mutation_name):
                arguments = schema.graphql_schema.mutation_type.fields[
                    mutation_name
                ].args
                self.assertSetEqual(
                    set(arguments) & relation_arguments,
                    {"Plant", "owner", "tagsList"},
                )

    def test_create_and_update_mutations_accept_relation_aliases(self) -> None:
        """Translate canonical GraphQL inputs into ID-only manager writes."""
        plant_a = self.TestPlant.create(ignore_permission=True, name="Plant A")
        plant_b = self.TestPlant.create(ignore_permission=True, name="Plant B")
        tag_a = self.TestTag.create(ignore_permission=True, name="Tag A")
        tag_b = self.TestTag.create(ignore_permission=True, name="Tag B")

        create_mutation = """
        mutation CreateAsset($name: String!, $Plant: ID!, $owner: ID, $tagsList: [ID]) {
            createTestAsset(name: $name, Plant: $Plant, owner: $owner, tagsList: $tagsList) {
                success
                TestAsset { id name }
            }
        }
        """
        with patch.object(
            self.TestAsset, "create", wraps=self.TestAsset.create
        ) as create:
            create_response = self.query(
                create_mutation,
                variables={
                    "name": "Asset 1",
                    "Plant": str(plant_a.id),
                    "owner": str(plant_b.id),
                    "tagsList": [str(tag_a.id)],
                },
            )
        self.assertResponseNoErrors(create_response)
        create.assert_called_once()
        relation_keys = {
            "_plant",
            "_plant_id",
            "owner",
            "owner_id",
            "tags_list",
            "tags_id_list",
        }
        self.assertEqual(
            {
                key: value
                for key, value in create.call_args.kwargs.items()
                if key in relation_keys
            },
            {
                "_plant_id": str(plant_a.id),
                "owner_id": str(plant_b.id),
                "tags_id_list": [str(tag_a.id)],
            },
        )
        create_data = create_response.json()["data"]["createTestAsset"]
        self.assertTrue(create_data["success"])
        asset_id = int(create_data["TestAsset"]["id"])
        created_asset = self.TestAsset.Interface._model.objects.get(pk=asset_id)
        self.assertEqual(created_asset._plant_id, plant_a.id)
        self.assertEqual(created_asset.owner_id, plant_b.id)

        update_mutation = """
        mutation UpdateAsset($id: ID!, $name: String!, $Plant: ID!, $owner: ID, $tagsList: [ID]) {
            updateTestAsset(id: $id, name: $name, Plant: $Plant, owner: $owner, tagsList: $tagsList) {
                success
                TestAsset { id name }
            }
        }
        """
        with patch.object(
            self.TestAsset, "update", autospec=True, side_effect=self.TestAsset.update
        ) as update:
            update_response = self.query(
                update_mutation,
                variables={
                    "id": asset_id,
                    "name": "Asset 1 Updated",
                    "Plant": str(plant_b.id),
                    "owner": str(plant_a.id),
                    "tagsList": [str(tag_a.id), str(tag_b.id)],
                },
            )
        self.assertResponseNoErrors(update_response)
        update.assert_called_once()
        self.assertEqual(
            {
                key: value
                for key, value in update.call_args.kwargs.items()
                if key in relation_keys
            },
            {
                "_plant_id": str(plant_b.id),
                "owner_id": str(plant_a.id),
                "tags_id_list": [str(tag_a.id), str(tag_b.id)],
            },
        )
        update_data = update_response.json()["data"]["updateTestAsset"]
        self.assertTrue(update_data["success"])

        db_asset = self.TestAsset.Interface._model.objects.get(pk=asset_id)
        self.assertEqual(db_asset._plant_id, plant_b.id)
        self.assertEqual(db_asset.owner_id, plant_a.id)
        self.assertSetEqual(
            set(db_asset.tags.values_list("id", flat=True)),
            {tag_a.id, tag_b.id},
        )

    def test_nullable_relation_update_preserves_omission_and_explicit_null(
        self,
    ) -> None:
        """Keep omitted relations unchanged while forwarding an explicit null ID."""
        plant = self.TestPlant.create(ignore_permission=True, name="Plant")
        owner = self.TestPlant.create(ignore_permission=True, name="Owner")
        asset = self.TestAsset.create(
            ignore_permission=True, name="Asset", _plant=plant, owner=owner
        )
        mutation = """
        mutation UpdateAsset($id: ID!, $name: String, $owner: ID) {
            updateTestAsset(id: $id, name: $name, owner: $owner) {
                success
                TestAsset { id name }
            }
        }
        """
        cases = (
            (
                {"name": "Renamed"},
                {"creator_id": None, "name": "Renamed"},
                owner.id,
            ),
            (
                {"name": "Cleared", "owner": None},
                {"creator_id": None, "name": "Cleared", "owner_id": None},
                None,
            ),
        )
        for variables, expected_kwargs, expected_owner_id in cases:
            with self.subTest(variables=variables):
                with patch.object(
                    self.TestAsset,
                    "update",
                    autospec=True,
                    side_effect=self.TestAsset.update,
                ) as update:
                    response = self.query(
                        mutation, variables={"id": str(asset.id), **variables}
                    )
                self.assertResponseNoErrors(response)
                self.assertTrue(response.json()["data"]["updateTestAsset"]["success"])
                update.assert_called_once()
                self.assertEqual(update.call_args.kwargs, expected_kwargs)
                db_asset = self.TestAsset.Interface._model.objects.get(pk=asset.id)
                self.assertEqual(db_asset.name, variables["name"])
                self.assertEqual(db_asset._plant_id, plant.id)
                self.assertEqual(db_asset.owner_id, expected_owner_id)
