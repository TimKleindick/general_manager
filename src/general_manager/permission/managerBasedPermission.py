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


class ManagerBasedPermission(BasePermission):
    __based_on__: Optional[str] = None
    __read__: list[str] = ["public"]
    __create__: list[str] = ["isAuthenticated"]
    __update__: list[str] = ["isAuthenticated"]
    __delete__: list[str] = ["isAuthenticated"]

    def __init__(
        self,
        instance: PermissionDataManager | GeneralManager,
        request_user: AbstractUser,
    ) -> None:

        self.__instance = instance
        self.__request_user = request_user
        self.__attribute_permissions = self.__getAttributePermissions()
        self.__based_on_permission = self.__getBasedOnPermission()
        self.__overall_results: Dict[permission_type, Optional[bool]] = {
            "create": None,
            "read": None,
            "update": None,
            "delete": None,
        }

    def __getBasedOnPermission(self) -> Optional[BasePermission]:
        from general_manager.manager.generalManager import GeneralManager

        __based_on__ = getattr(self, "__based_on__")
        if __based_on__ is None:
            return None

        basis_object = getattr(self.instance, __based_on__, None)
        if basis_object is None:
            raise ValueError(
                f"Based on object {__based_on__} not found in instance {self.__instance}"
            )
        if not isinstance(basis_object, GeneralManager) and not issubclass(
            basis_object, GeneralManager
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

    @property
    def instance(self) -> PermissionDataManager | GeneralManager:
        return self.__instance

    @property
    def request_user(self) -> AbstractUser:
        return self.__request_user

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
