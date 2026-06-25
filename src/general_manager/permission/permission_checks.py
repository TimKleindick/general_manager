"""Registry of reusable permission checks and their queryset filters."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

    from general_manager.manager.general_manager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta
    from general_manager.permission.permission_data_manager import (
        PermissionDataManager,
    )

type PermissionFilterAction = Literal["filter", "exclude"]
type PermissionFilter = dict[PermissionFilterAction, dict[str, object]]
type PermissionSubject = (
    PermissionDataManager[GeneralManager] | GeneralManager | GeneralManagerMeta
)
type permission_filter = Callable[
    [AbstractBaseUser | AnonymousUser, list[str]],
    PermissionFilter | None,
]

type permission_method = Callable[
    [
        PermissionSubject,
        AbstractBaseUser | AnonymousUser,
        list[str],
    ],
    bool,
]


class PermissionDict(TypedDict):
    """Typed dictionary describing a registered permission function."""

    permission_method: permission_method
    permission_filter: permission_filter


permission_functions: dict[str, PermissionDict] = {}

__all__ = ["permission_functions", "register_permission"]

_PERMISSION_ALREADY_REGISTERED_MESSAGE = "Permission function is already registered."


class _PermissionCheckCallable(Protocol):
    """Callable shape used by Django user objects for object permissions."""

    def __call__(self, permission: str) -> object: ...


class _FilterableRelation(Protocol):
    """Relation manager shape required by group and many-to-many checks."""

    def filter(self, **lookups: object) -> object: ...


class _ExistsCallable(Protocol):
    """Callable shape for Django query-like ``exists`` methods."""

    def __call__(self) -> object: ...


def _relation_filter(relation: object, **lookups: object) -> object | None:
    """Call ``relation.filter`` when the object exposes a callable filter method."""
    filter_method = getattr(relation, "filter", None)
    if not callable(filter_method):
        return None
    return cast(_FilterableRelation, relation).filter(**lookups)


def _filter_result_exists(result: object | None) -> bool:
    """Return whether a dynamic relation filter result exposes ``exists()`` truthily."""
    exists = getattr(result, "exists", None)
    if not callable(exists):
        return False
    return bool(cast(_ExistsCallable, exists)())


def _default_permission_filter(
    _user: AbstractBaseUser | AnonymousUser, _config: list[str]
) -> PermissionFilter | None:
    """Return no queryset constraint for permissions that are only instance checks."""
    return None


def register_permission(
    name: str, *, permission_filter: permission_filter | None = None
) -> Callable[[permission_method], permission_method]:
    """
    Register a permission expression keyword in the global registry.

    The decorated function receives the object being checked, the resolved
    request user, and the colon-separated configuration values from permission
    strings such as ``"belongsToCustomer:customer"``. It must return ``True``
    to grant access and ``False`` to deny access. Applying the decorator stores
    that function in ``permission_functions`` under ``name`` and returns the
    original function unchanged.

    ``name`` is stored exactly as provided in the global registry. Permission
    expression parsers split strings on ``&`` and ``:`` without escaping, so
    colon-free names are the practical form for rules referenced from
    permission strings. Empty config segments are preserved by normal string
    splitting: ``"rule:"`` passes ``[""]`` and ``"rule::x"`` passes
    ``["", "x"]``.

    When ``permission_filter`` is provided, read-query paths call it with the
    same user/config pair to build Django-style ``{"filter": {...}}`` and/or
    ``{"exclude": {...}}`` constraints. Return ``None`` when a permission
    cannot be represented as a queryset prefilter and must be evaluated per
    instance. Registry entries always store a callable ``permission_filter``;
    permissions registered without one receive a default callable returning
    ``None``. Django queryset authorization applies filter kwargs before
    exclude kwargs. Search backends receive only the filter side as a prefilter
    and the final instance gate checks exclude constraints.

    ``permission_functions`` is an ordinary process-local mutable dictionary.
    Direct mutation affects all later permission checks in the process; tests
    may snapshot and restore it, while application code should prefer this
    decorator. Permission methods and filters are called without wrapping their
    exceptions, so errors raised by custom callables propagate to the caller.

    Parameters:
        name (str): Identifier used before the first colon in permission
            expressions.
        permission_filter (permission_filter | None): Optional callable that
            returns queryset constraints corresponding to the permission.

    Returns:
        Callable[[permission_method], permission_method]: Decorator that
        registers the decorated function and returns it unchanged.

    Raises:
        ValueError: If applying the decorator would register a name already
            present in the global registry.
    """

    def decorator(func: permission_method) -> permission_method:
        if name in permission_functions:
            raise ValueError(_PERMISSION_ALREADY_REGISTERED_MESSAGE)
        filter_callable = permission_filter or _default_permission_filter
        permission_functions[name] = {
            "permission_method": func,
            "permission_filter": filter_callable,
        }
        return func

    return decorator


@register_permission("public")
def _permission_public(
    _instance: PermissionSubject,
    _user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> bool:
    """Allow any user, including anonymous and inactive users."""
    return True


def _matches_permission_filter(
    _user: AbstractBaseUser | AnonymousUser, config: list[str]
) -> PermissionFilter | None:
    """Convert ``matches:<field>:<value>`` into an equality queryset filter."""
    if len(config) < 2:
        return None
    return {"filter": {config[0]: config[1]}}


@register_permission("matches", permission_filter=_matches_permission_filter)
def _permission_matches(
    instance: PermissionSubject,
    _user: AbstractBaseUser | AnonymousUser,
    config: list[str],
) -> bool:
    """Allow access when the configured instance attribute stringifies to a value."""
    return bool(
        len(config) >= 2 and str(getattr(instance, config[0], None)) == config[1]
    )


@register_permission("isAdmin")
def _permission_is_admin(
    _instance: PermissionSubject,
    user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> bool:
    """Allow staff users, including Django superusers."""
    return bool(getattr(user, "is_staff", False))


def _is_self_permission_filter(
    user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> PermissionFilter | None:
    """Constrain querysets to rows whose ``creator_id`` matches the user id."""
    return {"filter": {"creator_id": getattr(user, "id", None)}}


@register_permission("isSelf", permission_filter=_is_self_permission_filter)
def _permission_is_self(
    instance: PermissionSubject,
    user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> bool:
    """Allow access when the instance ``creator`` is the resolved user."""
    return bool(getattr(instance, "creator", None) == user)


@register_permission("isAuthenticated")
def _permission_is_authenticated(
    _instance: PermissionSubject,
    user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> bool:
    """Allow users whose Django authentication flag is truthy."""
    return bool(getattr(user, "is_authenticated", False))


@register_permission("isActive")
def _permission_is_active(
    _instance: PermissionSubject,
    user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> bool:
    """Allow users whose Django active flag is truthy."""
    return bool(getattr(user, "is_active", False))


@register_permission("hasPermission")
def _permission_has_permission(
    _instance: PermissionSubject,
    user: AbstractBaseUser | AnonymousUser,
    config: list[str],
) -> bool:
    """Allow users for whom ``user.has_perm(config[0])`` grants access."""
    if not config:
        return False
    has_perm: object = getattr(user, "has_perm", None)
    if not callable(has_perm):
        return False
    return bool(cast(_PermissionCheckCallable, has_perm)(config[0]))


@register_permission("inGroup")
def _permission_in_group(
    _instance: PermissionSubject,
    user: AbstractBaseUser | AnonymousUser,
    config: list[str],
) -> bool:
    """Allow users whose Django ``groups`` relation contains the configured name."""
    if not config:
        return False
    filtered = _relation_filter(getattr(user, "groups", None), name=config[0])
    return _filter_result_exists(filtered)


def _related_user_field_permission_filter(
    user: AbstractBaseUser | AnonymousUser, config: list[str]
) -> PermissionFilter | None:
    """Constrain querysets to rows whose configured foreign key references the user."""
    if not config:
        return None
    user_id = getattr(user, "id", None)
    if user_id is None:
        return None
    return {"filter": {f"{config[0]}_id": user_id}}


@register_permission(
    "relatedUserField",
    permission_filter=_related_user_field_permission_filter,
)
def _permission_related_user_field(
    instance: PermissionSubject,
    user: AbstractBaseUser | AnonymousUser,
    config: list[str],
) -> bool:
    """Allow access when a configured related-object field equals the user."""
    if not config:
        return False
    related_object = getattr(instance, config[0], None)
    return bool(related_object == user)


def _many_to_many_contains_user_permission_filter(
    user: AbstractBaseUser | AnonymousUser, config: list[str]
) -> PermissionFilter | None:
    """Constrain querysets to rows whose configured many-to-many relation contains the user."""
    if not config:
        return None
    user_id = getattr(user, "id", None)
    if user_id is None:
        return None
    return {"filter": {f"{config[0]}__id": user_id}}


@register_permission(
    "manyToManyContainsUser",
    permission_filter=_many_to_many_contains_user_permission_filter,
)
def _permission_many_to_many_contains_user(
    instance: PermissionSubject,
    user: AbstractBaseUser | AnonymousUser,
    config: list[str],
) -> bool:
    """Allow access when a configured many-to-many relation contains the user."""
    if not config:
        return False
    related_manager = getattr(instance, config[0], None)
    user_pk = getattr(user, "pk", None)
    if user_pk is None:
        return False
    filtered = _relation_filter(related_manager, pk=user_pk)
    return _filter_result_exists(filtered)
