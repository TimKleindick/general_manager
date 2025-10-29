from __future__ import annotations

from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser, AnonymousUser, Group, Permission
from django.test import TestCase
from django.utils.crypto import get_random_string

from general_manager.permission import permission_functions, register_permission


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

    def test_register_permission_decorator(self) -> None:
        dummy_instance = MagicMock()

        def custom_filter(user: AnonymousUser | AbstractUser, config: list[str]):
            return {
                "filter": {
                    "custom_flag": config[0] if config else getattr(user, "id", None)
                }
            }

        try:

            @register_permission("customPermission", permission_filter=custom_filter)
            def _custom_permission(
                _instance,
                _user,
                _config: list[str],
            ) -> bool:
                return True

            self.assertIn("customPermission", permission_functions)
            permission_entry = permission_functions["customPermission"]
            self.assertTrue(
                permission_entry["permission_method"](dummy_instance, self.user, [])
            )
            self.assertEqual(
                permission_entry["permission_filter"](self.user, []),
                {"filter": {"custom_flag": self.user.id}},
            )

            with self.assertRaises(ValueError):

                @register_permission("customPermission")
                def _duplicate_permission(instance, user, config):
                    return False

        finally:
            permission_functions.pop("customPermission", None)

    def test_register_permission_without_filter(self) -> None:
        """Test registering a permission without a custom filter."""
        dummy_instance = MagicMock()

        try:

            @register_permission("simplePermission")
            def _simple_permission(_instance, user, _config: list[str]) -> bool:
                return user.is_authenticated

            self.assertIn("simplePermission", permission_functions)
            permission_entry = permission_functions["simplePermission"]
            self.assertTrue(
                permission_entry["permission_method"](dummy_instance, self.user, [])
            )
            self.assertFalse(
                permission_entry["permission_method"](
                    dummy_instance, self.anonymous, []
                )
            )
            # Default filter should return None
            self.assertIsNone(permission_entry["permission_filter"](self.user, []))
        finally:
            permission_functions.pop("simplePermission", None)

    def test_public_permission_with_inactive_user(self) -> None:
        """Test public permission allows inactive users."""
        check = permission_functions["public"]
        self.assertTrue(check["permission_method"](None, self.inactive_user, []))

    def test_matches_permission_with_no_config(self) -> None:
        """Test matches permission requires config."""
        matches = permission_functions["matches"]
        instance = MagicMock()
        instance.status = "active"

        self.assertFalse(matches["permission_method"](instance, self.user, []))
        self.assertFalse(matches["permission_method"](instance, self.user, ["status"]))

    def test_matches_permission_with_multiple_attributes(self) -> None:
        """Test matches permission with various attribute types."""
        matches = permission_functions["matches"]
        instance = MagicMock()
        instance.count = 42
        instance.flag = True
        instance.name = "test"

        self.assertTrue(
            matches["permission_method"](instance, self.user, ["count", "42"])
        )
        self.assertFalse(
            matches["permission_method"](instance, self.user, ["count", "43"])
        )
        self.assertTrue(
            matches["permission_method"](instance, self.user, ["name", "test"])
        )

    def test_is_admin_permission_with_anonymous(self) -> None:
        """Test isAdmin permission with anonymous user."""
        check = permission_functions["isAdmin"]
        self.assertFalse(check["permission_method"](None, self.anonymous, []))

    def test_is_authenticated_permission_with_inactive_user(self) -> None:
        """Test isAuthenticated permission with inactive but authenticated user."""
        check = permission_functions["isAuthenticated"]
        # Inactive users are still authenticated
        self.assertTrue(check["permission_method"](None, self.inactive_user, []))

    def test_is_active_permission_with_anonymous(self) -> None:
        """Test isActive permission with anonymous user."""
        check = permission_functions["isActive"]
        self.assertFalse(check["permission_method"](None, self.anonymous, []))

    def test_has_permission_with_no_config(self) -> None:
        """Test hasPermission requires config."""
        check = permission_functions["hasPermission"]
        self.assertFalse(check["permission_method"](None, self.user, []))

    def test_has_permission_with_anonymous_user(self) -> None:
        """Test hasPermission with anonymous user."""
        check = permission_functions["hasPermission"]
        self.assertFalse(
            check["permission_method"](None, self.anonymous, ["auth.add_user"])
        )

    def test_in_group_with_no_config(self) -> None:
        """Test inGroup requires config."""
        check = permission_functions["inGroup"]
        self.assertFalse(check["permission_method"](None, self.user, []))

    def test_in_group_with_anonymous_user(self) -> None:
        """Test inGroup with anonymous user."""
        check = permission_functions["inGroup"]
        self.assertFalse(check["permission_method"](None, self.anonymous, ["testers"]))

    def test_in_group_with_multiple_groups(self) -> None:
        """Test inGroup with user in multiple groups."""
        check = permission_functions["inGroup"]

        group1 = Group.objects.create(name="group1")
        group2 = Group.objects.create(name="group2")
        group1.user_set.add(self.user)
        group2.user_set.add(self.user)

        self.assertTrue(check["permission_method"](None, self.user, ["group1"]))
        self.assertTrue(check["permission_method"](None, self.user, ["group2"]))
        self.assertFalse(check["permission_method"](None, self.user, ["group3"]))

    def test_related_user_field_permission_with_no_config(self) -> None:
        """Test relatedUserField requires config."""
        check = permission_functions["relatedUserField"]
        instance = MagicMock()

        self.assertFalse(check["permission_method"](instance, self.user, []))
        self.assertIsNone(check["permission_filter"](self.user, []))

    def test_related_user_field_permission_with_missing_field(self) -> None:
        """Test relatedUserField with missing field."""
        check = permission_functions["relatedUserField"]
        instance = MagicMock()
        instance.owner = None

        self.assertFalse(check["permission_method"](instance, self.user, ["missing"]))

    def test_related_user_field_permission_with_anonymous(self) -> None:
        """Test relatedUserField with anonymous user."""
        check = permission_functions["relatedUserField"]
        instance = MagicMock()
        instance.owner = self.user

        self.assertFalse(
            check["permission_method"](instance, self.anonymous, ["owner"])
        )
        # Anonymous user should return None for filter
        self.assertIsNone(check["permission_filter"](self.anonymous, ["owner"]))

    def test_is_self_permission_with_different_user(self) -> None:
        """Test isSelf permission with different users."""
        check = permission_functions["isSelf"]
        instance = MagicMock()
        instance.creator = self.user

        self.assertTrue(check["permission_method"](instance, self.user, []))
        self.assertFalse(check["permission_method"](instance, self.admin_user, []))
        self.assertFalse(check["permission_method"](instance, self.anonymous, []))

    def test_is_self_permission_filter_with_anonymous(self) -> None:
        """Test isSelf permission filter with anonymous user."""
        check = permission_functions["isSelf"]
        result = check["permission_filter"](self.anonymous, [])
        self.assertEqual(result, {"filter": {"creator_id": None}})

    def test_many_to_many_contains_user_with_no_config(self) -> None:
        """Test manyToManyContainsUser requires config."""
        check = permission_functions["manyToManyContainsUser"]
        instance = MagicMock()

        self.assertFalse(check["permission_method"](instance, self.user, []))
        self.assertIsNone(check["permission_filter"](self.user, []))

    def test_many_to_many_contains_user_with_missing_field(self) -> None:
        """Test manyToManyContainsUser with missing field."""
        check = permission_functions["manyToManyContainsUser"]
        instance = MagicMock()
        instance.members = None

        self.assertFalse(check["permission_method"](instance, self.user, ["members"]))

    def test_many_to_many_contains_user_without_filter_method(self) -> None:
        """Test manyToManyContainsUser with object lacking filter method."""
        check = permission_functions["manyToManyContainsUser"]
        instance = MagicMock()
        instance.members = "not a manager"

        self.assertFalse(check["permission_method"](instance, self.user, ["members"]))

    def test_many_to_many_contains_user_with_anonymous(self) -> None:
        """Test manyToManyContainsUser with anonymous user."""
        check = permission_functions["manyToManyContainsUser"]
        related_manager = MagicMock()
        instance = MagicMock()
        instance.members = related_manager

        self.assertFalse(
            check["permission_method"](instance, self.anonymous, ["members"])
        )
        # Anonymous user should return None for filter
        self.assertIsNone(check["permission_filter"](self.anonymous, ["members"]))

    def test_many_to_many_contains_user_filter_result_no_exists(self) -> None:
        """Test manyToManyContainsUser when filter result has no exists method."""
        check = permission_functions["manyToManyContainsUser"]
        related_manager = MagicMock()
        filter_result = MagicMock()
        del filter_result.exists  # Remove exists method
        related_manager.filter.return_value = filter_result
        instance = MagicMock()
        instance.members = related_manager

        self.assertFalse(check["permission_method"](instance, self.user, ["members"]))

    def test_permission_functions_registry_completeness(self) -> None:
        """Test that all expected permission functions are registered."""
        expected_permissions = [
            "public",
            "matches",
            "isAdmin",
            "isSelf",
            "isAuthenticated",
            "isActive",
            "hasPermission",
            "inGroup",
            "relatedUserField",
            "manyToManyContainsUser",
        ]

        for perm_name in expected_permissions:
            self.assertIn(perm_name, permission_functions)
            self.assertIn("permission_method", permission_functions[perm_name])
            self.assertIn("permission_filter", permission_functions[perm_name])

    def test_register_permission_returns_function(self) -> None:
        """Test that register_permission decorator returns the original function."""
        try:

            @register_permission("testReturnFunc")
            def decorated_func(instance, user, config):
                return True

            # Decorator should return the function itself
            self.assertEqual(decorated_func.__name__, "decorated_func")
            self.assertTrue(callable(decorated_func))
        finally:
            permission_functions.pop("testReturnFunc", None)

    def test_matches_permission_filter_format(self) -> None:
        """Test matches permission filter returns correct format."""
        matches = permission_functions["matches"]

        result = matches["permission_filter"](self.user, ["field", "value"])
        self.assertIsInstance(result, dict)
        self.assertIn("filter", result)
        self.assertEqual(result["filter"], {"field": "value"})

    def test_is_self_permission_filter_format(self) -> None:
        """Test isSelf permission filter returns correct format."""
        check = permission_functions["isSelf"]

        result = check["permission_filter"](self.user, [])
        self.assertIsInstance(result, dict)
        self.assertIn("filter", result)
        self.assertEqual(result["filter"], {"creator_id": self.user.id})

    def test_related_user_field_permission_filter_format(self) -> None:
        """Test relatedUserField permission filter returns correct format."""
        check = permission_functions["relatedUserField"]

        result = check["permission_filter"](self.user, ["owner"])
        self.assertIsInstance(result, dict)
        self.assertIn("filter", result)
        self.assertEqual(result["filter"], {"owner_id": self.user.id})

    def test_many_to_many_contains_user_filter_format(self) -> None:
        """Test manyToManyContainsUser permission filter returns correct format."""
        check = permission_functions["manyToManyContainsUser"]

        result = check["permission_filter"](self.user, ["members"])
        self.assertIsInstance(result, dict)
        self.assertIn("filter", result)
        self.assertEqual(result["filter"], {"members__id": self.user.id})

    def test_permission_method_signature(self) -> None:
        """Test that all permission methods have consistent signature."""
        for _perm_name, perm_dict in permission_functions.items():
            method = perm_dict["permission_method"]
            # Should be callable with (instance, user, config)
            self.assertTrue(callable(method))

    def test_permission_filter_signature(self) -> None:
        """Test that all permission filters have consistent signature."""
        for _perm_name, perm_dict in permission_functions.items():
            filter_func = perm_dict["permission_filter"]
            # Should be callable with (user, config)
            self.assertTrue(callable(filter_func))

    def test_has_permission_with_superuser(self) -> None:
        """Test hasPermission with superuser who has all permissions."""
        check = permission_functions["hasPermission"]

        superuser = get_user_model().objects.create_superuser(
            username="superuser",
            email="super@example.com",
            password=get_random_string(12),
        )

        # Superusers should have all permissions
        self.assertTrue(check["permission_method"](None, superuser, ["any.permission"]))

    def test_is_admin_with_staff_and_superuser(self) -> None:
        """Test isAdmin distinguishes between staff and superuser."""
        check = permission_functions["isAdmin"]

        superuser = get_user_model().objects.create_superuser(
            username="superuser",
            email="super@example.com",
            password=get_random_string(12),
        )

        # Both staff and superuser should pass
        self.assertTrue(check["permission_method"](None, self.admin_user, []))
        self.assertTrue(check["permission_method"](None, superuser, []))

    def test_permission_config_with_extra_parameters(self) -> None:
        """Test permission functions handle extra config parameters."""
        matches = permission_functions["matches"]
        instance = MagicMock()
        instance.status = "active"

        # Extra config parameters should be ignored
        self.assertTrue(
            matches["permission_method"](
                instance, self.user, ["status", "active", "extra", "params"]
            )
        )
