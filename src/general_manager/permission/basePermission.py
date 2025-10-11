"""Base permission contract used by GeneralManager instances."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal
from general_manager.permission.permissionChecks import (
    permission_functions,
    permission_filter,
)

from django.contrib.auth.models import AnonymousUser, AbstractUser
from general_manager.permission.permissionDataManager import PermissionDataManager
from general_manager.permission.utils import validatePermissionString

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta


class BasePermission(ABC):
    """Abstract base class defining CRUD permission checks for managers."""

    def __init__(
        self,
        instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
        request_user: AbstractUser | AnonymousUser,
    ) -> None:
        """Initialise the permission context for a specific manager and user."""
        self._instance = instance
        self._request_user = request_user

    @property
    def instance(self) -> PermissionDataManager | GeneralManager | GeneralManagerMeta:
        """Return the object against which permission checks are performed."""
        return self._instance

    @property
    def request_user(self) -> AbstractUser | AnonymousUser:
        """Return the user being evaluated for permission checks."""
        return self._request_user

    @classmethod
    def checkCreatePermission(
        cls,
        data: dict[str, Any],
        manager: type[GeneralManager],
        request_user: AbstractUser | AnonymousUser | Any,
    ) -> None:
        """Validate create permissions for the supplied payload."""
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
        """Validate update permissions for the supplied payload."""
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
        """Validate delete permissions for the supplied manager instance."""
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
        """Return a ``User`` instance given a primary key or user object."""
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
        """
        Determine whether the given action is permitted on the specified attribute.

        Parameters:
            action (Literal["create", "read", "update", "delete"]): Operation being checked.
            attriubte (str): Attribute name subject to the permission check.

        Returns:
            bool: True when the action is allowed.
        """
        raise NotImplementedError

    def getPermissionFilter(
        self,
    ) -> list[dict[Literal["filter", "exclude"], dict[str, str]]]:
        """Return the filter/exclude constraints associated with this permission."""
        raise NotImplementedError

    def _getPermissionFilter(
        self, permission: str
    ) -> dict[Literal["filter", "exclude"], dict[str, str]]:
        """Resolve a filter definition for the given permission string."""
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
        """
        Validate complex permission expressions joined by ``&`` operators.

        Parameters:
            permission (str): Permission expression (for example, ``isAuthenticated&isMatchingKeyAccount``).

        Returns:
            bool: True when every sub-permission evaluates to True for the current user.
        """
        return validatePermissionString(permission, self.instance, self.request_user)
