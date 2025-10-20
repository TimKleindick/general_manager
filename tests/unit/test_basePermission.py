from __future__ import annotations
from typing import TYPE_CHECKING, Literal, cast
from django.test import TestCase
from django.contrib.auth.models import AnonymousUser  # als Dummy-User
from general_manager.permission.basePermission import BasePermission
from general_manager.permission.permissionChecks import (
    permission_functions,
    PermissionDict,
)
from unittest.mock import Mock
from general_manager.permission.permissionDataManager import (
    PermissionDataManager,
)

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager
    from django.contrib.auth.models import AbstractUser

# Dummy-Funktionen für permission_functions


def dummy_permission_filter(
    user: AnonymousUser | AbstractUser, config: list[str]
) -> dict[Literal["filter", "exclude"], dict[str, str]] | None:
    """
    Dummy-Implementierung der Filter-Funktion:
    - Gibt einen Filter zurück, wenn der erste Parameter "allow" ist,
    - sonst None.
    """
    if config and config[0] == "allow":
        return {"filter": {"dummy": "allowed"}, "exclude": {}}
    return None


def dummy_permission_method(instance, user, config):
    """
    Dummy-Implementierung der Berechtigungsmethode:
    - Gibt True zurück, wenn der erste Parameter "pass" ist,
    - sonst False.
    """
    if config and config[0] == "pass":
        return True
    return False


# Dummy-Implementierung von BasePermission
class DummyPermission(BasePermission):
    def checkPermission(
        self,
        action: Literal["create", "read", "update", "delete"],
        attriubte: str,
    ) -> bool:
        return True  # Für Testzwecke immer True zurückgeben

    def getPermissionFilter(
        self,
    ) -> list[dict[Literal["filter", "exclude"], dict[str, str]]]:
        # Eine Dummy-Implementierung des public getPermissionFilter
        return [{"filter": {"test": "value"}, "exclude": {}}]


