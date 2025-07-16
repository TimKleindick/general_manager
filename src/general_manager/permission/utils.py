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
    # permission can be a combination of multiple permissions
    # separated by "&" (e.g. "isAuthenticated&isMatchingKeyAccount")
    # this means that all sub_permissions must be true
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
