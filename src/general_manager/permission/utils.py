"""Utility helpers for evaluating permission expressions."""

from general_manager.permission.permission_checks import (
    permission_functions,
)
from general_manager.permission.permission_data_manager import PermissionDataManager
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from general_manager.manager.general_manager import GeneralManager
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


def validate_permission_string(
    permission: str,
    data: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    request_user: AbstractBaseUser | AnonymousUser,
) -> bool:
    """
    Evaluate a compound permission expression joined by '&' operators.

    Parameters:
        permission (str): Permission expression where sub-permissions are joined with '&'. Individual sub-permissions may include ':'-separated configuration parts (for example, "isAuthenticated&admin:level").
        data (PermissionDataManager | GeneralManager | GeneralManagerMeta): Object passed to each permission function.
        request_user (AbstractBaseUser | AnonymousUser): User for whom permissions are evaluated.

    Returns:
        `true` if every sub-permission evaluates to True, `false` otherwise.

    Raises:
        PermissionNotFoundError: If a referenced permission function is not registered.
    """

    def _validate_single_permission(
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
            _validate_single_permission(sub_permission)
            for sub_permission in permission.split("&")
        ]
    )
