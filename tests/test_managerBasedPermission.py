from __future__ import annotations
from typing import TYPE_CHECKING, Literal, Dict, Optional, cast
from django.test import TestCase
from django.contrib.auth.models import User, AnonymousUser
from unittest.mock import Mock, patch, PropertyMock, MagicMock

from general_manager.permission.basePermission import BasePermission
from general_manager.permission.managerBasedPermission import ManagerBasedPermission
from general_manager.permission.permissionChecks import (
    permission_functions,
    PermissionDict,
)

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager
    from general_manager.permission.permissionDataManager import (
        PermissionDataManager,
    )
    from django.contrib.auth.models import AbstractUser


class DummyPermission(BasePermission):
    """Test permission class for testing purposes."""

    def checkPermission(
        self,
        action: Literal["create", "read", "update", "delete"],
        attriubte: str,
    ) -> bool:
        return True

    def getPermissionFilter(
        self,
    ) -> list[dict[Literal["filter", "exclude"], dict[str, str]]]:
        return [{"filter": {"test": "value"}, "exclude": {}}]


class CustomManagerBasedPermission(ManagerBasedPermission):
    """Custom ManagerBasedPermission for testing."""

    __based_on__: Optional[str] = "manager"
    __read__: list[str] = ["public"]
    __create__: list[str] = ["isAuthenticated"]
    __update__: list[str] = ["admin"]
    __delete__: list[str] = ["isAuthenticated&admin"]

    # Test attribute-specific permissions
    specific_attribute: Dict[
        Literal["create", "read", "update", "delete"], list[str]
    ] = {
        "create": ["admin"],
        "read": ["public"],
        "update": ["admin"],
        "delete": ["admin"],
    }


class CustomManagerBasedPermissionNoBasis(ManagerBasedPermission):
    """Custom ManagerBasedPermission without a basis for testing."""

    __based_on__: Optional[str] = None
    __read__: list[str] = ["public"]
    __create__: list[str] = ["isAuthenticated"]
    __update__: list[str] = ["admin"]
    __delete__: list[str] = ["isAuthenticated&admin"]


