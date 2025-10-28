from __future__ import annotations

from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.test import TestCase
from django.utils.crypto import get_random_string

from general_manager.permission.permission_checks import permission_functions


class PermissionFunctionsTests(TestCase):
    def setUp(self) -> None:
        User = get_user_model()
        self.user = User.objects.create_user(
            username="active-user",
            email="active@example.com",
            password=get_random_string(12),
        )
        self.inactive_user = User.objects.create_user(
            username="inactive-user",
            email="inactive@example.com",
            password=get_random_string(12),
            is_active=False,
        )
        self.admin_user = User.objects.create_user(
            username="admin-user",
            email="admin@example.com",
            password=get_random_string(12),
        )
        self.admin_user.is_staff = True
        self.admin_user.save()
        self.anonymous = AnonymousUser()

    def test_matches_requires_config(self) -> None:
        matches = permission_functions["matches"]
        instance = type("Dummy", (), {"status": "ok"})()

        self.assertFalse(matches["permission_method"](instance, self.user, []))
        self.assertIsNone(matches["permission_filter"](self.user, []))

        self.assertTrue(
            matches["permission_method"](instance, self.user, ["status", "ok"])
        )
        self.assertEqual(
            matches["permission_filter"](self.user, ["status", "ok"]),
            {"filter": {"status": "ok"}},
        )

    def test_public_permission(self) -> None:
        check = permission_functions["public"]

        self.assertTrue(check["permission_method"](None, self.anonymous, []))
        self.assertIsNone(check["permission_filter"](self.anonymous, []))

    def test_is_admin_permission(self) -> None:
        check = permission_functions["isAdmin"]

        self.assertFalse(check["permission_method"](None, self.user, []))
        self.assertTrue(check["permission_method"](None, self.admin_user, []))
        self.assertIsNone(check["permission_filter"](self.admin_user, []))

    def test_is_authenticated_permission(self) -> None:
        check = permission_functions["isAuthenticated"]

        self.assertTrue(check["permission_method"](None, self.user, []))
        self.assertFalse(check["permission_method"](None, self.anonymous, []))
        self.assertIsNone(check["permission_filter"](self.user, []))

    def test_is_active_permission(self) -> None:
        check = permission_functions["isActive"]

        self.assertTrue(check["permission_method"](None, self.user, []))
        self.assertFalse(check["permission_method"](None, self.inactive_user, []))
        self.assertIsNone(check["permission_filter"](self.user, []))

    def test_has_permission(self) -> None:
        check = permission_functions["hasPermission"]

        # Assign a concrete permission to the user
        perm: Permission | None = Permission.objects.filter(codename="add_user").first()
        if perm is None:
            self.fail("Expected auth.add_user permission to exist in test database.")
        assert perm is not None
        self.user.user_permissions.add(perm)
        self.user.refresh_from_db()
        perm_name = f"{perm.content_type.app_label}.{perm.codename}"

        self.assertTrue(
            check["permission_method"](None, self.user, [perm_name]),
        )
        self.assertFalse(
            check["permission_method"](None, self.user, ["unknown.missing_permission"]),
        )
        self.assertIsNone(check["permission_filter"](self.user, []))

    def test_in_group(self) -> None:
        check = permission_functions["inGroup"]

        group = Group.objects.create(name="testers")
        group.user_set.add(self.user)

        self.assertTrue(check["permission_method"](None, self.user, ["testers"]))
        self.assertFalse(
            check["permission_method"](None, self.user, ["other-group"]),
        )
        self.assertIsNone(check["permission_filter"](self.user, []))

    def test_related_user_field_permission(self) -> None:
        check = permission_functions["relatedUserField"]
        instance = MagicMock()
        instance.owner = self.user
        instance.creator = self.user

        self.assertTrue(check["permission_method"](instance, self.user, ["owner"]))
        self.assertFalse(
            check["permission_method"](instance, self.inactive_user, ["owner"])
        )
        self.assertIsNone(check["permission_filter"](self.user, []))
        self.assertEqual(
            check["permission_filter"](self.user, ["owner"]),
            {"filter": {"owner_id": self.user.id}},
        )
        self.assertEqual(
            check["permission_filter"](self.inactive_user, ["owner"]),
            {"filter": {"owner_id": self.inactive_user.id}},
        )

    def test_is_self_permission(self) -> None:
        check = permission_functions["isSelf"]
        instance = MagicMock()
        instance.creator = self.user

        self.assertTrue(check["permission_method"](instance, self.user, []))
        self.assertFalse(check["permission_method"](instance, self.admin_user, []))
        self.assertEqual(
            check["permission_filter"](self.user, []),
            {"filter": {"creator_id": self.user.id}},
        )

    def test_many_to_many_contains_user_permission(self) -> None:
        check = permission_functions["manyToManyContainsUser"]
        related_manager = MagicMock()
        filter_result = MagicMock()
        filter_result.exists.return_value = True
        related_manager.filter.return_value = filter_result
        instance = MagicMock()
        instance.members = related_manager

        self.assertTrue(check["permission_method"](instance, self.user, ["members"]))
        related_manager.filter.assert_called_once_with(pk=self.user.pk)

        filter_result.exists.return_value = False
        self.assertFalse(check["permission_method"](instance, self.user, ["members"]))
        self.assertIsNone(check["permission_filter"](self.user, []))
        self.assertEqual(
            check["permission_filter"](self.user, ["members"]),
            {"filter": {"members__id": self.user.id}},
        )