class BasePermissionTests(TestCase):
    def setUp(self):
        # Backup der originalen permission_functions und Überschreiben für Tests
        self.original_permission_functions = permission_functions.copy()
        permission_functions.clear()
        permission_functions["dummy"] = cast(
            PermissionDict,
            {
                "permission_filter": dummy_permission_filter,
                "permission_method": dummy_permission_method,
            },
        )
        # Dummy-Instanzen für instance und request_user
        self.dummy_instance = Mock(spec=PermissionDataManager)
        self.dummy_user = AnonymousUser()
        self.permission_obj = DummyPermission(self.dummy_instance, self.dummy_user)

    def tearDown(self):
        # Wiederherstellen der originalen permission_functions
        permission_functions.clear()
        permission_functions.update(self.original_permission_functions)

    def test_getPermissionFilter_valid(self):
        """
        Testet _getPermissionFilter mit einem gültigen
        permission-String, der einen nicht-leeren Filter zurückgibt.
        """
        result = self.permission_obj._getPermissionFilter("dummy:allow")
        expected = {"filter": {"dummy": "allowed"}, "exclude": {}}
        self.assertEqual(result, expected)

    def test_getPermissionFilter_default(self):
        """
        Testet _getPermissionFilter, wenn der Dummy-Filter None zurückgibt,
        sodass der Default-Wert zurückgegeben wird.
        """
        result = self.permission_obj._getPermissionFilter("dummy:deny")
        expected = {"filter": {}, "exclude": {}}
        self.assertEqual(result, expected)

    def test_getPermissionFilter_invalid_permission(self):
        """
        Testet _getPermissionFilter mit einem ungültigen permission-String.
        Es sollte ein ValueError ausgelöst werden.
        """
        with self.assertRaises(ValueError):
            self.permission_obj._getPermissionFilter("nonexistent:whatever")

    def test_validatePermissionString_all_true(self):
        """
        Testet validatePermissionString, wenn alle Sub-Permissions true ergeben.
        """
        result = self.permission_obj.validatePermissionString("dummy:pass")
        self.assertTrue(result)
        result2 = self.permission_obj.validatePermissionString("dummy:pass&dummy:pass")
        self.assertTrue(result2)

    def test_validatePermissionString_one_false(self):
        """
        Testet validatePermissionString, wenn eine der Sub-Permissions false ist.
        """
        result = self.permission_obj.validatePermissionString("dummy:pass&dummy:fail")
        self.assertFalse(result)

    def test_validatePermissionString_invalid_permission(self):
        """
        Testet validatePermissionString mit einem ungültigen permission-String.
        Es sollte ein ValueError ausgelöst werden.
        """
        with self.assertRaises(ValueError):
            self.permission_obj.validatePermissionString("nonexistent:whatever")

    def test_checkPermission(self):
        """
        Testet die concrete Implementierung der checkPermission-Methode.
        """
        self.assertTrue(self.permission_obj.checkPermission("create", "attribute"))

    def test_getPermissionFilter_public(self):
        """
        Testet die public getPermissionFilter-Methode der DummyPermission.
        """
        result = self.permission_obj.getPermissionFilter()
        expected = [{"filter": {"test": "value"}, "exclude": {}}]
        self.assertEqual(result, expected)

    def test_permission_check_error_with_errors(self):
        """Test that PermissionCheckError is raised with proper error details."""
        from general_manager.permission.basePermission import PermissionCheckError
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
        from general_manager.permission.basePermission import PermissionCheckError

        # Create a mock user with id
        user = Mock()
        user.id = 42
        errors = ["Test error"]

        with self.assertRaises(PermissionCheckError) as ctx:
            raise PermissionCheckError(user, errors)

        self.assertIn("id=42", str(ctx.exception))
        self.assertIn("Test error", str(ctx.exception))

    def test_check_create_permission_raises_permission_check_error(self):
        """Test that checkCreatePermission raises PermissionCheckError on failure."""
        from general_manager.permission.basePermission import PermissionCheckError

        # Set up permission to fail
        self.permission_obj.create_permissions = {"attribute": "dummy:deny"}

        with self.assertRaises(PermissionCheckError) as ctx:
            self.permission_obj.checkCreatePermission(
                {"attribute": "test_value"}, self.user
            )

        self.assertIn("Permission denied", str(ctx.exception))

    def test_check_update_permission_raises_permission_check_error(self):
        """Test that checkUpdatePermission raises PermissionCheckError on failure."""
        from general_manager.permission.basePermission import PermissionCheckError

        # Set up permission to fail
        self.permission_obj.update_permissions = {"attribute": "dummy:deny"}

        with self.assertRaises(PermissionCheckError) as ctx:
            self.permission_obj.checkUpdatePermission(
                {"attribute": "new_value"}, self.user
            )

        self.assertIn("Permission denied", str(ctx.exception))

    def test_check_delete_permission_raises_permission_check_error(self):
        """Test that checkDeletePermission raises PermissionCheckError on failure."""
        from general_manager.permission.basePermission import PermissionCheckError

        # Create a mock manager instance
        manager_instance = Mock()
        manager_instance.attribute = "test_value"

        # Set up permission to fail
        self.permission_obj.delete_permissions = {"attribute": "dummy:deny"}

        with self.assertRaises(PermissionCheckError) as ctx:
            self.permission_obj.checkDeletePermission(manager_instance, self.user)

        self.assertIn("Permission denied", str(ctx.exception))

    def test_permission_not_found_error(self):
        """Test that PermissionNotFoundError is raised for unknown permissions."""
        from general_manager.permission.utils import PermissionNotFoundError

        with self.assertRaises(PermissionNotFoundError) as ctx:
            self.permission_obj.validatePermissionString("nonexistent:config")

        self.assertIn("Permission", str(ctx.exception))
        self.assertIn("not found", str(ctx.exception))

    def test_get_permission_filter_with_invalid_permission_string(self):
        """Test getPermissionFilter with invalid permission string."""
        from general_manager.permission.utils import PermissionNotFoundError

        self.permission_obj.read_permissions = {"field": "invalid:permission"}

        with self.assertRaises(PermissionNotFoundError):
            self.permission_obj.getPermissionFilter()

    def test_permission_multiple_errors_aggregation(self):
        """Test that multiple permission errors are aggregated properly."""
        from general_manager.permission.basePermission import PermissionCheckError

        # Set up multiple failing permissions
        self.permission_obj.create_permissions = {
            "field1": "dummy:deny",
            "field2": "dummy:deny",
            "field3": "dummy:deny",
        }

        with self.assertRaises(PermissionCheckError) as ctx:
            self.permission_obj.checkCreatePermission(
                {"field1": "val1", "field2": "val2", "field3": "val3"},
                self.user,
            )

        # Should contain all three errors
        error_str = str(ctx.exception)
        self.assertIn("field1", error_str)
        self.assertIn("field2", error_str)
        self.assertIn("field3", error_str)

    def test_permission_with_empty_data(self):
        """Test permission checks with empty data dictionaries."""
        # Should not raise any errors
        self.permission_obj.checkCreatePermission({}, self.user)
        self.permission_obj.checkUpdatePermission({}, self.user)

    def test_get_user_with_id_authenticated(self):
        """Test getUserWithId with authenticated user."""
        user = Mock()
        user.id = 123
        user.is_authenticated = True

        result = BasePermission.getUserWithId(user, "test_action")
        self.assertEqual(result, user)

    def test_get_user_with_id_anonymous(self):
        """Test getUserWithId with anonymous user."""
        user = AnonymousUser()

        with self.assertRaises(PermissionError) as ctx:
            BasePermission.getUserWithId(user, "test_action")

        self.assertIn("test_action", str(ctx.exception))
        self.assertIn("requires authentication", str(ctx.exception))