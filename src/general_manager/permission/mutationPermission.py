"""Permission helper for GraphQL mutations."""

from __future__ import annotations
from django.contrib.auth.models import AbstractUser, AnonymousUser
from typing import Any
from general_manager.permission.basePermission import (
    BasePermission,
    PermissionCheckError,
)

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
        Validate that the given user is authorized to perform the mutation described by `data`.

        Parameters:
            data (dict[str, Any]): Mutation payload mapping field names to values.
            request_user (AbstractUser | AnonymousUser | Any): A user object or a user identifier; if an identifier is provided it will be resolved to a user.

        Raises:
            PermissionCheckError: Raised with the `request_user` and a list of field-level error messages when one or more fields fail their permission checks.
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
            raise PermissionCheckError(request_user, errors)

    def checkPermission(
        self,
        attribute: str,
    ) -> bool:
        """
        Determine whether the request user is allowed to modify a specific attribute in the mutation payload.

        Updates the instance's cached overall permission result based on the class-level mutate permissions.

        Parameters:
            attribute (str): Name of the attribute to validate.

        Returns:
            True if modification of the attribute is allowed, False otherwise.
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
