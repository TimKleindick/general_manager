from __future__ import annotations
from typing import TYPE_CHECKING, Literal, cast, ClassVar
from django.test import TestCase
from django.contrib.auth.models import AnonymousUser  # as Dummy-User
from general_manager.permission.base_permission import BasePermission
from general_manager.permission.permission_checks import (
    permission_functions,
    PermissionDict,
)
from general_manager.permission.utils import PermissionNotFoundError
from unittest.mock import Mock, patch
from general_manager.permission.permission_data_manager import (
    PermissionDataManager,
)
from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

# Dummy helpers for permission_functions


def dummy_permission_filter(
    user: AnonymousUser | AbstractUser, config: list[str]
) -> dict[Literal["filter", "exclude"], dict[str, str]] | None:
    """
    Dummy implementation of the filter function:
    - Returns a filter mapping when the first parameter is "allow"
    - Returns None otherwise
    """
    if config and config[0] == "allow":
        return {"filter": {"dummy": "allowed"}, "exclude": {}}
    return None


def dummy_permission_method(instance, user, config):
    """
    Dummy implementation of the permission method:
    - Returns True when the first parameter is "pass"
    - Returns False otherwise
    """
    if config and config[0] == "pass":
        return True
    return False


# Dummy implementation of BasePermission
class DummyPermission(BasePermission):
    create_permissions: ClassVar[dict[str, str]] = {}
    update_permissions: ClassVar[dict[str, str]] = {}
    delete_permissions: ClassVar[dict[str, str]] = {}
    read_permissions: ClassVar[dict[str, str]] = {}

    def check_permission(
        self,
        action: Literal["create", "read", "update", "delete"],
        attribute: str,
    ) -> bool:
        permission_map: dict[str, dict[str, str]] = {
            "create": self.__class__.create_permissions,
            "read": self.__class__.read_permissions,
            "update": self.__class__.update_permissions,
            "delete": self.__class__.delete_permissions,
        }
        configured_permission = permission_map.get(action, {}).get(attribute)
        if configured_permission is None:
            return True
        return self.validate_permission_string(configured_permission)

    def get_permission_filter(
        self,
    ) -> list[dict[Literal["filter", "exclude"], dict[str, str]]]:
        filters: list[dict[Literal["filter", "exclude"], dict[str, str]]] = []
        for permission in self.__class__.read_permissions.values():
            if isinstance(permission, str):
                filters.append(self._get_permission_filter(permission))
            else:
                for permission_string in permission:
                    filters.append(self._get_permission_filter(permission_string))
        return filters


