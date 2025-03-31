from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal
from generalManager.src.permission.permissionChecks import (
    permission_functions,
    permission_filter,
)


if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser
    from generalManager.src.permission.permissionDataManager import (
        PermissionDataManager,
    )
    from generalManager.src.manager.generalManager import GeneralManager
    from generalManager.src.manager.meta import GeneralManagerMeta


class BasePermission(ABC):

    def __init__(
        self,
        instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
        request_user: AbstractUser,
    ) -> None:
        pass

    @property
    @abstractmethod
    def instance(self) -> PermissionDataManager | GeneralManager:
        raise NotImplementedError

    @property
    @abstractmethod
    def request_user(self) -> AbstractUser:
        raise NotImplementedError

    @abstractmethod
    def checkPermission(
        self,
        action: Literal["create", "read", "update", "delete"],
        attriubte: str,
    ) -> bool:
        raise NotImplementedError

    def getPermissionFilter(
        self,
    ) -> list[dict[Literal["filter", "exclude"], dict[str, str]]]:
        """
        Returns the filter for the permission
        """
        raise NotImplementedError

    def _getPermissionFilter(
        self, permission: str
    ) -> dict[Literal["filter", "exclude"], dict[str, str]]:
        """
        Returns the filter for the permission
        """
        permission_function, *config = permission.split(":")
        if permission_function not in permission_functions:
            raise ValueError(f"Permission {permission} not found")
        permission_filter = permission_functions[permission_function]["permission_filter"](
            self.request_user, config
        )
        if permission_filter is None:
            return {"filter": {}, "exclude": {}}
        return permission_filter

    def validatePermissionString(
        self,
        permission: str,
    ) -> bool:
        # permission can be a combination of multiple permissions
        # separated by "&" (e.g. "isAuthenticated&isMatchingKeyAccount")
        # this means that all sub_permissions must be true
        return all(
            [
                self.__validateSinglePermission(sub_permission)
                for sub_permission in permission.split("&")
            ]
        )

    def __validateSinglePermission(
        self,
        permission: str,
    ) -> bool:
        permission_function, *config = permission.split(":")
        if permission_function not in permission_functions:
            raise ValueError(f"Permission {permission} not found")

        return permission_functions[permission_function]["permission_method"](
            self.instance, self.request_user, config
        )
