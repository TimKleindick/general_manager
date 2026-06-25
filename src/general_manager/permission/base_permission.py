"""Base permission contract used by GeneralManager instances."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias, TypedDict, cast

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.utils.functional import SimpleLazyObject, empty

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
ReadPermissionReason: TypeAlias = Literal[
    "unfilterable_read_rule",
    "based_on_class_context",
    "filter_key_conflict",
    "no_prefilter_backend",
]


class PermissionConstraint(TypedDict, total=False):
    """Internal optional filter/exclude mappings for one read-permission alternative.

    This type is not exported from ``general_manager.permission`` and has no
    stable public import path.

    Lookup value types are backend-specific and intentionally typed as
    ``object``; static type checkers cannot validate lookup/value compatibility
    for a particular queryset or search backend.
    """

    filter: dict[str, object]
    exclude: dict[str, object]


@dataclass(slots=True)
class ReadPermissionPlan:
    """Represent read prefilters plus whether an instance gate is still required.

    This is an internal adapter type used by permission classes and GraphQL
    resolvers. It is not exported from ``general_manager.permission`` and has no
    stable public import path. The behavior below documents generated resolver
    compatibility for maintainers; it does not make this adapter a user-facing
    import contract.

    ``filters`` is an ordered list of ``PermissionConstraint`` entries. Each item
    may contain a ``"filter"`` mapping, an ``"exclude"`` mapping, both, or
    neither; those optional keys are represented by ``TypedDict(total=False)``.
    Missing keys and empty constraint dictionaries are treated by GraphQL
    resolvers as empty mappings. A constraint with neither key matches the entire
    original queryset when unioned, so permission code should only emit one when
    the rule intentionally grants access to every candidate row. For example,
    ``{}``, ``{"filter": {}}``, and ``{"exclude": {}}`` all match every
    candidate row for that one alternative. Mapping keys
    are Django-style lookup strings
    such as ``"owner_id"`` or ``"status__in"``, and mapping values are passed
    through to the queryset/search backend unchanged. Within one constraint,
    resolvers apply ``filter(**mapping)`` first and then ``exclude(**mapping)``.
    Multiple constraint entries are evaluated against the original queryset in
    list order and unioned, so they behave as alternatives rather than
    successive AND restrictions. Duplicate rows are handled by the bucket/queryset
    union implementation, not by ``ReadPermissionPlan`` itself; ordering is kept
    so backends with ordered union behavior can make deterministic choices.
    These dictionaries are intentionally mutable; callers should treat a
    constructed plan as request-scoped and avoid mutating it after handing it to
    GraphQL resolvers.

    An empty ``filters`` list means no prefilter restriction is applied by the
    plan. Resolvers start from the original queryset unless at least one
    constraint entry is present. Combined with ``requires_instance_check=False``,
    that means unrestricted read access for the candidate queryset. Combined with
    ``requires_instance_check=True``, resolvers use the full candidate queryset
    and then run per-object read checks for every row.

    ``requires_instance_check`` tells GraphQL resolvers whether they must still
    call per-object read authorization after applying the prefilters. Reasons are
    values from ``ReadPermissionReason``. ``"unfilterable_read_rule"`` means at
    least one read rule could not be converted into a backend prefilter.
    ``"based_on_class_context"`` means delegated permission logic required
    class-level context that cannot be represented as a row prefilter.
    ``"filter_key_conflict"`` means generated filter keys conflicted while
    composing permission filters. ``"no_prefilter_backend"`` means the interface
    backend cannot enforce read prefilters fully. Reason tuples are request
    diagnostics, not an exhaustive proof; their input order is not meaningful,
    and resolvers may sort/deduplicate them before logging. Plans with
    ``requires_instance_check=False`` should normally use an empty reasons tuple.
    Non-empty reasons in that state are diagnostic only and do not force an
    instance check by themselves.
    """

    filters: list[PermissionConstraint]
    requires_instance_check: bool = True
    instance_check_reasons: tuple[ReadPermissionReason, ...] = ()


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
        instance: PermissionDataManager[GeneralManager]
        | GeneralManager
        | GeneralManagerMeta,
        request_user: UserLike | object,
    ) -> None:
        """Initialise the permission context for a specific manager and user.

        ``request_user`` may be a Django user, ``AnonymousUser``,
        ``SimpleLazyObject``, or a primary-key-like value. It is normalized
        through :meth:`get_user_with_id`.
        """
        self._instance = instance
        self._request_user = self.get_user_with_id(request_user)

    @property
    def instance(
        self,
    ) -> PermissionDataManager[GeneralManager] | GeneralManager | GeneralManagerMeta:
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

    @abstractmethod
    def describe_operation_permissions(
        self,
        action: Literal["create", "read", "update", "delete"],
    ) -> tuple[str, ...]:
        """Return permission expressions associated with an action-level check."""

    @abstractmethod
    def check_operation_permission(
        self,
        action: Literal["create", "read", "update", "delete"],
    ) -> bool:
        """Return whether an action without attribute payload is allowed."""

    def can_read_instance(self) -> bool:
        """Return whether the current user may see that the instance exists."""
        if self._is_superuser():
            return True
        permission_plan = self.get_read_permission_plan()
        if not permission_plan.requires_instance_check:
            return True

        candidate_attributes: tuple[str, ...] = ()
        instance_attributes = getattr(self.instance, "_attributes", None)
        if isinstance(instance_attributes, Mapping):
            candidate_attributes = tuple(
                key for key in instance_attributes.keys() if not key.startswith("_")
            )
        elif isinstance(self.instance, PermissionDataManager):
            permission_payload = self.instance.permission_data
            permission_keys = (
                permission_payload.keys()
                if isinstance(permission_payload, Mapping)
                else ()
            )
            candidate_attributes = tuple(
                key
                for key in permission_keys
                if isinstance(key, str) and not key.startswith("_")
            )

        for attribute in candidate_attributes:
            if self.check_permission("read", attribute):
                return True

        raise NotImplementedError(
            "can_read_instance() requires an explicit implementation when no "
            "concrete readable attribute can be inferred from the permission context."
        )

    def _is_superuser(self) -> bool:
        """Return True when the current request user bypasses permission checks."""
        return bool(getattr(self.request_user, "is_superuser", False))

    @classmethod
    def check_create_permission(
        cls,
        data: dict[str, object],
        manager: type[GeneralManager],
        request_user: UserLike | object,
    ) -> None:
        """
        Validate that the requesting user is allowed to perform the create operation.

        Checks create permission for every key in `data` using the given `manager`.
        Empty payloads still evaluate the create-level permission gate once. If any
        attribute is not permitted, raises a PermissionCheckError that includes the
        evaluated user and a list of denial messages.

        Parameters:
            data: Mapping of attribute names to the values intended for creation.
            manager (type[GeneralManager]): Manager class that defines the model/schema against which permissions are checked.
            request_user: User instance or user id (will be resolved to a user or AnonymousUser).

        Raises:
            PermissionCheckError: If one or more attributes in `data` are denied for the resolved `request_user`.
        """
        request_user = cls.get_user_with_id(request_user)
        permission_data = PermissionDataManager(permission_data=data, manager=manager)
        Permission = cls(permission_data, request_user)
        manager_name = manager.__name__ if manager is not None else None
        if Permission._is_superuser():
            if audit_logging_enabled():
                if not data:
                    emit_permission_audit_event(
                        PermissionAuditEvent(
                            action="create",
                            attributes=(),
                            granted=True,
                            user=request_user,
                            manager=manager_name,
                            permissions=Permission.describe_operation_permissions(
                                "create"
                            ),
                            bypassed=True,
                        )
                    )
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
        if not data:
            is_allowed = Permission.check_operation_permission("create")
            if audit_logging_enabled():
                emit_permission_audit_event(
                    PermissionAuditEvent(
                        action="create",
                        attributes=(),
                        granted=is_allowed,
                        user=request_user,
                        manager=manager_name,
                        permissions=Permission.describe_operation_permissions("create"),
                    )
                )
            if not is_allowed:
                logger.info(
                    "permission denied",
                    context={
                        "manager": manager_name,
                        "action": "create",
                        "user_id": user_identifier,
                    },
                )
                errors.append("Create permission denied")
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
        data: dict[str, object],
        old_manager_instance: GeneralManager,
        request_user: UserLike | object,
    ) -> None:
        """
        Validate whether the request_user can perform the update operation.

        Checks update permission for every key in ``data`` against the existing
        manager instance. Empty payloads still evaluate the update-level
        permission gate once.

        Parameters:
            data: Mapping of attribute names to new values to be applied.
            old_manager_instance (GeneralManager): Existing manager instance whose current state is used to evaluate update permissions.
            request_user: User instance or user id; non-user values will be resolved to a User or AnonymousUser via get_user_with_id.

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
                if not data:
                    emit_permission_audit_event(
                        PermissionAuditEvent(
                            action="update",
                            attributes=(),
                            granted=True,
                            user=request_user,
                            manager=manager_name,
                            permissions=Permission.describe_operation_permissions(
                                "update"
                            ),
                            bypassed=True,
                        )
                    )
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
        if not data:
            is_allowed = Permission.check_operation_permission("update")
            if audit_logging_enabled():
                emit_permission_audit_event(
                    PermissionAuditEvent(
                        action="update",
                        attributes=(),
                        granted=is_allowed,
                        user=request_user,
                        manager=manager_name,
                        permissions=Permission.describe_operation_permissions("update"),
                    )
                )
            if not is_allowed:
                logger.info(
                    "permission denied",
                    context={
                        "manager": manager_name,
                        "action": "update",
                        "user_id": user_identifier,
                    },
                )
                errors.append("Update permission denied")
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
        request_user: UserLike | object,
    ) -> None:
        """
        Validate that the request_user has delete permission for every attribute of the given manager instance.

        This resolves the provided request_user to a User/AnonymousUser, evaluates delete permission for each attribute present on manager_instance, collects any denied attributes into error messages, and raises PermissionCheckError if any permissions are denied.

        Parameters:
            manager_instance (GeneralManager): The manager object whose attributes will be checked for delete permission.
            request_user: The user (or user id) to evaluate; non-user values will be resolved to AnonymousUser.

        Raises:
            PermissionCheckError: If one or more attributes are not permitted for deletion by request_user. The exception carries the user and the list of denial messages.
        """
        request_user = cls.get_user_with_id(request_user)
        permission_data = PermissionDataManager(manager_instance)
        Permission = cls(permission_data, request_user)
        manager_name = manager_instance.__class__.__name__
        permission_attributes = cls._iter_permission_attributes(manager_instance)
        if Permission._is_superuser():
            if audit_logging_enabled():
                for key in permission_attributes:
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
        for key in permission_attributes:
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
        user: UserLike | object,
    ) -> UserLike:
        """
        Resolve a user identifier or user-like object to a Django User or AnonymousUser instance.

        If the input is already an AbstractBaseUser, AnonymousUser, or configured
        user-model instance, it is returned unchanged. If the input is a primary
        key (or other value used to look up a User by id), the corresponding User
        is returned; if no such User exists, an AnonymousUser is returned.

        Parameters:
            user: A user object or a value to look up a User by primary key.

        Returns:
            UserLike: The resolved User instance, or an AnonymousUser when no matching User is found.
        """
        from django.contrib.auth import get_user_model

        if isinstance(user, SimpleLazyObject):
            wrapped = getattr(user, "_wrapped", empty)
            if wrapped is empty:
                setup = cast(
                    Callable[[], None], object.__getattribute__(user, "_setup")
                )
                setup()
                wrapped = getattr(user, "_wrapped", empty)
            user = wrapped

        User = get_user_model()
        if isinstance(user, (AbstractBaseUser, AnonymousUser, User)):
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
    ) -> list[PermissionConstraint]:
        """Return the filter/exclude constraints associated with this permission."""
        raise NotImplementedError

    def get_read_permission_plan(self) -> ReadPermissionPlan:
        """Return read-query prefilters plus whether instance checks must still run."""
        return ReadPermissionPlan(
            filters=self.get_permission_filter(),
            requires_instance_check=True,
            instance_check_reasons=("no_prefilter_backend",),
        )

    @staticmethod
    def _iter_permission_attributes(
        manager_instance: GeneralManager,
    ) -> tuple[str, ...]:
        """Return stable public/domain attributes for permission and audit iteration."""
        attributes = getattr(manager_instance, "_attributes", None)
        if isinstance(attributes, Mapping):
            return tuple(attributes.keys())
        return tuple(
            key for key in manager_instance.__dict__.keys() if not key.startswith("_")
        )

    def _get_permission_filter(self, permission: str) -> PermissionConstraint:
        """
        Resolve the filter/exclude constraints associated with a permission expression.

        Parameters:
            permission (str): Permission expression of the form "<function_name>[:config,...]"; the leading name selects a permission function and the optional colon-separated values are passed as configuration.

        Returns:
            PermissionConstraint: A mapping with optional ``"filter"`` and
            ``"exclude"`` dictionaries whose lookup values are typed as
            ``object``. Superusers and unfilterable permissions that return
            ``None`` both resolve to empty ``filter`` and ``exclude`` mappings.

        Raises:
            PermissionNotFoundError: If no permission function matches the leading name in `permission`.
        """
        if self._is_superuser():
            return {"filter": {}, "exclude": {}}
        permission_function, *config = permission.split(":")
        if permission_function not in permission_functions:
            raise PermissionNotFoundError(permission)
        permission_filter = cast(
            PermissionConstraint | None,
            permission_functions[permission_function]["permission_filter"](
                self.request_user, config
            ),
        )
        if permission_filter is None:
            return {"filter": {}, "exclude": {}}
        return permission_filter

    def _get_permission_filter_info(
        self, permission: str
    ) -> tuple[PermissionConstraint, bool]:
        """
        Resolve filter/exclude constraints and whether the permission is query-filterable.

        Superusers return empty filter/exclude mappings marked filterable. When
        a permission filter returns ``None``, empty mappings are returned with
        ``False`` to signal that callers must keep a row-level instance check.

        Returns:
            ``(constraint, is_filterable)``.

        Raises:
            PermissionNotFoundError: If no registered permission function
                matches the leading permission name.
        """
        if self._is_superuser():
            return {"filter": {}, "exclude": {}}, True
        permission_function, *config = permission.split(":")
        if permission_function not in permission_functions:
            raise PermissionNotFoundError(permission)
        permission_filter = cast(
            PermissionConstraint | None,
            permission_functions[permission_function]["permission_filter"](
                self.request_user, config
            ),
        )
        if permission_filter is None:
            return {"filter": {}, "exclude": {}}, False
        return permission_filter, True

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
