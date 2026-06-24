"""Permission helper for GraphQL mutations."""

from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission.audit import (
    PermissionAuditEvent,
    audit_logging_enabled,
    emit_permission_audit_event,
)
from general_manager.permission.base_permission import (
    BasePermission,
    PermissionCheckError,
    UserLike,
)
from general_manager.permission.permission_data_manager import PermissionDataManager
from general_manager.permission.utils import validate_permission_string


logger = get_logger("permission.mutation")
_UNSET_MUTATE_PERMISSIONS = object()


class MutationPermission:
    """Evaluate custom GraphQL mutation permissions from class attributes.

    Subclasses declare global mutation expressions in ``__mutate__`` and may
    declare attribute-specific ``list[str]`` class attributes whose names match
    mutation payload keys. Global expressions are alternatives: any expression
    that evaluates to ``True`` grants the global mutation gate. For a payload
    field with attribute-specific permissions, both the global gate and that
    field's gate must pass; payload fields without a field-specific list use the
    global gate alone. Expressions inside one permission list are alternatives.
    Empty permission lists grant their gate, so ``__mutate__ = []`` allows the
    global gate and ``field = []`` allows that field gate. ``__mutate__`` is
    resolved through normal class inheritance; omitting it on the concrete class
    denies only when no base class provides it. ``__mutate__`` and field-specific
    values must be ``list`` instances whose items are all strings. Invalid
    ``__mutate__`` values deny the global gate; invalid field values are ignored.
    Field-specific lists are collected only from the concrete class dictionary.
    """

    __mutate__: ClassVar[list[str]]

    def __init__(self, data: dict[str, object], request_user: UserLike) -> None:
        """Create a mutation permission context for normalized data and user.

        ``data`` is the mutation argument mapping after GraphQL argument
        normalization. Manager-typed arguments are already manager instances.
        Dictionary payloads are wrapped in :class:`PermissionDataManager`, so
        custom registry permissions can use attribute access against field names.
        Direct construction expects an already resolved Django user or
        ``AnonymousUser``; use :meth:`check` when an identifier should be
        resolved first.

        Args:
            data: Mutation payload mapping field names to normalized values.
            request_user: Django user or anonymous user attempting the mutation.
        """
        self._data: PermissionDataManager[GeneralManager] = PermissionDataManager(data)
        self._request_user = request_user
        self.__attribute_permissions = self.__get_attribute_permissions()
        mutate_permissions: object = getattr(
            self.__class__,
            "__mutate__",
            _UNSET_MUTATE_PERMISSIONS,
        )
        self._mutate_permissions: list[str] | None
        if mutate_permissions is _UNSET_MUTATE_PERMISSIONS:
            self._mutate_permissions = None
        elif isinstance(mutate_permissions, list) and all(
            isinstance(item, str) for item in mutate_permissions
        ):
            self._mutate_permissions = list(mutate_permissions)
        else:
            self._mutate_permissions = None

        self.__overall_result: bool | None = None

    @property
    def data(self) -> PermissionDataManager[GeneralManager]:
        """Return wrapped mutation data used by registered permission methods."""
        return self._data

    @property
    def request_user(self) -> AbstractBaseUser | AnonymousUser:
        """Return the user whose permissions are being evaluated."""
        return self._request_user

    def __get_attribute_permissions(
        self,
    ) -> dict[str, list[str]]:
        """Collect concrete-class ``list[str]`` field permission declarations."""
        attribute_permissions: dict[str, list[str]] = {}
        for attribute, value in self.__class__.__dict__.items():
            if attribute.startswith("__"):
                continue
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                attribute_permissions[attribute] = list(value)
        return attribute_permissions

    def describe_permissions(self, attribute: str) -> tuple[str, ...]:
        """Return declared expressions for diagnostics in evaluation order.

        The result is ``__mutate__`` expressions followed by expressions declared
        directly on ``attribute``. Values are not deduplicated, and omitted
        ``__mutate__`` contributes no expression even though it denies the global
        gate.
        """
        base_permissions = tuple(self._mutate_permissions or [])
        attribute_permissions = tuple(self.__attribute_permissions.get(attribute, []))
        return base_permissions + attribute_permissions

    @classmethod
    def check(
        cls,
        data: dict[str, object],
        request_user: object,
    ) -> None:
        """Validate that ``request_user`` may execute the mutation for ``data``.

        Non-user values are resolved through
        :meth:`BasePermission.get_user_with_id`; missing or invalid identifiers
        become ``AnonymousUser``. Superusers bypass expression evaluation and
        produce granted audit events when audit logging is enabled. For
        non-superusers, every payload key is checked independently and all denied
        fields are collected before a single :class:`PermissionCheckError` is
        raised.

        Args:
            data: Mutation payload mapping field names to normalized values.
            request_user: User object, anonymous user, lazy user, or user
                identifier to evaluate.

        Raises:
            PermissionCheckError: If one or more payload fields fail permission
                checks.
        """
        errors: list[str] = []
        if not isinstance(request_user, (AbstractBaseUser, AnonymousUser)):
            request_user = BasePermission.get_user_with_id(request_user)
        resolved_user = request_user
        Permission = cls(data, request_user)
        class_name = cls.__name__
        is_audit_enabled = audit_logging_enabled()
        if getattr(resolved_user, "is_superuser", False):
            if is_audit_enabled:
                for key in data:
                    emit_permission_audit_event(
                        PermissionAuditEvent(
                            action="mutation",
                            attributes=(key,),
                            granted=True,
                            user=resolved_user,
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
                        user=resolved_user,
                        manager=class_name,
                        permissions=Permission.describe_permissions(key),
                    )
                )
            if not is_allowed:
                user_identifier = getattr(resolved_user, "id", None)
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
            raise PermissionCheckError(resolved_user, errors)

    def check_permission(
        self,
        attribute: str,
    ) -> bool:
        """Return whether the request user may mutate ``attribute``.

        The global ``__mutate__`` result is cached only for attributes without
        field-specific permissions. Attribute-specific permissions are evaluated
        every time their field is checked and are combined with the global gate.
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
        permissions: list[str] | None,
    ) -> bool:
        """Return ``True`` when any expression in ``permissions`` evaluates true."""
        if permissions is None:
            return False
        # Empty permissions list means no restrictions, so the field is allowed.
        if permissions == []:
            return True
        for permission in permissions:
            if validate_permission_string(permission, self.data, self.request_user):
                return True
        return False
