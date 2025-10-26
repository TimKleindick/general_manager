from __future__ import annotations
from typing import TYPE_CHECKING, ClassVar, Dict, Literal, Optional, cast
from django.test import TestCase
from django.contrib.auth.models import User, AnonymousUser
from django.utils.crypto import get_random_string
from unittest.mock import Mock, patch, PropertyMock, MagicMock

from general_manager.permission.base_permission import BasePermission
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.permission.permission_checks import (
    permission_functions,
    PermissionDict,
)

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.permission.permission_data_manager import (
        PermissionDataManager,
    )
    from django.contrib.auth.models import AbstractUser


class DummyPermission(BasePermission):
    """Test permission class for testing purposes."""

    def check_permission(
        self,
        action: Literal["create", "read", "update", "delete"],
        attribute: str,
    ) -> bool:
        return True

    def get_permission_filter(
        self,
    ) -> list[dict[Literal["filter", "exclude"], dict[str, str]]]:
        return [{"filter": {"test": "value"}, "exclude": {}}]


class CustomManagerBasedPermission(ManagerBasedPermission):
    """Custom ManagerBasedPermission for testing."""

    __based_on__: Optional[str] = "manager"
    __read__: ClassVar[list[str]] = ["public"]
    __create__: ClassVar[list[str]] = ["isAuthenticated"]
    __update__: ClassVar[list[str]] = ["isAdmin"]
    __delete__: ClassVar[list[str]] = ["isAuthenticated&isAdmin"]

    # Test attribute-specific permissions
    specific_attribute: ClassVar[
        Dict[Literal["create", "read", "update", "delete"], list[str]]
    ] = {
        "create": ["isAdmin"],
        "read": ["public"],
        "update": ["isAdmin"],
        "delete": ["isAdmin"],
    }


class CustomManagerBasedPermissionNoBasis(ManagerBasedPermission):
    """Custom ManagerBasedPermission without a basis for testing."""

    __based_on__: Optional[str] = None
    __read__: ClassVar[list[str]] = ["public"]
    __create__: ClassVar[list[str]] = ["isAuthenticated"]
    __update__: ClassVar[list[str]] = ["isAdmin"]
    __delete__: ClassVar[list[str]] = ["isAuthenticated&isAdmin"]


