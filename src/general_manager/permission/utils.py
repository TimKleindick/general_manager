"""Utility helpers for evaluating permission expressions."""

from general_manager.permission.permissionChecks import (
    permission_functions,
)
from general_manager.permission.permissionDataManager import PermissionDataManager
from django.contrib.auth.models import AbstractUser, AnonymousUser

from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta


class PermissionNotFoundError(ValueError):
    """Raised when a referenced permission function is not registered."""

    def __init__(self, permission: str) -> None:
        """
        Exception raised when a referenced permission function cannot be found.

        Parameters:
            permission (str): The permission identifier that was not found; used to format the exception message.
        """
        super().__init__(f"Permission {permission} not found.")


def validatePermissionString(
    permission: str,
    data: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    request_user: AbstractUser | AnonymousUser,
) -> bool:
    """
    Evaluate a compound permission expression joined by '&' operators.

    Parameters:
        permission (str): Permission expression where sub-permissions are joined with '&'. Individual sub-permissions may include ':'-separated configuration parts (for example, "isAuthenticated&admin:level").
        data (PermissionDataManager | GeneralManager | GeneralManagerMeta): Object passed to each permission function.
        request_user (AbstractUser | AnonymousUser): User for whom permissions are evaluated.

    Returns:
        `true` if every sub-permission evaluates to True, `false` otherwise.

    Raises:
        PermissionNotFoundError: If a referenced permission function is not registered.
    """

    def _validateSinglePermission(
        permission: str,
    ) -> bool:
        """
        Evaluate a single sub-permission expression against the registered permission functions.

        Parameters:
                permission (str): A single permission fragment in the form "permission_name[:config...]" where parts after the first colon are passed as configuration.

        Returns:
                bool: `true` if the referenced permission function grants the permission, `false` otherwise.

        Raises:
                PermissionNotFoundError: If no registered permission function matches the `permission_name`.
        """
        permission_function, *config = permission.split(":")
        if permission_function not in permission_functions:
            raise PermissionNotFoundError(permission)

        return permission_functions[permission_function]["permission_method"](
            data, request_user, config
        )

    return all(
        [
            _validateSinglePermission(sub_permission)
            for sub_permission in permission.split("&")
        ]
    )
