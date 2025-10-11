"""Utility helpers for evaluating permission expressions."""

from general_manager.permission.permissionChecks import (
    permission_functions,
)
from general_manager.permission.permissionDataManager import PermissionDataManager
from django.contrib.auth.models import AbstractUser, AnonymousUser

from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta


def validatePermissionString(
    permission: str,
    data: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    request_user: AbstractUser | AnonymousUser,
) -> bool:
    """
    Evaluate a compound permission expression joined by ``&`` operators.

    Parameters:
        permission (str): Permission expression (for example, ``isAuthenticated&admin``).
        data (PermissionDataManager | GeneralManager | GeneralManagerMeta): Object evaluated by the permission functions.
        request_user (AbstractUser | AnonymousUser): User performing the action.

    Returns:
        bool: True if every sub-permission evaluates to True.
    """

    def _validateSinglePermission(
        permission: str,
    ) -> bool:
        permission_function, *config = permission.split(":")
        if permission_function not in permission_functions:
            raise ValueError(f"Permission {permission} not found")

        return permission_functions[permission_function]["permission_method"](
            data, request_user, config
        )

    return all(
        [
            _validateSinglePermission(sub_permission)
            for sub_permission in permission.split("&")
        ]
    )