class ManagerBasedPermissionTests(TestCase):
    def setUp(self):
        # Create a test user
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpassword"
        )

        # Create an admin user
        self.admin_user = User.objects.create_user(
            username="adminuser", email="admin@example.com", password="adminpassword"
        )
        self.admin_user.is_staff = True
        self.admin_user.save()

        # Anonymous user
        self.anonymous_user = AnonymousUser()

        # Create a mock instance
        self.mock_instance = Mock()

        # Store original permission functions
        self.original_permission_functions = permission_functions.copy()

        # Set up patches for GeneralManager
        # We'll patch the entire check in __getBasedOnPermission to avoid issubclass issues
        self.check_patcher = patch(
            "general_manager.permission.managerBasedPermission.ManagerBasedPermission._ManagerBasedPermission__getBasedOnPermission"
        )
        self.mock_check = self.check_patcher.start()

        # Create based_on permissions for different scenarios
        self.mock_permission = DummyPermission(Mock(), self.user)

        # By default, return None as the based_on permission
        self.mock_check.return_value = None

    def tearDown(self):
        # Restore original permission functions
        permission_functions.clear()
        permission_functions.update(self.original_permission_functions)

        # Stop all patches
        self.check_patcher.stop()

    def test_init(self):
        """Test initialization of ManagerBasedPermission."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)

        self.assertEqual(permission.instance, self.mock_instance)
        self.assertEqual(permission.request_user, self.user)

    def test_get_attribute_permissions(self):
        """Test getting attribute permissions."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)
        method = getattr(permission, "_ManagerBasedPermission__getAttributePermissions")
        attribute_permissions = method()

        self.assertIn("specific_attribute", attribute_permissions)
        self.assertEqual(
            attribute_permissions["specific_attribute"]["create"], ["admin"]
        )
        self.assertEqual(
            attribute_permissions["specific_attribute"]["read"], ["public"]
        )
        self.assertEqual(
            attribute_permissions["specific_attribute"]["update"], ["admin"]
        )
        self.assertEqual(
            attribute_permissions["specific_attribute"]["delete"], ["admin"]
        )

    def test_check_permission_read_with_public_access(self):
        """Test checking read permission with public access."""
        permission = CustomManagerBasedPermission(
            self.mock_instance, cast("AbstractUser", self.anonymous_user)
        )

        result = permission.checkPermission("read", "any_attribute")
        self.assertTrue(result)

    def test_check_permission_create_with_authenticated_user(self):
        """Test checking create permission with an authenticated user."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)

        result = permission.checkPermission("create", "any_attribute")
        self.assertTrue(result)

    def test_check_permission_create_with_anonymous_user(self):
        """Test checking create permission with an anonymous user."""
        permission = CustomManagerBasedPermission(
            self.mock_instance, cast("AbstractUser", self.anonymous_user)
        )

        result = permission.checkPermission("create", "any_attribute")
        self.assertFalse(result)

    def test_check_permission_update_with_admin_user(self):
        """Test checking update permission with an admin user."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.admin_user)

        result = permission.checkPermission("update", "any_attribute")
        self.assertTrue(result)

    def test_check_permission_update_with_regular_user(self):
        """Test checking update permission with a regular user."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)

        result = permission.checkPermission("update", "any_attribute")
        self.assertFalse(result)

    def test_check_permission_delete_with_admin_user(self):
        """Test checking delete permission with an admin user."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.admin_user)

        result = permission.checkPermission("delete", "any_attribute")
        self.assertTrue(result)

    def test_check_permission_delete_with_regular_user(self):
        """Test checking delete permission with a regular user."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)

        result = permission.checkPermission("delete", "any_attribute")
        self.assertFalse(result)

    def test_check_permission_with_based_on_denied(self):
        """Test checking permission when based_on permission denies it."""
        # Configure the mock to return a permission that denies access
        self.mock_check.return_value = Mock(checkPermission=Mock(return_value=False))

        permission = CustomManagerBasedPermission(self.mock_instance, self.admin_user)

        result = permission.checkPermission("update", "any_attribute")
        self.assertFalse(result)

    def test_check_permission_with_specific_attribute(self):
        """Test checking permission with attribute-specific permissions."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)

        # Regular user should not have permission to create on specific_attribute
        result = permission.checkPermission("create", "specific_attribute")
        self.assertFalse(result)

        # Admin user should have permission
        permission = CustomManagerBasedPermission(self.mock_instance, self.admin_user)
        result = permission.checkPermission("create", "specific_attribute")
        self.assertTrue(result)

    def test_check_permission_with_invalid_action(self):
        """Test checking permission with an invalid action raises ValueError."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)

        with self.assertRaises(ValueError):
            # Using an invalid action for testing
            permission.checkPermission("invalid", "any_attribute")  # type: ignore

    def test_check_specific_permission(self):
        """Test checking specific permissions."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)
        method = getattr(permission, "_ManagerBasedPermission__checkSpecificPermission")

        # Test with a valid permission that returns True
        with patch.object(
            CustomManagerBasedPermission, "validatePermissionString", return_value=True
        ):
            result = method(["some_permission"])
            self.assertTrue(result)

        # Test with permissions that all return False
        with patch.object(
            CustomManagerBasedPermission, "validatePermissionString", return_value=False
        ):
            result = method(["perm1", "perm2"])
            self.assertFalse(result)

    def test_get_permission_filter(self):
        """Test getting permission filters."""
        # Configure the mock to return a permission with filters
        based_on_filters = [
            {"filter": {"user": "test"}, "exclude": {"status": "deleted"}}
        ]
        self.mock_check.return_value = Mock(
            getPermissionFilter=Mock(return_value=based_on_filters)
        )

        permission = CustomManagerBasedPermission(self.mock_instance, self.user)
        filters = permission.getPermissionFilter()

        # Should have at least the based_on filters (prefixed with manager__) and one for __read__
        self.assertGreaterEqual(len(filters), 2)

        # Check that the based_on filter keys are properly prefixed
        self.assertEqual(filters[0]["filter"], {"manager__user": "test"})
        self.assertEqual(filters[0]["exclude"], {"manager__status": "deleted"})

    def test_get_permission_filter_no_based_on(self):
        """Test getting permission filters without a based_on permission."""
        # Configure the mock to return None (no based_on permission)
        self.mock_check.return_value = None

        permission = CustomManagerBasedPermissionNoBasis(self.mock_instance, self.user)
        filters = permission.getPermissionFilter()

        # Should have just the filters from __read__
        self.assertEqual(len(filters), 1)

    def test_permission_caching(self):
        """Test that permission results are cached."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.admin_user)

        # Mock the validatePermissionString method to track calls
        with patch.object(
            CustomManagerBasedPermission,
            "validatePermissionString",
            side_effect=lambda x: x == "admin",
        ) as mock_validate:

            # First call should call validatePermissionString
            result1 = permission.checkPermission("update", "any_attribute")
            self.assertTrue(result1)

            # Second call to the same action should use cached result
            result2 = permission.checkPermission("update", "different_attribute")
            self.assertTrue(result2)

            # The validate method should be called exactly once for the action
            self.assertEqual(mock_validate.call_count, 1)
