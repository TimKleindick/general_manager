"""Registry of reusable permission checks and their queryset filters."""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING, TypedDict, Literal

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
    from general_manager.permission.permission_data_manager import (
        PermissionDataManager,
    )
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta

type permission_filter = Callable[
    [AbstractBaseUser | AnonymousUser, list[str]],
    dict[Literal["filter", "exclude"], dict[str, Any]] | None,
]

type permission_method = Callable[
    [
        PermissionDataManager | GeneralManager | GeneralManagerMeta,
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


def _default_permission_filter(
    _user: AbstractBaseUser | AnonymousUser, _config: list[str]
) -> dict[Literal["filter", "exclude"], dict[str, Any]] | None:
    return None


def register_permission(
    name: str, *, permission_filter: permission_filter | None = None
) -> Callable[[permission_method], permission_method]:
    """
    Register a permission function in the global registry.

    Parameters:
        name (str): Identifier used in permission expressions.
        permission_filter (permission_filter | None): Optional callable that
            provides queryset filters corresponding to the permission.

    Returns:
        Callable[[permission_method], permission_method]: Decorator that
        registers the decorated function and returns it unchanged.

    Raises:
        ValueError: If another permission with the same name has already been
            registered.
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
    _instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    _user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> bool:
    return True


def _matches_permission_filter(
    _user: AbstractBaseUser | AnonymousUser, config: list[str]
) -> dict[Literal["filter", "exclude"], dict[str, Any]] | None:
    if len(config) < 2:
        return None
    return {"filter": {config[0]: config[1]}}


@register_permission("matches", permission_filter=_matches_permission_filter)
def _permission_matches(
    instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    _user: AbstractBaseUser | AnonymousUser,
    config: list[str],
) -> bool:
    return bool(
        len(config) >= 2 and str(getattr(instance, config[0], None)) == config[1]
    )


@register_permission("isAdmin")
def _permission_is_admin(
    _instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> bool:
    return bool(getattr(user, "is_staff", False))


def _is_self_permission_filter(
    user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> dict[Literal["filter", "exclude"], dict[str, Any]] | None:
    return {"filter": {"creator_id": getattr(user, "id", None)}}


@register_permission("isSelf", permission_filter=_is_self_permission_filter)
def _permission_is_self(
    instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> bool:
    return bool(instance.creator == user)  # type: ignore[union-attr]


@register_permission("isAuthenticated")
def _permission_is_authenticated(
    _instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> bool:
    return bool(getattr(user, "is_authenticated", False))


@register_permission("isActive")
def _permission_is_active(
    _instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    user: AbstractBaseUser | AnonymousUser,
    _config: list[str],
) -> bool:
    return bool(getattr(user, "is_active", False))


@register_permission("hasPermission")
def _permission_has_permission(
    _instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    user: AbstractBaseUser | AnonymousUser,
    config: list[str],
) -> bool:
    if not config:
        return False
    has_perm = getattr(user, "has_perm", None)
    if not callable(has_perm):
        return False
    return bool(has_perm(config[0]))


@register_permission("inGroup")
def _permission_in_group(
    _instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    user: AbstractBaseUser | AnonymousUser,
    config: list[str],
) -> bool:
    if not config:
        return False
    group_manager = getattr(user, "groups", None)
    if group_manager is None or not hasattr(group_manager, "filter"):
        return False
    filtered = group_manager.filter(name=config[0])  # type: ignore[attr-defined]
    return bool(hasattr(filtered, "exists") and filtered.exists())  # type: ignore[call-arg]


def _related_user_field_permission_filter(
    user: AbstractBaseUser | AnonymousUser, config: list[str]
) -> dict[Literal["filter", "exclude"], dict[str, Any]] | None:
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
    instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    user: AbstractBaseUser | AnonymousUser,
    config: list[str],
) -> bool:
    if not config:
        return False
    related_object = getattr(instance, config[0], None)
    return bool(related_object == user)  # type: ignore[arg-type]


def _many_to_many_contains_user_permission_filter(
    user: AbstractBaseUser | AnonymousUser, config: list[str]
) -> dict[Literal["filter", "exclude"], dict[str, Any]] | None:
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
    instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    user: AbstractBaseUser | AnonymousUser,
    config: list[str],
) -> bool:
    if not config:
        return False
    related_manager = getattr(instance, config[0], None)
    if related_manager is None or not hasattr(related_manager, "filter"):
        return False
    user_pk = getattr(user, "pk", None)
    if user_pk is None:
        return False
    filtered = related_manager.filter(pk=user_pk)  # type: ignore[attr-defined]
    return bool(hasattr(filtered, "exists") and filtered.exists())  # type: ignore[call-arg]
