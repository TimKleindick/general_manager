from __future__ import annotations
from typing import TYPE_CHECKING, Literal, Optional, Dict
from general_manager.permission.basePermission import BasePermission

if TYPE_CHECKING:
    from general_manager.permission.permissionDataManager import (
        PermissionDataManager,
    )
    from general_manager.manager.generalManager import GeneralManager
    from django.contrib.auth.models import AbstractUser

type permission_type = Literal[
    "create",
    "read",
    "update",
    "delete",
]


class notExistent:
    pass


class ManagerBasedPermission(BasePermission):
    __based_on__: Optional[str] = None
    __read__: list[str]
    __create__: list[str]
    __update__: list[str]
    __delete__: list[str]

    def __init__(
        self,
        instance: PermissionDataManager | GeneralManager,
        request_user: AbstractUser,
    ) -> None:
        """
        Initializes the ManagerBasedPermission with a manager instance and the requesting user.
        
        Configures default CRUD permissions, collects attribute-specific permissions, and sets up any related "based on" permission for cascading checks.
        """
        super().__init__(instance, request_user)
        self.__setPermissions()

        self.__attribute_permissions = self.__getAttributePermissions()
        self.__based_on_permission = self.__getBasedOnPermission()
        self.__overall_results: Dict[permission_type, Optional[bool]] = {
            "create": None,
            "read": None,
            "update": None,
            "delete": None,
        }

    def __setPermissions(self, skip_based_on: bool = False) -> None:

        """
        Assigns default permission lists for CRUD actions based on the presence of a related permission attribute.
        
        If the permission is based on another attribute and `skip_based_on` is False, all default permissions are set to empty lists. Otherwise, read permissions default to `["public"]` and write permissions to `["isAuthenticated"]`. Class-level overrides are respected if present.
        """
        default_read = ["public"]
        default_write = ["isAuthenticated"]

        if self.__based_on__ is not None and not skip_based_on:
            default_read = []
            default_write = []

        self.__read__ = getattr(self.__class__, "__read__", default_read)
        self.__create__ = getattr(self.__class__, "__create__", default_write)
        self.__update__ = getattr(self.__class__, "__update__", default_write)
        self.__delete__ = getattr(self.__class__, "__delete__", default_write)

    def __getBasedOnPermission(self) -> Optional[BasePermission]:
        """
        Retrieves the permission object associated with the `__based_on__` attribute, if present and valid.
        
        Returns:
            An instance of the related `BasePermission` subclass if the `__based_on__` attribute exists on the instance and its `Permission` class is a subclass of `BasePermission`; otherwise, returns `None`.
        
        Raises:
            ValueError: If the `__based_on__` attribute is missing from the instance.
            TypeError: If the `__based_on__` attribute is not a `GeneralManager` or its subclass.
        """
        from general_manager.manager.generalManager import GeneralManager

        __based_on__ = getattr(self, "__based_on__")
        if __based_on__ is None:
            return None

        basis_object = getattr(self.instance, __based_on__, notExistent)
        if basis_object is notExistent:
            raise ValueError(
                f"Based on configuration '{__based_on__}' is not valid or does not exist."
            )
        if basis_object is None:
            self.__setPermissions(skip_based_on=True)
            return None
        if not isinstance(basis_object, GeneralManager) and not (
            isinstance(basis_object, type) and issubclass(basis_object, GeneralManager)
        ):
            raise TypeError(f"Based on object {__based_on__} is not a GeneralManager")

        Permission = getattr(basis_object, "Permission", None)

        if Permission is None or not issubclass(
            Permission,
            BasePermission,
        ):
            return None

        return Permission(
            instance=getattr(self.instance, __based_on__),
            request_user=self.request_user,
        )

    def __getAttributePermissions(
        self,
    ) -> dict[str, dict[permission_type, list[str]]]:
        attribute_permissions = {}
        for attribute in self.__class__.__dict__:
            if not attribute.startswith("__"):
                attribute_permissions[attribute] = getattr(self, attribute)
        return attribute_permissions

    def checkPermission(
        self,
        action: permission_type,
        attriubte: str,
    ) -> bool:
        if (
            self.__based_on_permission
            and not self.__based_on_permission.checkPermission(action, attriubte)
        ):
            return False

        if action == "create":
            permissions = self.__create__
        elif action == "read":
            permissions = self.__read__
        elif action == "update":
            permissions = self.__update__
        elif action == "delete":
            permissions = self.__delete__
        else:
            raise ValueError(f"Action {action} not found")

        has_attribute_permissions = (
            attriubte in self.__attribute_permissions
            and action in self.__attribute_permissions[attriubte]
        )

        if not has_attribute_permissions:
            last_result = self.__overall_results.get(action)
            if last_result is not None:
                return last_result
            attribute_permission = True
        else:
            attribute_permission = self.__checkSpecificPermission(
                self.__attribute_permissions[attriubte][action]
            )

        permission = self.__checkSpecificPermission(permissions)
        self.__overall_results[action] = permission
        return permission and attribute_permission

    def __checkSpecificPermission(
        self,
        permissions: list[str],
    ) -> bool:
        """
        Return True if no permissions are required or if at least one permission string is valid for the user.
        
        If the permissions list is empty, access is granted. Otherwise, returns True if any permission string in the list is validated for the user; returns False if none are valid.
        """
        if not permissions:
            return True
        for permission in permissions:
            if self.validatePermissionString(permission):
                return True
        return False

    def getPermissionFilter(
        self,
    ) -> list[dict[Literal["filter", "exclude"], dict[str, str]]]:
        """
        Returns the filter for the permission
        """
        __based_on__ = getattr(self, "__based_on__")
        filters: list[dict[Literal["filter", "exclude"], dict[str, str]]] = []

        if self.__based_on_permission is not None:
            base_permissions = self.__based_on_permission.getPermissionFilter()
            for permission in base_permissions:
                filter = permission.get("filter", {})
                exclude = permission.get("exclude", {})
                filters.append(
                    {
                        "filter": {
                            f"{__based_on__}__{key}": value
                            for key, value in filter.items()
                        },
                        "exclude": {
                            f"{__based_on__}__{key}": value
                            for key, value in exclude.items()
                        },
                    }
                )

        for permission in self.__read__:
            filters.append(self._getPermissionFilter(permission))

        return filters
