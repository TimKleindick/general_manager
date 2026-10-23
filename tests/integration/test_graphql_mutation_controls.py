"""Generated writes cannot use payload fields to impersonate a trusted actor."""

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

import graphene
from django.contrib.auth import get_user_model
from django.db import models
from graphql import GraphQLError

from general_manager.api.graphql_mutations import (
    create_write_fields,
    generate_create_mutation_class,
    generate_update_mutation_class,
)
from general_manager.interface import DatabaseInterface
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class GraphQLMutationControlsTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class ProtectedControlRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)

            class Permission(ManagerBasedPermission):
                __create__: ClassVar[list[str]] = ["isAdmin"]
                __update__: ClassVar[list[str]] = ["isAdmin"]

        cls.Record = ProtectedControlRecord
        cls.general_manager_classes = [ProtectedControlRecord]

    def setUp(self) -> None:
        super().setUp()
        self.user = get_user_model().objects.create_user(username="control-client")
        self.admin = get_user_model().objects.create_user(
            username="control-admin", is_staff=True, is_superuser=True
        )
        self.info = SimpleNamespace(context=SimpleNamespace(user=self.user))
        self.record = self.Record.create(name="Original", creator_id=self.admin.pk)

    def test_payload_cannot_bypass_permissions_or_replace_actor(self) -> None:
        for generate, identification in (
            (generate_create_mutation_class, {}),
            (generate_update_mutation_class, {"id": self.record.id}),
        ):
            mutation = generate(self.Record, {"success": graphene.Boolean()})
            self.assertIsNotNone(mutation)
            for attack in (
                {"ignore_permission": True},
                {"creator_id": self.admin.pk},
                {"ignore_permission": True, "creator_id": self.admin.pk},
            ):
                with self.subTest(operation=generate.__name__, attack=attack):
                    with self.assertRaises(GraphQLError) as raised:
                        mutation.mutate(
                            None,
                            self.info,
                            **identification,
                            name="Unauthorized",
                            **attack,
                        )
                    self.assertEqual(
                        raised.exception.extensions["code"], "BAD_USER_INPUT"
                    )
                    self.assertEqual(self.Record.Interface._model.objects.count(), 1)
                    self.assertEqual(self.Record(id=self.record.id).name, "Original")

    def test_relation_alias_cannot_normalize_to_actor_control(self) -> None:
        attributes = self.Record.Interface.get_attribute_types()
        attributes["creator"] = {
            "type": self.Record,
            "is_required": False,
            "is_editable": True,
            "is_derived": False,
            "default": models.NOT_PROVIDED,
        }
        for generate, identification in (
            (generate_create_mutation_class, {}),
            (generate_update_mutation_class, {"id": self.record.id}),
        ):
            # Build a safe schema first, then simulate stale interface metadata.
            mutation = generate(self.Record, {"success": graphene.Boolean()})
            with self.subTest(operation=generate.__name__):
                with patch.object(
                    self.Record.Interface,
                    "get_attribute_types",
                    return_value=attributes,
                ):
                    with self.assertRaises(GraphQLError) as raised:
                        mutation.mutate(
                            None,
                            self.info,
                            **identification,
                            name="Unauthorized",
                            creator=str(self.admin.pk),
                        )
                self.assertEqual(raised.exception.extensions["code"], "BAD_USER_INPUT")
                self.assertEqual(self.Record.Interface._model.objects.count(), 1)
                self.assertEqual(self.Record(id=self.record.id).name, "Original")

        with patch.object(
            self.Record.Interface, "get_attribute_types", return_value=attributes
        ):
            with self.assertRaisesRegex(ValueError, "reserved"):
                create_write_fields(self.Record.Interface)

    def test_normal_writes_still_enforce_permissions_and_preserve_history(self) -> None:
        for generate, identification in (
            (generate_create_mutation_class, {}),
            (generate_update_mutation_class, {"id": self.record.id}),
        ):
            mutation = generate(self.Record, {"success": graphene.Boolean()})
            with self.subTest(operation=generate.__name__):
                with self.assertRaises(GraphQLError) as raised:
                    mutation.mutate(None, self.info, **identification, name="Denied")
                self.assertEqual(
                    raised.exception.extensions["code"], "PERMISSION_DENIED"
                )

                info = SimpleNamespace(context=SimpleNamespace(user=self.admin))
                result = mutation.mutate(
                    None,
                    info,
                    **identification,
                    name="Allowed",
                    history_comment="Reviewed",
                )
                self.assertTrue(result["success"])
                record = result["ProtectedControlRecord"]
                history = record.history.order_by("-history_date").first()
                self.assertEqual(history.history_user_id, self.admin.pk)
                self.assertEqual(history.history_change_reason, "Reviewed")
