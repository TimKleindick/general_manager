"""Permission helper for GraphQL mutations."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from general_manager.permission.audit import (
    PermissionAuditEvent,
    audit_logging_enabled,
    emit_permission_audit_event,
)
from general_manager.permission.base_permission import (
    BasePermission,
    PermissionCheckError,
)
from general_manager.permission.permission_data_manager import PermissionDataManager
from general_manager.permission.utils import validate_permission_string
from general_manager.logging import get_logger


logger = get_logger("permission.mutation")


class MutationPermission:
    """Evaluate mutation permissions using class-level configuration."""

    __mutate__: list[str]

    def __init__(
        self, data: dict[str, Any], request_user: AbstractBaseUser | AnonymousUser
    ) -> None:
        """
        Create a mutation permission context for the given data and user.

        Parameters:
            data (dict[str, Any]): Input payload for the mutation.
            request_user (AbstractBaseUser | AnonymousUser): User attempting the mutation.
        """
        self._data: PermissionDataManager = PermissionDataManager(data)
        self._request_user = request_user
        self.__attribute_permissions = self.__get_attribute_permissions()
        self._mutate_permissions: list[str] = getattr(self.__class__, "__mutate__", [])

        self.__overall_result: bool | None = None

    @property
    def data(self) -> PermissionDataManager:
        """Return wrapped permission data."""
        return self._data

    @property
    def request_user(self) -> AbstractBaseUser | AnonymousUser:
        """Return the user whose permissions are being evaluated."""
        return self._request_user

    def __get_attribute_permissions(
        self,
    ) -> dict[str, list[str]]:
        """Collect attribute-specific permission expressions declared on the class."""
        attribute_permissions = {}
        for attribute in self.__class__.__dict__:
            if not attribute.startswith("__"):
                attribute_permissions[attribute] = getattr(self.__class__, attribute)
        return attribute_permissions

    def describe_permissions(self, attribute: str) -> tuple[str, ...]:
        """Return mutate-level and attribute-specific permissions for the field."""
        base_permissions = tuple(self._mutate_permissions)
        attribute_permissions = tuple(self.__attribute_permissions.get(attribute, []))
        return base_permissions + attribute_permissions

    @classmethod
    def check(
        cls,
        data: dict[str, Any],
        request_user: AbstractBaseUser | AnonymousUser | Any,
    ) -> None:
        """
        Validate that the given user is authorized to perform the mutation described by `data`.

        Parameters:
            data (dict[str, Any]): Mutation payload mapping field names to values.
            request_user (AbstractBaseUser | AnonymousUser | Any): A user object or a user identifier; if an identifier is provided it will be resolved to a user.

        Raises:
            PermissionCheckError: Raised with the `request_user` and a list of field-level error messages when one or more fields fail their permission checks.
        """
        errors: list[str] = []
        if not isinstance(request_user, (AbstractBaseUser, AnonymousUser)):
            request_user = BasePermission.get_user_with_id(request_user)
        Permission = cls(data, request_user)
        class_name = cls.__name__
        is_audit_enabled = audit_logging_enabled()
        if getattr(request_user, "is_superuser", False):
            if is_audit_enabled:
                for key in data:
                    emit_permission_audit_event(
                        PermissionAuditEvent(
                            action="mutation",
                            attributes=(key,),
                            granted=True,
                            user=request_user,
                            manager=class_name,
                            permissions=Permission.describe_permissions(key),
                            bypassed=True,
                        )
                    )
            return
        for key in data:
            is_allowed = Permission.check_permission(key)
            if is_audit_enabled:
                emit_permission_audit_event(
                    PermissionAuditEvent(
                        action="mutation",
                        attributes=(key,),
                        granted=is_allowed,
                        user=request_user,
                        manager=class_name,
                        permissions=Permission.describe_permissions(key),
                    )
                )
            if not is_allowed:
                user_identifier = getattr(request_user, "id", None)
                logger.info(
                    "permission denied",
                    context={
                        "mutation": class_name,
                        "action": "mutation",
                        "attribute": key,
                        "user_id": user_identifier,
                    },
                )
                errors.append(f"Mutation permission denied for attribute '{key}'")
        if errors:
            raise PermissionCheckError(request_user, errors)

    def check_permission(
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
            attribute_permission = self.__check_specific_permission(
                self.__attribute_permissions[attribute]
            )

        permission = self.__check_specific_permission(self._mutate_permissions)
        self.__overall_result = permission
        return permission and attribute_permission

    def __check_specific_permission(
        self,
        permissions: list[str],
    ) -> bool:
        """Return True when any permission expression evaluates to True."""
        for permission in permissions:
            if validate_permission_string(permission, self.data, self.request_user):
                return True
        return False
