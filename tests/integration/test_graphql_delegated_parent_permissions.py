from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import User
from django.db import models
from django.test import override_settings

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import ManagerBasedPermission, OverrideManagerPermission
from general_manager.permission.permission_data_manager import PermissionDataManager
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
        """Define parent permissions and children using primary and alternate FK keys."""
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
        """Create a user and distinguish permitted from denied parent rows."""
        super().setUp()
        self.user = User.objects.create_user(username="delegated-user")
        self.client.force_login(self.user)
        self.parent = self.Parent.create(allowed=True, ignore_permission=True)
        self.denied_parent = self.Parent.create(allowed=False, ignore_permission=True)

    def test_graphql_create_uses_parent_create_permission(self):
        """Delegate generated create mutations to the selected parent permission."""
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
        """Accept either public relation spelling for a permitted parent."""
        for payload in ({"parent": self.parent}, {"parent_id": str(self.parent.id)}):
            with self.subTest(payload=payload):
                child = self.Child.create(creator_id=self.user.id, **payload)
                self.assertEqual(child.parent, self.parent)

    def test_graphql_create_rejects_denied_and_invalid_parents(self):
        """Reject denied or invalid parent inputs before writing child rows."""
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
        """Do not grant delegated access when no concrete parent exists."""
        for payload in ({}, {"parent_id": None}, {"parent": None}):
            with self.subTest(payload=payload), self.assertRaises(PermissionError):
                self.Child.create(creator_id=self.user.id, **payload)

    def test_create_does_not_use_parent_update_permission(self):
        """Evaluate the parent create gate rather than its update permission."""
        from unittest.mock import patch

        with patch.object(self.Parent.Permission, "__create__", ["isAdmin"]):
            with self.assertRaises(PermissionError):
                self.Child.create(creator_id=self.user.id, parent_id=self.parent.id)

    def test_graphql_update_checks_new_parent(self):
        """Deny a mutation that replaces an allowed parent with a denied one."""
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
        """Delegate update and delete to their corresponding parent operations."""
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
        """Keep the child row when its parent denies deletion."""
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

    def test_conflicting_relation_aliases_check_explicit_id_target(self):
        """Authorize the explicit ID winner independently of keyword order."""
        for operation in ("create", "update"):
            for id_first in (True, False):
                for allowed_id in (True, False):
                    with self.subTest(
                        operation=operation,
                        id_first=id_first,
                        allowed_id=allowed_id,
                    ):
                        id_target = self.parent if allowed_id else self.denied_parent
                        relation = self.denied_parent if allowed_id else self.parent
                        payload = (
                            {"parent_id": id_target.id, "parent": relation}
                            if id_first
                            else {"parent": relation, "parent_id": id_target.id}
                        )
                        if operation == "create":
                            mutate = self.Child.create
                        else:
                            child = self.Child.create(
                                parent=self.parent, ignore_permission=True
                            )
                            mutate = child.update
                        if allowed_id:
                            result = mutate(creator_id=self.user.id, **payload)
                            self.assertEqual(result.parent, self.parent)
                        else:
                            with self.assertRaises(PermissionError):
                                mutate(creator_id=self.user.id, **payload)

    def test_explicit_null_id_does_not_authorize_ignored_parent(self):
        """Do not use an ignored parent object to authorize an explicit null ID."""
        for operation in ("create", "update"):
            for id_first in (True, False):
                with self.subTest(operation=operation, id_first=id_first):
                    payload = (
                        {"parent_id": None, "parent": self.parent}
                        if id_first
                        else {"parent": self.parent, "parent_id": None}
                    )
                    child = self.Child.create(
                        parent=self.parent, ignore_permission=True
                    )
                    mutate = (
                        self.Child.create if operation == "create" else child.update
                    )
                    with self.assertRaises(PermissionError):
                        mutate(creator_id=self.user.id, **payload)
                    self.assertEqual(self.Child(child.id).parent, self.parent)

    def test_object_update_overrides_existing_id(self):
        """Treat snapshot IDs as old state, not explicit competing update inputs."""
        child = self.Child.create(parent=self.denied_parent, ignore_permission=True)
        child = child.update(parent=self.parent, creator_id=self.user.id)
        self.assertEqual(child.parent, self.parent)

    def test_create_only_requires_parent_create(self):
        """Keep parent read and update restrictions separate from child creation."""
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
        """Resolve submitted alternate keys before evaluating the actual parent permission."""
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
        """Allow a denied old parent to be replaced with a permitted new one."""
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
        """Preserve the old parent when replacement input cannot identify a valid target."""
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

    def test_manager_alternate_key_inputs_authorize_the_persisted_target(self):
        """Persist the authorized manager target key rather than its colliding primary ID."""
        self.parent.update(code="allowed-code", ignore_permission=True)
        self.denied_parent.update(code=str(self.parent.id), ignore_permission=True)
        for operation in ("create", "update"):
            for alias in ("parent", "parent_id"):
                with self.subTest(operation=operation, alias=alias):
                    payload = {alias: self.parent}
                    if operation == "create":
                        result = self.CodeChild.create(
                            creator_id=self.user.id, **payload
                        )
                        candidate = PermissionDataManager(payload, self.CodeChild)
                    else:
                        child = self.CodeChild.create(
                            parent_id="allowed-code", ignore_permission=True
                        )
                        candidate = PermissionDataManager.for_update(child, payload)
                        result = child.update(creator_id=self.user.id, **payload)
                    row = self.CodeChild.Interface._model.objects.get(pk=result.id)
                    self.assertEqual(row.parent_id, "allowed-code")
                    self.assertEqual(result.parent, self.parent)
                    self.assertEqual(candidate.parent_id, "allowed-code")
                    self.assertEqual(candidate.parent, self.parent)

    def test_manager_alternate_key_explicit_alias_wins_including_null(self):
        """Apply explicit alternate-key winners and deny ignored or null parent targets."""
        self.parent.update(code="allowed-code", ignore_permission=True)
        self.denied_parent.update(code=str(self.parent.id), ignore_permission=True)
        for operation in ("create", "update"):
            for id_first in (True, False):
                for winner in (self.parent, self.denied_parent, None):
                    with self.subTest(
                        operation=operation, id_first=id_first, winner=winner
                    ):
                        relation = (
                            self.denied_parent if winner is self.parent else self.parent
                        )
                        pairs = [("parent", relation), ("parent_id", winner)]
                        if id_first:
                            pairs.reverse()
                        payload = dict(pairs)
                        child = self.CodeChild.create(
                            parent_id="allowed-code", ignore_permission=True
                        )
                        mutate = (
                            self.CodeChild.create
                            if operation == "create"
                            else child.update
                        )
                        before = self.CodeChild.Interface._model.objects.count()
                        if winner is self.parent:
                            result = mutate(creator_id=self.user.id, **payload)
                            row = self.CodeChild.Interface._model.objects.get(
                                pk=result.id
                            )
                            self.assertEqual(row.parent_id, "allowed-code")
                            self.assertEqual(result.parent, self.parent)
                        else:
                            with self.assertRaises(PermissionError):
                                mutate(creator_id=self.user.id, **payload)
                            self.assertEqual(
                                self.CodeChild.Interface._model.objects.count(), before
                            )
                            self.assertEqual(
                                self.CodeChild(child.id).parent, self.parent
                            )

    def test_manager_with_null_alternate_key_cannot_authorize_a_relation(self):
        """Reject null-key managers instead of authorizing a colliding primary-ID target."""
        self.parent.update(code=None, ignore_permission=True)
        self.denied_parent.update(code=str(self.parent.id), ignore_permission=True)
        retained = self.Parent.create(
            allowed=True, code="retained", ignore_permission=True
        )
        for operation in ("create", "update"):
            for alias in ("parent", "parent_id"):
                with self.subTest(operation=operation, alias=alias):
                    child = self.CodeChild.create(
                        parent_id="retained", ignore_permission=True
                    )
                    mutate = (
                        self.CodeChild.create if operation == "create" else child.update
                    )
                    before = self.CodeChild.Interface._model.objects.count()
                    with self.assertRaises(PermissionError):
                        mutate(creator_id=self.user.id, **{alias: self.parent})
                    self.assertEqual(
                        self.CodeChild.Interface._model.objects.count(), before
                    )
                    self.assertEqual(self.CodeChild(child.id).parent, retained)

    def test_stale_or_changed_manager_keys_cannot_authorize_another_row(self):
        """Resolve submitted alternate keys freshly before create or replacement update."""
        self.Parent.create(allowed=True, code="retained", ignore_permission=True)
        for state in ("stale", "changed"):
            for operation in ("create", "update"):
                for alias in ("parent", "parent_id"):
                    with self.subTest(state=state, operation=operation, alias=alias):
                        suffix = f"{state}-{operation}-{alias}"
                        allowed = self.Parent.create(
                            allowed=True, code=f"a-{suffix}", ignore_permission=True
                        )
                        denied = self.Parent.create(
                            allowed=False, code=f"d-{suffix}", ignore_permission=True
                        )
                        child = (
                            self.CodeChild.create(
                                parent_id="retained", ignore_permission=True
                            )
                            if operation == "update"
                            else None
                        )
                        if state == "stale":
                            model = self.Parent.Interface._model
                            model.objects.filter(pk=allowed.id).update(
                                code=f"m-{suffix}"
                            )
                            model.objects.filter(pk=denied.id).update(code=allowed.code)
                        else:
                            allowed._interface._instance.code = denied.code
                        mutate = (
                            self.CodeChild.create if child is None else child.update
                        )
                        before = self.CodeChild.Interface._model.objects.count()
                        with self.assertRaises(PermissionError):
                            mutate(creator_id=self.user.id, **{alias: allowed})
                        self.assertEqual(
                            self.CodeChild.Interface._model.objects.count(), before
                        )
                        if child is not None:
                            row = self.CodeChild.Interface._model.objects.get(
                                pk=child.id
                            )
                            self.assertEqual(row.parent_id, "retained")