class ManagerBasedPermissionTests(TestCase):
    def setUp(self):
        # Create a test user
        """
        Prepare fixtures for ManagerBasedPermission tests.

        Sets up test users (regular and staff admin), an AnonymousUser, a Mock instance, and a DummyPermission used as a potential based-on permission. Stores a copy of the current permission_functions and starts a patch for ManagerBasedPermission.__get_based_on_permission, configuring that patched method to return None by default.

        Attributes set on self:
            user: regular test User
            admin_user: staff User
            anonymous_user: AnonymousUser instance
            mock_instance: Mock used as the manager/instance under test
            original_permission_functions: copy of permission_functions prior to test modifications
            check_patcher: patcher for ManagerBasedPermission.__get_based_on_permission
            mock_check: started patch object for the patched method
            mock_permission: DummyPermission instance
        """
        user_password = get_random_string(12)
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password=user_password
        )

        # Create an admin user
        admin_password = get_random_string(12)
        self.admin_user = User.objects.create_user(
            username="adminuser", email="admin@example.com", password=admin_password
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
        # We'll patch the entire check in __get_based_on_permission to avoid issubclass issues
        self.check_patcher = patch(
            "general_manager.permission.manager_based_permission.ManagerBasedPermission._ManagerBasedPermission__get_based_on_permission"
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
        method = permission._ManagerBasedPermission__get_attribute_permissions
        attribute_permissions = method()

        self.assertIn("specific_attribute", attribute_permissions)
        self.assertEqual(
            attribute_permissions["specific_attribute"]["create"], ["isAdmin"]
        )
        self.assertEqual(
            attribute_permissions["specific_attribute"]["read"], ["public"]
        )
        self.assertEqual(
            attribute_permissions["specific_attribute"]["update"], ["isAdmin"]
        )
        self.assertEqual(
            attribute_permissions["specific_attribute"]["delete"], ["isAdmin"]
        )

    def test_check_permission_read_with_public_access(self):
        """Test checking read permission with public access."""
        permission = CustomManagerBasedPermission(
            self.mock_instance, cast("AbstractUser", self.anonymous_user)
        )

        result = permission.check_permission("read", "any_attribute")
        self.assertTrue(result)

    def test_check_permission_create_with_authenticated_user(self):
        """Test checking create permission with an authenticated user."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)

        result = permission.check_permission("create", "any_attribute")
        self.assertTrue(result)

    def test_check_permission_create_with_anonymous_user(self):
        """Test checking create permission with an anonymous user."""
        permission = CustomManagerBasedPermission(
            self.mock_instance, cast("AbstractUser", self.anonymous_user)
        )

        result = permission.check_permission("create", "any_attribute")
        self.assertFalse(result)

    def test_check_permission_update_with_admin_user(self):
        """Test checking update permission with an admin user."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.admin_user)

        result = permission.check_permission("update", "any_attribute")
        self.assertTrue(result)

    def test_check_permission_update_with_regular_user(self):
        """Test checking update permission with a regular user."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)

        result = permission.check_permission("update", "any_attribute")
        self.assertFalse(result)

    def test_check_permission_delete_with_admin_user(self):
        """Test checking delete permission with an admin user."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.admin_user)

        result = permission.check_permission("delete", "any_attribute")
        self.assertTrue(result)

    def test_check_permission_delete_with_regular_user(self):
        """Test checking delete permission with a regular user."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)

        result = permission.check_permission("delete", "any_attribute")
        self.assertFalse(result)

    def test_check_permission_with_based_on_denied(self):
        """Test checking permission when based_on permission denies it."""
        # Configure the mock to return a permission that denies access
        self.mock_check.return_value = Mock(check_permission=Mock(return_value=False))

        permission = CustomManagerBasedPermission(self.mock_instance, self.admin_user)

        result = permission.check_permission("update", "any_attribute")
        self.assertFalse(result)

    def test_check_permission_with_specific_attribute(self):
        """Test checking permission with attribute-specific permissions."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)

        # Regular user should not have permission to create on specific_attribute
        result = permission.check_permission("create", "specific_attribute")
        self.assertFalse(result)

        # Admin user should have permission
        permission = CustomManagerBasedPermission(self.mock_instance, self.admin_user)
        result = permission.check_permission("create", "specific_attribute")
        self.assertTrue(result)

    def test_check_permission_with_invalid_action(self):
        """Test checking permission with an invalid action raises ValueError."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)

        with self.assertRaises(ValueError):
            # Using an invalid action for testing
            permission.check_permission("invalid", "any_attribute")  # type: ignore

    def test_check_specific_permission(self):
        """Test checking specific permissions."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.user)
        method = permission._ManagerBasedPermission__check_specific_permission

        # Test with a valid permission that returns True
        with patch.object(
            CustomManagerBasedPermission,
            "validate_permission_string",
            return_value=True,
        ):
            result = method(["some_permission"])
            self.assertTrue(result)

        # Test with permissions that all return False
        with patch.object(
            CustomManagerBasedPermission,
            "validate_permission_string",
            return_value=False,
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
            get_permission_filter=Mock(return_value=based_on_filters)
        )

        permission = CustomManagerBasedPermission(self.mock_instance, self.user)
        filters = permission.get_permission_filter()

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
        filters = permission.get_permission_filter()

        # Should have just the filters from __read__
        self.assertEqual(len(filters), 1)

    def test_permission_caching(self):
        """Test that permission results are cached."""
        permission = CustomManagerBasedPermission(self.mock_instance, self.admin_user)

        # Mock the validate_permission_string method to track calls
        with patch.object(
            CustomManagerBasedPermission,
            "validate_permission_string",
            side_effect=lambda x: x == "isAdmin",
        ) as mock_validate:
            # First call should call validate_permission_string
            result1 = permission.check_permission("update", "any_attribute")
            self.assertTrue(result1)

            # Second call to the same action should use cached result
            result2 = permission.check_permission("update", "different_attribute")
            self.assertTrue(result2)

            # The validate method should be called exactly once for the action
            self.assertEqual(mock_validate.call_count, 1)
