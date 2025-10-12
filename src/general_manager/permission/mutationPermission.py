"""Permission helper for GraphQL mutations."""

from __future__ import annotations
from django.contrib.auth.models import AbstractUser, AnonymousUser
from typing import Any
from general_manager.permission.basePermission import BasePermission

from general_manager.permission.permissionDataManager import PermissionDataManager
from general_manager.permission.utils import validatePermissionString


class MutationPermission:
    """Evaluate mutation permissions using class-level configuration."""

    __mutate__: list[str]

    def __init__(
        self, data: dict[str, Any], request_user: AbstractUser | AnonymousUser
    ) -> None:
        """
        Create a mutation permission context for the given data and user.

        Parameters:
            data (dict[str, Any]): Input payload for the mutation.
            request_user (AbstractUser | AnonymousUser): User attempting the mutation.
        """
        self._data: PermissionDataManager = PermissionDataManager(data)
        self._request_user = request_user
        self.__attribute_permissions = self.__getAttributePermissions()

        self.__overall_result: bool | None = None

    @property
    def data(self) -> PermissionDataManager:
        """Return wrapped permission data."""
        return self._data

    @property
    def request_user(self) -> AbstractUser | AnonymousUser:
        """Return the user whose permissions are being evaluated."""
        return self._request_user

    def __getAttributePermissions(
        self,
    ) -> dict[str, list[str]]:
        """Collect attribute-specific permission expressions declared on the class."""
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
        Validate that ``request_user`` may execute the mutation for the provided data.

        Parameters:
            data (dict[str, Any]): Mutation payload.
            request_user (AbstractUser | AnonymousUser | Any): User or user ID.

        Raises:
            PermissionError: If any field-level permission check fails.
        """
        errors = []
        if not isinstance(request_user, (AbstractUser, AnonymousUser)):
            request_user = BasePermission.getUserWithId(request_user)
        Permission = cls(data, request_user)
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
        """
        Evaluate permissions for a specific attribute within the mutation payload.

        Parameters:
            attribute (str): Attribute name being validated.

        Returns:
            bool: True when permitted, False otherwise.
        """

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
        """Return True when any permission expression evaluates to True."""
        for permission in permissions:
            if validatePermissionString(permission, self.data, self.request_user):
                return True
        return False
