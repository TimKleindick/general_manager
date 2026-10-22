from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import User
from django.db import models
from django.test import override_settings

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import ManagerBasedPermission, OverrideManagerPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


@override_settings(
    GENERAL_MANAGER={
        "DEFAULT_PERMISSIONS": {
            "CREATE": ["isAdmin"],
            "UPDATE": ["isAdmin"],
            "DELETE": ["isAdmin"],
        }
    }
)
class TestGraphQLDelegatedParentPermissions(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        class DelegationParent(GeneralManager):
            class Interface(DatabaseInterface):
                allowed = models.BooleanField(default=True)
                code = models.CharField(
                    max_length=30, unique=True, null=True, blank=True
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = [
                    "isAuthenticated&matches:allowed:True"
                ]
                __update__: ClassVar[list[str]] = [
                    "isAuthenticated&matches:allowed:True"
                ]
                __delete__: ClassVar[list[str]] = [
                    "isAuthenticated&matches:allowed:True"
                ]

        class DelegationChild(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50, default="Child")
                parent = models.ForeignKey(
                    "general_manager.DelegationParent", on_delete=models.CASCADE
                )

            class Permission(ManagerBasedPermission):
                __based_on__: ClassVar[str] = "parent"

        class DelegationCodeChild(GeneralManager):
            class Interface(DatabaseInterface):
                parent = models.ForeignKey(
                    "general_manager.DelegationParent",
                    to_field="code",
                    on_delete=models.CASCADE,
                )

            class Permission(ManagerBasedPermission):
                __based_on__: ClassVar[str] = "parent"

        cls.CodeChild = DelegationCodeChild
        cls.Parent = DelegationParent
        cls.Child = DelegationChild
        cls.general_manager_classes = [
            DelegationParent,
            DelegationChild,
            DelegationCodeChild,
        ]

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="delegated-user")
        self.client.force_login(self.user)
        self.parent = self.Parent.create(allowed=True, ignore_permission=True)
        self.denied_parent = self.Parent.create(allowed=False, ignore_permission=True)

    def test_graphql_create_uses_parent_create_permission(self):
        response = self.query(
            """mutation($parent: ID!) {
                createDelegationChild(parent: $parent) {
                    success DelegationChild { id }
                }
            }""",
            variables={"parent": str(self.parent.id)},
        )
        self.assertResponseNoErrors(response)
        result = response.json()["data"]["createDelegationChild"]
        self.assertTrue(result["success"])
        self.assertEqual(
            self.Child(result["DelegationChild"]["id"]).parent, self.parent
        )

    def test_direct_create_accepts_parent_object_and_normalized_id(self):
        for payload in ({"parent": self.parent}, {"parent_id": str(self.parent.id)}):
            with self.subTest(payload=payload):
                child = self.Child.create(creator_id=self.user.id, **payload)
                self.assertEqual(child.parent, self.parent)

    def test_graphql_create_rejects_denied_and_invalid_parents(self):
        for parent_id in (str(self.denied_parent.id), "999999", "invalid"):
            with self.subTest(parent_id=parent_id):
                response = self.query(
                    """mutation($parent: ID!) {
                        createDelegationChild(parent: $parent) { success }
                    }""",
                    variables={"parent": parent_id},
                )
                self.assertTrue(response.json().get("errors"))
                self.assertEqual(self.Child.Interface._model.objects.count(), 0)

    def test_missing_or_null_parent_retains_default_permission(self):
        for payload in ({}, {"parent_id": None}, {"parent": None}):
            with self.subTest(payload=payload), self.assertRaises(PermissionError):
                self.Child.create(creator_id=self.user.id, **payload)

    def test_create_does_not_use_parent_update_permission(self):
        from unittest.mock import patch

        with patch.object(self.Parent.Permission, "__create__", ["isAdmin"]):
            with self.assertRaises(PermissionError):
                self.Child.create(creator_id=self.user.id, parent_id=self.parent.id)

    def test_graphql_update_checks_new_parent(self):
        child = self.Child.create(parent=self.parent, ignore_permission=True)
        response = self.query(
            """mutation($id: ID!, $parent: ID!) {
                updateDelegationChild(id: $id, parent: $parent) { success }
            }""",
            variables={"id": str(child.id), "parent": str(self.denied_parent.id)},
        )
        self.assertEqual(
            response.json()["errors"][0]["extensions"]["code"], "PERMISSION_DENIED"
        )
        self.assertEqual(self.Child(child.id).parent, self.parent)

    def test_graphql_update_and_delete_keep_delegated_operations(self):
        from unittest.mock import patch

        child = self.Child.create(parent=self.parent, ignore_permission=True)
        with patch.object(self.Parent.Permission, "__create__", ["isAdmin"]):
            response = self.query(
                """mutation($id: ID!) {
                    updateDelegationChild(id: $id, name: "Updated") { success }
                }""",
                variables={"id": str(child.id)},
            )
            self.assertResponseNoErrors(response)
            self.assertEqual(self.Child(child.id).name, "Updated")
            response = self.query(
                """mutation($id: ID!) {
                    deleteDelegationChild(id: $id) { success }
                }""",
                variables={"id": str(child.id)},
            )
            self.assertResponseNoErrors(response)
            self.assertFalse(
                self.Child.Interface._model.objects.filter(pk=child.id).exists()
            )

    def test_graphql_delete_rejects_parent_denial(self):
        child = self.Child.create(parent=self.denied_parent, ignore_permission=True)
        response = self.query(
            """mutation($id: ID!) {
                deleteDelegationChild(id: $id) { success }
            }""",
            variables={"id": str(child.id)},
        )
        self.assertEqual(
            response.json()["errors"][0]["extensions"]["code"], "PERMISSION_DENIED"
        )
        self.assertTrue(
            self.Child.Interface._model.objects.filter(pk=child.id).exists()
        )

    def test_conflicting_relation_aliases_follow_persisted_order(self):
        for operation in ("create", "update"):
            for id_first in (True, False):
                for allowed_last in (True, False):
                    with self.subTest(
                        operation=operation,
                        id_first=id_first,
                        allowed_last=allowed_last,
                    ):
                        first = self.denied_parent if allowed_last else self.parent
                        last = self.parent if allowed_last else self.denied_parent
                        payload = (
                            {"parent_id": first.id, "parent": last}
                            if id_first
                            else {"parent": first, "parent_id": last.id}
                        )
                        if operation == "create":
                            mutate = self.Child.create
                        else:
                            child = self.Child.create(
                                parent=self.parent, ignore_permission=True
                            )
                            mutate = child.update
                        if allowed_last:
                            result = mutate(creator_id=self.user.id, **payload)
                            self.assertEqual(result.parent, self.parent)
                        else:
                            with self.assertRaises(PermissionError):
                                mutate(creator_id=self.user.id, **payload)

    def test_object_update_overrides_existing_id(self):
        child = self.Child.create(parent=self.denied_parent, ignore_permission=True)
        child = child.update(parent=self.parent, creator_id=self.user.id)
        self.assertEqual(child.parent, self.parent)

    def test_create_only_requires_parent_create(self):
        from unittest.mock import patch

        with (
            patch.object(self.Parent.Permission, "__read__", ["isAdmin"]),
            patch.object(self.Parent.Permission, "__update__", ["isAdmin"]),
        ):
            response = self.query(
                """mutation($parent: ID!) {
                    createDelegationChild(parent: $parent) { success }
                }""",
                variables={"parent": str(self.parent.id)},
            )
            self.assertResponseNoErrors(response)
            self.assertTrue(response.json()["data"]["createDelegationChild"]["success"])

    def test_normalized_alternate_key_checks_actual_parent(self):
        self.denied_parent.update(code=str(self.parent.id), ignore_permission=True)
        with self.assertRaises(PermissionError):
            self.CodeChild.create(
                parent_id=str(self.parent.id), creator_id=self.user.id
            )
        self.parent.update(code="allowed-code", ignore_permission=True)
        child = self.CodeChild.create(parent_id="allowed-code", creator_id=self.user.id)
        self.assertEqual(child.parent, self.parent)
        child = child.update(creator_id=self.user.id)
        self.assertEqual(child.parent, self.parent)

    def test_graphql_update_accepts_permitted_new_parent(self):
        child = self.Child.create(parent=self.denied_parent, ignore_permission=True)
        response = self.query(
            """mutation($id: ID!, $parent: ID!) {
                updateDelegationChild(id: $id, parent: $parent) { success }
            }""",
            variables={"id": str(child.id), "parent": str(self.parent.id)},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(self.Child(child.id).parent, self.parent)

    def test_graphql_update_rejects_invalid_parent_without_writing(self):
        child = self.Child.create(parent=self.parent, ignore_permission=True)
        for parent_id in ("999999", "invalid", None):
            with self.subTest(parent_id=parent_id):
                response = self.query(
                    """mutation($id: ID!, $parent: ID) {
                        updateDelegationChild(id: $id, parent: $parent) { success }
                    }""",
                    variables={"id": str(child.id), "parent": parent_id},
                )
                self.assertTrue(response.json().get("errors"))
                self.assertEqual(self.Child(child.id).parent, self.parent)
