"""Base permission contract used by GeneralManager instances."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from general_manager.logging import get_logger
from general_manager.permission.audit import (
    PermissionAuditEvent,
    audit_logging_enabled,
    emit_permission_audit_event,
)
from general_manager.permission.permission_checks import permission_functions
from general_manager.permission.permission_data_manager import PermissionDataManager
from general_manager.permission.utils import (
    PermissionNotFoundError,
    validate_permission_string,
)

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta

logger = get_logger("permission.base")

UserLike: TypeAlias = AbstractBaseUser | AnonymousUser


class PermissionCheckError(PermissionError):
    """Raised when permission evaluation fails for a user."""

    def __init__(self, user: UserLike, errors: list[str]) -> None:
        """
        Initialize a PermissionCheckError carrying the requesting user's identity and permission failure details.

        Parameters:
            user (UserLike): The user for whom permission evaluation failed; if the user has an `id`, it is included in the error message, otherwise the user is labeled "anonymous".
            errors (list[str]): A list of error messages describing individual permission failures.
        """
        user_id = getattr(user, "id", None)
        user_label = "anonymous" if user_id is None else f"id={user_id}"
        super().__init__(
            f"Permission denied for user {user_label} with errors: {errors}."
        )


class BasePermission(ABC):
    """Abstract base class defining CRUD permission checks for managers."""

    def __init__(
        self,
        instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
        request_user: UserLike,
    ) -> None:
        """Initialise the permission context for a specific manager and user."""
        self._instance = instance
        self._request_user = request_user

    @property
    def instance(self) -> PermissionDataManager | GeneralManager | GeneralManagerMeta:
        """Return the object against which permission checks are performed."""
        return self._instance

    @property
    def request_user(self) -> UserLike:
        """Return the user being evaluated for permission checks."""
        return self._request_user

    def describe_permissions(
        self,
        action: Literal["create", "read", "update", "delete"],
        attribute: str,
    ) -> tuple[str, ...]:
        """Return permission expressions associated with an action/attribute pair."""
        return ()

    def _is_superuser(self) -> bool:
        """Return True when the current request user bypasses permission checks."""
        return bool(getattr(self.request_user, "is_superuser", False))

    @classmethod
    def check_create_permission(
        cls,
        data: dict[str, Any],
        manager: type[GeneralManager],
        request_user: UserLike | Any,
    ) -> None:
        """
        Validate that the requesting user is allowed to create each attribute in the provided payload.

        Checks create permission for every key in `data` using the given `manager`. If any attribute is not permitted, raises a PermissionCheckError that includes the evaluated user and a list of denial messages.

        Parameters:
            data (dict[str, Any]): Mapping of attribute names to the values intended for creation.
            manager (type[GeneralManager]): Manager class that defines the model/schema against which permissions are checked.
            request_user (UserLike | Any): User instance or user id (will be resolved to a user or AnonymousUser).

        Raises:
            PermissionCheckError: If one or more attributes in `data` are denied for the resolved `request_user`.
        """
        request_user = cls.get_user_with_id(request_user)
        permission_data = PermissionDataManager(permission_data=data, manager=manager)
        Permission = cls(permission_data, request_user)
        manager_name = manager.__name__ if manager is not None else None
        if Permission._is_superuser():
            if audit_logging_enabled():
                for key in data.keys():
                    emit_permission_audit_event(
                        PermissionAuditEvent(
                            action="create",
                            attributes=(key,),
                            granted=True,
                            user=request_user,
                            manager=manager_name,
                            permissions=Permission.describe_permissions("create", key),
                            bypassed=True,
                        )
                    )
            return

        errors: list[str] = []
        user_identifier = getattr(request_user, "id", None)
        for key in data.keys():
            is_allowed = Permission.check_permission("create", key)
            if audit_logging_enabled():
                emit_permission_audit_event(
                    PermissionAuditEvent(
                        action="create",
                        attributes=(key,),
                        granted=is_allowed,
                        user=request_user,
                        manager=manager_name,
                        permissions=Permission.describe_permissions("create", key),
                    )
                )
            if not is_allowed:
                logger.info(
                    "permission denied",
                    context={
                        "manager": manager_name,
                        "action": "create",
                        "attribute": key,
                        "user_id": user_identifier,
                    },
                )
                errors.append(f"Create permission denied for attribute '{key}'")
        if errors:
            raise PermissionCheckError(request_user, errors)

    @classmethod
    def check_update_permission(
        cls,
        data: dict[str, Any],
        old_manager_instance: GeneralManager,
        request_user: UserLike | Any,
    ) -> None:
        """
        Validate whether the request_user can update the given fields on an existing manager instance.

        Parameters:
            data (dict[str, Any]): Mapping of attribute names to new values to be applied.
            old_manager_instance (GeneralManager): Existing manager instance whose current state is used to evaluate update permissions.
            request_user (UserLike | Any): User instance or user id; non-user values will be resolved to a User or AnonymousUser via get_user_with_id.

        Raises:
            PermissionCheckError: Raised with a list of error messages when one or more fields are not permitted to be updated.
        """
        request_user = cls.get_user_with_id(request_user)
        permission_data = PermissionDataManager.for_update(
            base_data=old_manager_instance, update_data=data
        )
        Permission = cls(permission_data, request_user)
        manager_name = old_manager_instance.__class__.__name__
        if Permission._is_superuser():
            if audit_logging_enabled():
                for key in data.keys():
                    emit_permission_audit_event(
                        PermissionAuditEvent(
                            action="update",
                            attributes=(key,),
                            granted=True,
                            user=request_user,
                            manager=manager_name,
                            permissions=Permission.describe_permissions("update", key),
                            bypassed=True,
                        )
                    )
            return

        errors: list[str] = []
        user_identifier = getattr(request_user, "id", None)
        for key in data.keys():
            is_allowed = Permission.check_permission("update", key)
            if audit_logging_enabled():
                emit_permission_audit_event(
                    PermissionAuditEvent(
                        action="update",
                        attributes=(key,),
                        granted=is_allowed,
                        user=request_user,
                        manager=manager_name,
                        permissions=Permission.describe_permissions("update", key),
                    )
                )
            if not is_allowed:
                logger.info(
                    "permission denied",
                    context={
                        "manager": manager_name,
                        "action": "update",
                        "attribute": key,
                        "user_id": user_identifier,
                    },
                )
                errors.append(f"Update permission denied for attribute '{key}'")
        if errors:
            raise PermissionCheckError(request_user, errors)

    @classmethod
    def check_delete_permission(
        cls,
        manager_instance: GeneralManager,
        request_user: UserLike | Any,
    ) -> None:
        """
        Validate that the request_user has delete permission for every attribute of the given manager instance.

        This resolves the provided request_user to a User/AnonymousUser, evaluates delete permission for each attribute present on manager_instance, collects any denied attributes into error messages, and raises PermissionCheckError if any permissions are denied.

        Parameters:
            manager_instance (GeneralManager): The manager object whose attributes will be checked for delete permission.
            request_user (UserLike | Any): The user (or user id) to evaluate; non-user values will be resolved to AnonymousUser.

        Raises:
            PermissionCheckError: If one or more attributes are not permitted for deletion by request_user. The exception carries the user and the list of denial messages.
        """
        request_user = cls.get_user_with_id(request_user)
        permission_data = PermissionDataManager(manager_instance)
        Permission = cls(permission_data, request_user)
        manager_name = manager_instance.__class__.__name__
        if Permission._is_superuser():
            if audit_logging_enabled():
                for key in manager_instance.__dict__.keys():
                    emit_permission_audit_event(
                        PermissionAuditEvent(
                            action="delete",
                            attributes=(key,),
                            granted=True,
                            user=request_user,
                            manager=manager_name,
                            permissions=Permission.describe_permissions("delete", key),
                            bypassed=True,
                        )
                    )
            return

        errors: list[str] = []
        user_identifier = getattr(request_user, "id", None)
        for key in manager_instance.__dict__.keys():
            is_allowed = Permission.check_permission("delete", key)
            if audit_logging_enabled():
                emit_permission_audit_event(
                    PermissionAuditEvent(
                        action="delete",
                        attributes=(key,),
                        granted=is_allowed,
                        user=request_user,
                        manager=manager_name,
                        permissions=Permission.describe_permissions("delete", key),
                    )
                )
            if not is_allowed:
                logger.info(
                    "permission denied",
                    context={
                        "manager": manager_name,
                        "action": "delete",
                        "attribute": key,
                        "user_id": user_identifier,
                    },
                )
                errors.append(f"Delete permission denied for attribute '{key}'")
        if errors:
            raise PermissionCheckError(request_user, errors)

    @staticmethod
    def get_user_with_id(
        user: Any | UserLike,
    ) -> UserLike:
        """
        Resolve a user identifier or user-like object to a Django User or AnonymousUser instance.

        If the input is already an AbstractBaseUser or AnonymousUser, it is returned unchanged. If the input is a primary key (or other value used to look up a User by id), the corresponding User is returned; if no such User exists, an AnonymousUser is returned.

        Parameters:
            user (Any | UserLike): A user object or a value to look up a User by primary key.

        Returns:
            UserLike: The resolved User instance, or an AnonymousUser when no matching User is found.
        """
        from django.contrib.auth import get_user_model

        User = get_user_model()
        if isinstance(user, (AbstractBaseUser, AnonymousUser)):
            return user
        try:
            return User.objects.get(pk=user)
        except (User.DoesNotExist, ValueError, TypeError):
            return AnonymousUser()

    @abstractmethod
    def check_permission(
        self,
        action: Literal["create", "read", "update", "delete"],
        attribute: str,
    ) -> bool:
        """
        Determine whether the given action is permitted on the specified attribute.

        Parameters:
            action (Literal["create", "read", "update", "delete"]): Operation being checked.
            attribute (str): Attribute name subject to the permission check.

        Returns:
            bool: True when the action is allowed.
        """
        raise NotImplementedError

    def get_permission_filter(
        self,
    ) -> list[dict[Literal["filter", "exclude"], dict[str, str]]]:
        """Return the filter/exclude constraints associated with this permission."""
        raise NotImplementedError

    def _get_permission_filter(
        self, permission: str
    ) -> dict[Literal["filter", "exclude"], dict[str, str]]:
        """
        Resolve the filter/exclude constraints associated with a permission expression.

        Parameters:
            permission (str): Permission expression of the form "<function_name>[:config,...]"; the leading name selects a permission function and the optional colon-separated values are passed as configuration.

        Returns:
            dict: A mapping with keys "filter" and "exclude", each a dict[str, str] describing query constraints to apply. If the resolved permission provides no constraints, both will be empty dicts.

        Raises:
            PermissionNotFoundError: If no permission function matches the leading name in `permission`.
        """
        if self._is_superuser():
            return {"filter": {}, "exclude": {}}
        permission_function, *config = permission.split(":")
        if permission_function not in permission_functions:
            raise PermissionNotFoundError(permission)
        permission_filter = permission_functions[permission_function][
            "permission_filter"
        ](self.request_user, config)
        if permission_filter is None:
            return {"filter": {}, "exclude": {}}
        return permission_filter

    def validate_permission_string(
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
        if self._is_superuser():
            return True
        return validate_permission_string(permission, self.instance, self.request_user)
