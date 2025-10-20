"""Registry of reusable permission checks and their queryset filters."""

from typing import Any, Callable, TYPE_CHECKING, TypedDict, Literal

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser, AnonymousUser
    from general_manager.permission.permissionDataManager import (
        PermissionDataManager,
    )
    from general_manager.manager.generalManager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta


type permission_filter = Callable[
    [AbstractUser | AnonymousUser, list[str]],
    dict[Literal["filter", "exclude"], dict[str, str]] | None,
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


permission_functions: dict[str, PermissionDict] = {
    "public": {
        "permission_method": lambda _instance, _user, _config: True,
        "permission_filter": lambda _user, _config: None,
    },
    "matches": {
        "permission_method": lambda instance, _user, config: getattr(
            instance, config[0]
        )
        == config[1],
        "permission_filter": lambda _user, config: {"filter": {config[0]: config[1]}},
    },
    "ends_with": {
        "permission_method": lambda instance, _user, config: getattr(
            instance, config[0]
        ).endswith(config[1]),
        "permission_filter": lambda _user, config: {
            "filter": {f"{config[0]}__endswith": config[1]}
        },
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
}
