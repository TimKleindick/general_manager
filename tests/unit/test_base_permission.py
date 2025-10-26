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

if TYPE_CHECKING:
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
        self.user = self.dummy_user
        self.permission_obj = DummyPermission(self.dummy_instance, self.dummy_user)
        self.original_class_permissions = {
            "create": DummyPermission.create_permissions.copy(),
            "read": DummyPermission.read_permissions.copy(),
            "update": DummyPermission.update_permissions.copy(),
            "delete": DummyPermission.delete_permissions.copy(),
        }

    def tearDown(self):
        # Wiederherstellen der originalen permission_functions
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
        Testet _get_permission_filter mit einem gültigen
        permission-String, der einen nicht-leeren Filter zurückgibt.
        """
        result = self.permission_obj._get_permission_filter("dummy:allow")
        expected = {"filter": {"dummy": "allowed"}, "exclude": {}}
        self.assertEqual(result, expected)

    def test_get_permission_filter_default(self):
        """
        Testet _get_permission_filter, wenn der Dummy-Filter None zurückgibt,
        sodass der Default-Wert zurückgegeben wird.
        """
        result = self.permission_obj._get_permission_filter("dummy:deny")
        expected = {"filter": {}, "exclude": {}}
        self.assertEqual(result, expected)

    def test_get_permission_filter_invalid_permission(self):
        """
        Testet _get_permission_filter mit einem ungültigen permission-String.
        Es sollte ein ValueError ausgelöst werden.
        """
        with self.assertRaises(PermissionNotFoundError):
            self.permission_obj._get_permission_filter("nonexistent:whatever")

    def test_validate_permission_string_all_true(self):
        """
        Testet validate_permission_string, wenn alle Sub-Permissions true ergeben.
        """
        result = self.permission_obj.validate_permission_string("dummy:pass")
        self.assertTrue(result)
        result2 = self.permission_obj.validate_permission_string(
            "dummy:pass&dummy:pass"
        )
        self.assertTrue(result2)

    def test_validate_permission_string_one_false(self):
        """
        Testet validate_permission_string, wenn eine der Sub-Permissions false ist.
        """
        result = self.permission_obj.validate_permission_string("dummy:pass&dummy:fail")
        self.assertFalse(result)

    def test_validate_permission_string_invalid_permission(self):
        """
        Testet validate_permission_string mit einem ungültigen permission-String.
        Es sollte ein ValueError ausgelöst werden.
        """
        with self.assertRaises(ValueError):
            self.permission_obj.validate_permission_string("nonexistent:whatever")

    def test_check_permission(self):
        """
        Testet die concrete Implementierung der check_permission-Methode.
        """
        self.assertTrue(self.permission_obj.check_permission("create", "attribute"))

    def test_get_permission_filter_public(self):
        """
        Testet die public get_permission_filter-Methode der DummyPermission.
        """
        DummyPermission.read_permissions = {"field": "dummy:allow"}
        result = self.permission_obj.get_permission_filter()
        expected = [{"filter": {"dummy": "allowed"}, "exclude": {}}]
        self.assertEqual(result, expected)

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

        with self.assertRaises(PermissionCheckError) as ctx:
            DummyPermission.check_create_permission(
                {"attribute": "test_value"}, None, self.dummy_user
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

        with self.assertRaises(PermissionCheckError) as ctx:
            DummyPermission.check_create_permission(
                {"field1": "val1", "field2": "val2", "field3": "val3"},
                None,
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
