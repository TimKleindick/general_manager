"""Utility helpers for evaluating permission expressions."""

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from general_manager.permission.permission_checks import (
    PermissionSubject,
    permission_functions,
)


class PermissionNotFoundError(ValueError):
    """Raised when a permission expression references an unregistered name."""

    def __init__(self, permission: str) -> None:
        """Build the error for an unresolved permission expression.

        Args:
            permission: Full permission fragment that failed lookup, including
                any colon-separated configuration values.
        """
        self.permission = permission
        super().__init__(f"Permission {permission} not found.")


def validate_permission_string(
    permission: str,
    data: PermissionSubject,
    request_user: AbstractBaseUser | AnonymousUser,
) -> bool:
    """Evaluate a permission expression against the global registry.

    Permission strings use simple splitting: ``&`` joins required fragments and
    ``:`` separates the registered permission name from configuration values.
    For example, ``"isAuthenticated&belongsToCustomer:customer"`` first calls
    the ``isAuthenticated`` method with an empty config list, then calls
    ``belongsToCustomer`` with ``["customer"]`` if the first check passed.
    Empty fragments and empty config segments are preserved by normal string
    splitting, so ``""`` tries to resolve an empty permission name, ``"rule:"``
    passes ``[""]``, and ``"rule&&other"`` tries to resolve an empty permission
    name between the two ampersands.

    Fragments are evaluated left-to-right and short-circuit on the first
    ``False`` result. A later unknown permission is therefore reported only when
    every earlier fragment grants access. Custom permission methods are expected
    to return ``bool`` and their result is normalized through ``bool(...)``.
    They are called without exception wrapping.

    Args:
        permission: Permission expression to evaluate.
        data: Manager instance, manager class, or permission data wrapper passed
            unchanged to each permission method.
        request_user: Django user or anonymous user being checked.

    Returns:
        ``True`` when every reached permission method returns ``True``;
        otherwise ``False``.

    Raises:
        PermissionNotFoundError: If a reached fragment references an
            unregistered permission name.
    """

    def _validate_single_permission(
        permission: str,
    ) -> bool:
        permission_function, *config = permission.split(":")
        if permission_function not in permission_functions:
            raise PermissionNotFoundError(permission)

        return bool(
            permission_functions[permission_function]["permission_method"](
                data, request_user, config
            )
        )

    return all(
        _validate_single_permission(sub_permission)
        for sub_permission in permission.split("&")
    )