class BasePermissionTests(TestCase):
    def setUp(self):
        # Backup the original permission_functions and override them for tests
        self.original_permission_functions = permission_functions.copy()
        permission_functions.clear()
        permission_functions["dummy"] = cast(
            PermissionDict,
            {
                "permission_filter": dummy_permission_filter,
                "permission_method": dummy_permission_method,
            },
        )
        # Dummy instances for `instance` and `request_user`
        self.dummy_instance = Mock(spec=PermissionDataManager)
        self.dummy_user = AnonymousUser()
        self.user = self.dummy_user
        self.permission_obj = DummyPermission(self.dummy_instance, self.dummy_user)
        self.original_class_permissions = {
            "create": DummyPermission.create_permissions.copy(),
            "read": DummyPermission.read_permissions.copy(),
            "update": DummyPermission.update_permissions.copy(),
            "delete": DummyPermission.delete_permissions.copy(),
        }
        UserModel = get_user_model()
        self.superuser = UserModel.objects.create_superuser(
            username="superuser",
            email="super@example.com",
            password=get_random_string(12),
        )

    def tearDown(self):
        # Restore the original permission_functions
        permission_functions.clear()
        permission_functions.update(self.original_permission_functions)
        DummyPermission.create_permissions = self.original_class_permissions[
            "create"
        ].copy()
        DummyPermission.read_permissions = self.original_class_permissions[
            "read"
        ].copy()
        DummyPermission.update_permissions = self.original_class_permissions[
            "update"
        ].copy()
        DummyPermission.delete_permissions = self.original_class_permissions[
            "delete"
        ].copy()

    def test_get_permission_filter_valid(self):
        """
        Test _get_permission_filter with a valid permission string that yields a non-empty filter.
        """
        result = self.permission_obj._get_permission_filter("dummy:allow")
        expected = {"filter": {"dummy": "allowed"}, "exclude": {}}
        self.assertEqual(result, expected)

    def test_get_permission_filter_default(self):
        """
        Test _get_permission_filter when the dummy filter returns None to ensure the default value is produced.
        """
        result = self.permission_obj._get_permission_filter("dummy:deny")
        expected = {"filter": {}, "exclude": {}}
        self.assertEqual(result, expected)

    def test_get_permission_filter_invalid_permission(self):
        """
        Test _get_permission_filter with an invalid permission string; should raise PermissionNotFoundError.
        """
        with self.assertRaises(PermissionNotFoundError):
            self.permission_obj._get_permission_filter("nonexistent:whatever")

    def test_validate_permission_string_all_true(self):
        """Test validate_permission_string when all sub-permissions evaluate to True."""
        result = self.permission_obj.validate_permission_string("dummy:pass")
        self.assertTrue(result)
        result2 = self.permission_obj.validate_permission_string(
            "dummy:pass&dummy:pass"
        )
        self.assertTrue(result2)

    def test_validate_permission_string_one_false(self):
        """Test validate_permission_string when one sub-permission evaluates to False."""
        result = self.permission_obj.validate_permission_string("dummy:pass&dummy:fail")
        self.assertFalse(result)

    def test_validate_permission_string_invalid_permission(self):
        """Test validate_permission_string with an invalid permission string; should raise ValueError."""
        with self.assertRaises(ValueError):
            self.permission_obj.validate_permission_string("nonexistent:whatever")

    def test_check_permission(self):
        """Test the concrete check_permission implementation."""
        self.assertTrue(self.permission_obj.check_permission("create", "attribute"))

    def test_get_permission_filter_public(self):
        """Test the public get_permission_filter method of DummyPermission."""
        DummyPermission.read_permissions = {"field": "dummy:allow"}
        result = self.permission_obj.get_permission_filter()
        expected = [{"filter": {"dummy": "allowed"}, "exclude": {}}]
        self.assertEqual(result, expected)

    def test_superuser_short_circuits_checks(self):
        """Superusers should bypass validation errors and filters."""
        DummyPermission.read_permissions = {"field": "nonexistent:whatever"}
        DummyPermission.create_permissions = {"attribute": "nonexistent:whatever"}
        superuser_permission = DummyPermission(self.dummy_instance, self.superuser)

        self.assertTrue(
            superuser_permission.validate_permission_string("nonexistent:whatever")
        )
        # Should not raise PermissionNotFoundError despite invalid permission
        self.assertEqual(
            superuser_permission.get_permission_filter(),
            [{"filter": {}, "exclude": {}}],
        )
        self.assertTrue(superuser_permission.check_permission("create", "attribute"))

    def test_permission_check_error_with_errors(self):
        """Test that PermissionCheckError is raised with proper error details."""
        from general_manager.permission.base_permission import PermissionCheckError
        from django.contrib.auth.models import AnonymousUser

        user = AnonymousUser()
        errors = ["Error 1", "Error 2"]

        with self.assertRaises(PermissionCheckError) as ctx:
            raise PermissionCheckError(user, errors)

        self.assertIn("Permission denied", str(ctx.exception))
        self.assertIn("anonymous", str(ctx.exception))
        self.assertIn("Error 1", str(ctx.exception))
        self.assertIn("Error 2", str(ctx.exception))

    def test_permission_check_error_with_authenticated_user(self):
        """Test PermissionCheckError message with authenticated user."""
        from general_manager.permission.base_permission import PermissionCheckError

        # Create a mock user with id
        user = Mock()
        user.id = 42
        errors = ["Test error"]

        with self.assertRaises(PermissionCheckError) as ctx:
            raise PermissionCheckError(user, errors)

        self.assertIn("id=42", str(ctx.exception))
        self.assertIn("Test error", str(ctx.exception))

    def test_check_create_permission_raises_permission_check_error(self):
        """Test that check_create_permission raises PermissionCheckError on failure."""
        from general_manager.permission.base_permission import PermissionCheckError

        # Set up permission to fail
        DummyPermission.create_permissions = {"attribute": "dummy:deny"}

        dummy_manager = type("DummyManager", (), {"attribute": "test_value"})

        with self.assertRaises(PermissionCheckError) as ctx:
            DummyPermission.check_create_permission(
                {"attribute": "test_value"}, dummy_manager, self.dummy_user
            )

        self.assertIn("Permission denied", str(ctx.exception))

    def test_check_update_permission_raises_permission_check_error(self):
        """Test that check_update_permission raises PermissionCheckError on failure."""
        from general_manager.permission.base_permission import PermissionCheckError

        # Set up permission to fail
        DummyPermission.update_permissions = {"attribute": "dummy:deny"}

        with patch(
            "general_manager.permission.base_permission.PermissionDataManager.for_update"
        ) as mock_for_update:
            mock_for_update.return_value = PermissionDataManager(
                {"attribute": "new_value"}, None
            )
            with self.assertRaises(PermissionCheckError) as ctx:
                DummyPermission.check_update_permission(
                    {"attribute": "new_value"}, Mock(), self.dummy_user
                )

        self.assertIn("Permission denied", str(ctx.exception))

    def test_check_delete_permission_raises_permission_check_error(self):
        """Test that check_delete_permission raises PermissionCheckError on failure."""
        from general_manager.permission.base_permission import PermissionCheckError

        # Create a mock manager instance
        manager_instance = Mock()
        manager_instance.attribute = "test_value"

        # Set up permission to fail
        DummyPermission.delete_permissions = {"attribute": "dummy:deny"}

        with patch(
            "general_manager.permission.base_permission.PermissionDataManager"
        ) as mock_permission_manager:
            mock_permission_manager.return_value = Mock(spec=PermissionDataManager)
            with self.assertRaises(PermissionCheckError) as ctx:
                DummyPermission.check_delete_permission(
                    manager_instance, self.dummy_user
                )

        self.assertIn("Permission denied", str(ctx.exception))

    def test_permission_not_found_error(self):
        """Test that PermissionNotFoundError is raised for unknown permissions."""
        with self.assertRaises(PermissionNotFoundError) as ctx:
            self.permission_obj.validate_permission_string("nonexistent:config")

        self.assertIn("Permission", str(ctx.exception))
        self.assertIn("not found", str(ctx.exception))

    def test_get_permission_filter_with_invalid_permission_string(self):
        """Test get_permission_filter with invalid permission string."""
        DummyPermission.read_permissions = {"field": "invalid:permission"}

        with self.assertRaises(PermissionNotFoundError):
            self.permission_obj.get_permission_filter()

    def test_permission_multiple_errors_aggregation(self):
        """Test that multiple permission errors are aggregated properly."""
        from general_manager.permission.base_permission import PermissionCheckError

        # Set up multiple failing permissions
        DummyPermission.create_permissions = {
            "field1": "dummy:deny",
            "field2": "dummy:deny",
            "field3": "dummy:deny",
        }

        dummy_manager = type("DummyManager", (), {"attribute": "test_value"})

        with self.assertRaises(PermissionCheckError) as ctx:
            DummyPermission.check_create_permission(
                {"field1": "val1", "field2": "val2", "field3": "val3"},
                dummy_manager,
                self.dummy_user,
            )

        # Should contain all three errors
        error_str = str(ctx.exception)
        self.assertIn("field1", error_str)
        self.assertIn("field2", error_str)
        self.assertIn("field3", error_str)

    def test_permission_with_empty_data(self):
        """Test permission checks with empty data dictionaries."""
        # Should not raise any errors
        DummyPermission.check_create_permission({}, None, self.dummy_user)
        with patch(
            "general_manager.permission.base_permission.PermissionDataManager.for_update"
        ) as mock_for_update:
            mock_for_update.return_value = PermissionDataManager({}, None)
            DummyPermission.check_update_permission({}, Mock(), self.dummy_user)

    def test_get_user_with_id_authenticated(self):
        """Test get_user_with_id with authenticated user."""
        User = get_user_model()
        user = User.objects.create_user(
            username="test_user",
        )

        result = BasePermission.get_user_with_id(user)
        self.assertEqual(result, user)

    def test_get_user_with_id_anonymous(self):
        """Test get_user_with_id with anonymous user."""
        user = AnonymousUser()

        result = BasePermission.get_user_with_id(user)

        self.assertIs(result, user)

    def test_describe_permissions_default_implementation(self) -> None:
        """Test describe_permissions returns empty tuple by default."""
        result = self.permission_obj.describe_permissions("create", "field")
        self.assertEqual(result, ())

    def test_is_superuser_method(self) -> None:
        """Test _is_superuser helper method."""
        regular_permission = DummyPermission(self.dummy_instance, self.user)
        superuser_permission = DummyPermission(self.dummy_instance, self.superuser)
        
        self.assertFalse(regular_permission._is_superuser())
        self.assertTrue(superuser_permission._is_superuser())

    def test_is_superuser_with_anonymous_user(self) -> None:
        """Test _is_superuser returns False for anonymous users."""
        anonymous_permission = DummyPermission(self.dummy_instance, self.dummy_user)
        self.assertFalse(anonymous_permission._is_superuser())

    def test_check_create_permission_with_multiple_errors(self) -> None:
        """Test check_create_permission aggregates multiple errors."""
        from general_manager.permission.base_permission import PermissionCheckError
        
        DummyPermission.create_permissions = {
            "field1": "dummy:deny",
            "field2": "dummy:deny",
            "field3": "dummy:pass",
        }
        
        dummy_manager = type("DummyManager", (), {})
        
        with self.assertRaises(PermissionCheckError) as ctx:
            DummyPermission.check_create_permission(
                {"field1": "v1", "field2": "v2", "field3": "v3"},
                dummy_manager,
                self.dummy_user,
            )
        
        error_str = str(ctx.exception)
        self.assertIn("field1", error_str)
        self.assertIn("field2", error_str)
        self.assertNotIn("field3", error_str)  # field3 should pass

    def test_check_update_permission_with_superuser(self) -> None:
        """Test check_update_permission bypasses checks for superuser."""
        DummyPermission.update_permissions = {"field": "dummy:deny"}
        
        old_instance = Mock()
        old_instance.__class__.__name__ = "TestManager"
        
        with patch(
            "general_manager.permission.base_permission.PermissionDataManager.for_update"
        ) as mock_for_update:
            mock_for_update.return_value = PermissionDataManager({}, None)
            # Should not raise for superuser
            DummyPermission.check_update_permission(
                {"field": "value"}, old_instance, self.superuser
            )

    def test_check_delete_permission_with_superuser(self) -> None:
        """Test check_delete_permission bypasses checks for superuser."""
        DummyPermission.delete_permissions = {"field": "dummy:deny"}
        
        manager_instance = Mock()
        manager_instance.__class__.__name__ = "TestManager"
        manager_instance.__dict__ = {"field": "value"}
        
        with patch(
            "general_manager.permission.base_permission.PermissionDataManager"
        ) as mock_permission_manager:
            mock_permission_manager.return_value = Mock(spec=PermissionDataManager)
            # Should not raise for superuser
            DummyPermission.check_delete_permission(manager_instance, self.superuser)

    def test_get_user_with_id_with_primary_key(self) -> None:
        """Test get_user_with_id resolves user by primary key."""
        User = get_user_model()
        user = User.objects.create_user(username="pk_test_user")
        
        resolved = BasePermission.get_user_with_id(user.pk)
        self.assertEqual(resolved.id, user.id)
        self.assertEqual(resolved.username, "pk_test_user")

    def test_get_user_with_id_with_invalid_pk(self) -> None:
        """Test get_user_with_id returns AnonymousUser for invalid pk."""
        result = BasePermission.get_user_with_id(999999)
        self.assertIsInstance(result, AnonymousUser)

    def test_get_user_with_id_with_invalid_type(self) -> None:
        """Test get_user_with_id handles invalid types gracefully."""
        result = BasePermission.get_user_with_id("invalid")
        self.assertIsInstance(result, AnonymousUser)

    def test_get_user_with_id_with_none(self) -> None:
        """Test get_user_with_id handles None gracefully."""
        result = BasePermission.get_user_with_id(None)
        self.assertIsInstance(result, AnonymousUser)

    def test_permission_check_error_message_format(self) -> None:
        """Test PermissionCheckError message format."""
        from general_manager.permission.base_permission import PermissionCheckError
        
        errors = ["Error 1", "Error 2"]
        exc = PermissionCheckError(self.dummy_user, errors)
        
        msg = str(exc)
        self.assertIn("Permission denied", msg)
        self.assertIn("anonymous", msg)
        self.assertIn("Error 1", msg)
        self.assertIn("Error 2", msg)

    def test_check_permission_with_different_actions(self) -> None:
        """Test check_permission with all CRUD actions."""
        for action in ["create", "read", "update", "delete"]:
            result = self.permission_obj.check_permission(action, "test_attr")
            self.assertTrue(result)

    def test_get_permission_filter_with_multiple_read_permissions(self) -> None:
        """Test get_permission_filter with multiple read permissions."""
        DummyPermission.read_permissions = {
            "field1": "dummy:allow",
            "field2": "dummy:allow",
        }
        
        result = self.permission_obj.get_permission_filter()
        self.assertEqual(len(result), 2)
        for filter_dict in result:
            self.assertIn("filter", filter_dict)
            self.assertIn("exclude", filter_dict)

    def test_validate_permission_string_with_single_permission(self) -> None:
        """Test validate_permission_string with single permission."""
        result = self.permission_obj.validate_permission_string("dummy:pass")
        self.assertTrue(result)
        
        result = self.permission_obj.validate_permission_string("dummy:fail")
        self.assertFalse(result)

    def test_validate_permission_string_with_multiple_permissions(self) -> None:
        """Test validate_permission_string with ANDed permissions."""
        result = self.permission_obj.validate_permission_string("dummy:pass&dummy:pass")
        self.assertTrue(result)
        
        result = self.permission_obj.validate_permission_string("dummy:pass&dummy:fail")
        self.assertFalse(result)
        
        result = self.permission_obj.validate_permission_string("dummy:fail&dummy:fail")
        self.assertFalse(result)

    def test_superuser_bypasses_get_permission_filter(self) -> None:
        """Test superuser receives empty filter from _get_permission_filter."""
        DummyPermission.read_permissions = {"field": "dummy:allow"}
        superuser_permission = DummyPermission(self.dummy_instance, self.superuser)
        
        result = superuser_permission._get_permission_filter("dummy:allow")
        self.assertEqual(result, {"filter": {}, "exclude": {}})

    def test_check_create_permission_with_empty_manager_name(self) -> None:
        """Test check_create_permission handles manager without __name__."""
        DummyPermission.create_permissions = {}
        
        dummy_manager = type("", (), {})  # No __name__
        
        # Should not raise
        DummyPermission.check_create_permission(
            {"field": "value"}, dummy_manager, self.dummy_user
        )

    def test_check_update_permission_logs_user_id(self) -> None:
        """Test check_update_permission includes user_id in logs."""
        DummyPermission.update_permissions = {"field": "dummy:deny"}
        
        User = get_user_model()
        user = User.objects.create_user(
            username="log_test_user",
            password=get_random_string(12)
        )
        
        old_instance = Mock()
        old_instance.__class__.__name__ = "TestManager"
        
        with patch(
            "general_manager.permission.base_permission.PermissionDataManager.for_update"
        ) as mock_for_update:
            mock_for_update.return_value = PermissionDataManager({"field": "value"}, None)
            
            from general_manager.permission.base_permission import PermissionCheckError
            with self.assertRaises(PermissionCheckError):
                DummyPermission.check_update_permission(
                    {"field": "value"}, old_instance, user
                )

    def test_check_delete_permission_with_complex_instance(self) -> None:
        """Test check_delete_permission with instance having multiple attributes."""
        DummyPermission.delete_permissions = {}
        
        manager_instance = Mock()
        manager_instance.__class__.__name__ = "ComplexManager"
        manager_instance.__dict__ = {
            "field1": "value1",
            "field2": "value2",
            "field3": "value3",
        }
        
        with patch(
            "general_manager.permission.base_permission.PermissionDataManager"
        ) as mock_permission_manager:
            mock_permission_manager.return_value = Mock(spec=PermissionDataManager)
            # Should not raise
            DummyPermission.check_delete_permission(manager_instance, self.dummy_user)

    def test_instance_property(self) -> None:
        """Test instance property returns the instance."""
        self.assertEqual(self.permission_obj.instance, self.dummy_instance)

    def test_request_user_property(self) -> None:
        """Test request_user property returns the user."""
        self.assertEqual(self.permission_obj.request_user, self.dummy_user)

    def test_check_permission_all_actions_allowed(self) -> None:
        """Test check_permission when all actions are allowed."""
        DummyPermission.create_permissions = {}
        DummyPermission.read_permissions = {}
        DummyPermission.update_permissions = {}
        DummyPermission.delete_permissions = {}
        
        for action in ["create", "read", "update", "delete"]:
            self.assertTrue(self.permission_obj.check_permission(action, "any_field"))

    def test_permission_filter_with_list_of_permissions(self) -> None:
        """Test get_permission_filter when permission value is a list."""
        DummyPermission.read_permissions = {
            "field": ["dummy:allow", "dummy:allow"]
        }
        
        result = self.permission_obj.get_permission_filter()
        # Should have two filter entries for the list
        self.assertEqual(len(result), 2)

    def test_get_user_with_id_preserves_user_attributes(self) -> None:
        """Test get_user_with_id preserves all user attributes."""
        User = get_user_model()
        user = User.objects.create_user(
            username="attr_test",
            email="attr@test.com",
            password=get_random_string(12),
        )
        user.is_staff = True
        user.save()
        
        resolved = BasePermission.get_user_with_id(user.pk)
        self.assertEqual(resolved.username, "attr_test")
        self.assertEqual(resolved.email, "attr@test.com")
        self.assertTrue(resolved.is_staff)

    def test_superuser_shortcircuit_with_missing_is_superuser_attr(self) -> None:
        """Test _is_superuser handles objects without is_superuser attribute."""
        user_without_attr = Mock(spec=[])  # No is_superuser attribute
        permission = DummyPermission(self.dummy_instance, user_without_attr)
        self.assertFalse(permission._is_superuser())

    def test_check_create_permission_with_none_manager(self) -> None:
        """Test check_create_permission with None manager."""
        DummyPermission.create_permissions = {}
        
        # Should handle None manager gracefully
        DummyPermission.check_create_permission(
            {"field": "value"}, None, self.dummy_user
        )

    def test_permission_check_error_inherits_from_permission_error(self) -> None:
        """Test PermissionCheckError is a PermissionError subclass."""
        from general_manager.permission.base_permission import PermissionCheckError
        
        exc = PermissionCheckError(self.dummy_user, ["error"])
        self.assertIsInstance(exc, PermissionError)