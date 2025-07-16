from __future__ import annotations
from django.contrib.auth.models import AbstractUser, AnonymousUser
from typing import Any
from general_manager.permission.basePermission import BasePermission

from general_manager.permission.permissionDataManager import PermissionDataManager
from general_manager.permission.utils import validatePermissionString


class MutationPermission:
    __mutate__: list[str]

    def __init__(
        self, data: dict[str, Any], request_user: AbstractUser | AnonymousUser
    ) -> None:
        self._data = PermissionDataManager(data)
        self._request_user = request_user
        self.__attribute_permissions = self.__getAttributePermissions()

        self.__overall_result: bool | None = None

    @property
    def data(self) -> PermissionDataManager:
        return self._data

    @property
    def request_user(self) -> AbstractUser | AnonymousUser:
        return self._request_user

    def __getAttributePermissions(
        self,
    ) -> dict[str, list[str]]:
        attribute_permissions = {}
        for attribute in self.__class__.__dict__:
            if not attribute.startswith("__"):
                attribute_permissions[attribute] = getattr(self.__class__, attribute)
        return attribute_permissions

    @classmethod
    def check(
        cls,
        data: dict[str, Any],
        request_user: AbstractUser | AnonymousUser | Any,
    ) -> None:
        """
        Check if the user has permission to perform the mutation based on the provided data.
        Raises:
            PermissionError: If the user does not have permission.
        """
        errors = []
        Permission = cls(data, BasePermission.getUserWithId(request_user))
        for key in data:
            if not Permission.checkPermission(key):
                errors.append(
                    f"Permission denied for {key} with value {data[key]} for user {request_user}"
                )
        if errors:
            raise PermissionError(f"Permission denied with errors: {errors}")

    def checkPermission(
        self,
        attribute: str,
    ) -> bool:

        has_attribute_permissions = attribute in self.__attribute_permissions

        if not has_attribute_permissions:
            last_result = self.__overall_result
            if last_result is not None:
                return last_result
            attribute_permission = True
        else:
            attribute_permission = self.__checkSpecificPermission(
                self.__attribute_permissions[attribute]
            )

        permission = self.__checkSpecificPermission(self.__mutate__)
        self.__overall_result = permission
        return permission and attribute_permission

    def __checkSpecificPermission(
        self,
        permissions: list[str],
    ) -> bool:
        for permission in permissions:
            if validatePermissionString(permission, self.data, self.request_user):
                return True
        return False
