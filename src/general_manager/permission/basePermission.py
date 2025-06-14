from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal
from general_manager.permission.permissionChecks import (
    permission_functions,
    permission_filter,
)

from django.contrib.auth.models import AnonymousUser, AbstractUser
from general_manager.permission.permissionDataManager import PermissionDataManager

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta


class BasePermission(ABC):

    def __init__(
        self,
        instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
        request_user: AbstractUser | AnonymousUser,
    ) -> None:
        self._instance = instance
        self._request_user = request_user

    @property
    def instance(self) -> PermissionDataManager | GeneralManager | GeneralManagerMeta:
        return self._instance

    @property
    def request_user(self) -> AbstractUser | AnonymousUser:
        return self._request_user

    @classmethod
    def checkCreatePermission(
        cls,
        data: dict[str, Any],
        manager: type[GeneralManager],
        request_user: AbstractUser | AnonymousUser | Any,
    ) -> None:
        request_user = cls.getUserWithId(request_user)
        errors = []
        permission_data = PermissionDataManager(permission_data=data, manager=manager)
        Permission = cls(permission_data, request_user)
        for key in data.keys():
            is_allowed = Permission.checkPermission("create", key)
            if not is_allowed:
                errors.append(
                    f"Permission denied for {key} with value {data[key]} for user {request_user}"
                )
        if errors:
            raise PermissionError(
                f"Permission denied for user {request_user} with errors: {errors}"
            )

    @classmethod
    def checkUpdatePermission(
        cls,
        data: dict[str, Any],
        old_manager_instance: GeneralManager,
        request_user: AbstractUser | AnonymousUser | Any,
    ) -> None:
        request_user = cls.getUserWithId(request_user)

        errors = []
        permission_data = PermissionDataManager.forUpdate(
            base_data=old_manager_instance, update_data=data
        )
        Permission = cls(permission_data, request_user)
        for key in data.keys():
            is_allowed = Permission.checkPermission("update", key)
            if not is_allowed:
                errors.append(
                    f"Permission denied for {key} with value {data[key]} for user {request_user}"
                )
        if errors:
            raise PermissionError(
                f"Permission denied for user {request_user} with errors: {errors}"
            )

    @classmethod
    def checkDeletePermission(
        cls,
        manager_instance: GeneralManager,
        request_user: AbstractUser | AnonymousUser | Any,
    ) -> None:
        request_user = cls.getUserWithId(request_user)

        errors = []
        permission_data = PermissionDataManager(manager_instance)
        Permission = cls(permission_data, request_user)
        for key in manager_instance.__dict__.keys():
            is_allowed = Permission.checkPermission("delete", key)
            if not is_allowed:
                errors.append(
                    f"Permission denied for {key} with value {getattr(manager_instance, key)} for user {request_user}"
                )
        if errors:
            raise PermissionError(
                f"Permission denied for user {request_user} with errors: {errors}"
            )

    @staticmethod
    def getUserWithId(
        user: Any | AbstractUser | AnonymousUser,
    ) -> AbstractUser | AnonymousUser:
        """
        Returns the user with the given id
        """
        from django.contrib.auth.models import User

        if isinstance(user, (AbstractUser, AnonymousUser)):
            return user
        try:
            return User.objects.get(id=user)
        except User.DoesNotExist:
            return AnonymousUser()

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
        permission_filter = permission_functions[permission_function][
            "permission_filter"
        ](self.request_user, config)
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
