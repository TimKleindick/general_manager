"""Registry of reusable permission checks and their queryset filters."""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING, TypedDict, Literal

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser, AnonymousUser
    from general_manager.permission.permission_data_manager import (
        PermissionDataManager,
    )
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta


type permission_filter = Callable[
    [AbstractUser | AnonymousUser, list[str]],
    dict[Literal["filter", "exclude"], dict[str, Any]] | None,
]

type permission_method = Callable[
    [
        PermissionDataManager | GeneralManager | GeneralManagerMeta,
        AbstractUser | AnonymousUser,
        list[str],
    ],
    bool,
]


class PermissionDict(TypedDict):
    """Typed dictionary describing a registered permission function."""

    permission_method: permission_method
    permission_filter: permission_filter


def _related_user_field_permission_method(
    instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    user: AbstractUser | AnonymousUser,
    config: list[str],
) -> bool:
    if not config:
        return False
    related_object = getattr(instance, config[0], None)
    return bool(related_object == user)  # type: ignore[arg-type]


def _related_user_field_permission_filter(
    user: AbstractUser | AnonymousUser, config: list[str]
) -> dict[Literal["filter", "exclude"], dict[str, Any]] | None:
    if not config:
        return None
    user_id = getattr(user, "id", None)
    if user_id is None:
        return None
    return {"filter": {f"{config[0]}_id": user_id}}


def _many_to_many_contains_user_permission_method(
    instance: PermissionDataManager | GeneralManager | GeneralManagerMeta,
    user: AbstractUser | AnonymousUser,
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


def _many_to_many_contains_user_permission_filter(
    user: AbstractUser | AnonymousUser, config: list[str]
) -> dict[Literal["filter", "exclude"], dict[str, Any]] | None:
    if not config:
        return None
    user_id = getattr(user, "id", None)
    if user_id is None:
        return None
    return {"filter": {f"{config[0]}__id": user_id}}


permission_functions: dict[str, PermissionDict] = {
    "public": {
        "permission_method": lambda _instance, _user, _config: True,
        "permission_filter": lambda _user, _config: None,
    },
    "matches": {
        "permission_method": lambda instance, _user, config: bool(
            len(config) >= 2 and getattr(instance, config[0]) == config[1]
        ),
        "permission_filter": lambda _user, config: (
            {"filter": {config[0]: config[1]}} if len(config) >= 2 else None
        ),
    },
    "isAdmin": {
        "permission_method": lambda _instance, user, _config: user.is_staff,
        "permission_filter": lambda _user, _config: None,
    },
    "isSelf": {
        "permission_method": lambda instance, user, _config: instance.creator == user,  # type: ignore
        "permission_filter": lambda user, _config: {"filter": {"creator_id": user.id}},  # type: ignore
    },
    "isAuthenticated": {
        "permission_method": lambda _instance, user, _config: user.is_authenticated,
        "permission_filter": lambda _user, _config: None,
    },
    "isActive": {
        "permission_method": lambda _instance, user, _config: user.is_active,
        "permission_filter": lambda _user, _config: None,
    },
    "hasPermission": {
        "permission_method": lambda _instance, user, config: bool(
            config and user.has_perm(config[0])
        ),
        "permission_filter": lambda _user, _config: None,
    },
    "inGroup": {
        "permission_method": lambda _instance, user, config: bool(
            config
            and hasattr(user, "groups")
            and user.groups.filter(name=config[0]).exists()
        ),
        "permission_filter": lambda _user, _config: None,
    },
    "relatedUserField": {
        "permission_method": _related_user_field_permission_method,
        "permission_filter": _related_user_field_permission_filter,
    },
    "manyToManyContainsUser": {
        "permission_method": _many_to_many_contains_user_permission_method,
        "permission_filter": _many_to_many_contains_user_permission_filter,
    },
}
