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
