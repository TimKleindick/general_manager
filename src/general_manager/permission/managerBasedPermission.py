"""Default permission implementation leveraging manager configuration."""

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
    """Permission implementation driven by class-level configuration lists."""

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
        Initialise the permission object and gather default and attribute-level rules.

        Parameters:
            instance (PermissionDataManager | GeneralManager): Target data used for permission evaluation.
            request_user (AbstractUser): User whose permissions are being checked.
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
        """Populate CRUD permissions using class-level defaults and overrides."""
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
        Retrieve the permission object referenced by ``__based_on__`` when configured.

        Returns:
            BasePermission | None: Permission instance for the related object, if applicable.

        Raises:
            ValueError: If the configured attribute does not exist on the instance.
            TypeError: If the attribute does not resolve to a `GeneralManager`.
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
        """Collect attribute-level permission overrides defined on the class."""
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
        """
        Determine whether the user has permission to perform ``action`` on ``attribute``.

        Parameters:
            action (permission_type): CRUD operation being evaluated.
            attriubte (str): Attribute name subject to the permission check.

        Returns:
            bool: True when the action is permitted.
        """
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
        """Return True if any permission expression in the list evaluates to True."""
        if not permissions:
            return True
        for permission in permissions:
            if self.validatePermissionString(permission):
                return True
        return False

    def getPermissionFilter(
        self,
    ) -> list[dict[Literal["filter", "exclude"], dict[str, str]]]:
        """Return queryset filters inferred from class-level permission configuration."""
        __based_on__ = getattr(self, "__based_on__")
        filters: list[dict[Literal["filter", "exclude"], dict[str, str]]] = []

        if self.__based_on_permission is not None:
            base_permissions = self.__based_on_permission.getPermissionFilter()
            for base_permission in base_permissions:
                filter = base_permission.get("filter", {})
                exclude = base_permission.get("exclude", {})
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
