"""Default permission implementation leveraging manager configuration."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Literal, Optional, Dict, ClassVar
from collections.abc import Mapping

from django.conf import settings
from general_manager.permission.base_permission import BasePermission, UserLike

if TYPE_CHECKING:
    from general_manager.permission.permission_data_manager import (
        PermissionDataManager,
    )
    from general_manager.manager.general_manager import GeneralManager

type permission_type = Literal[
    "create",
    "read",
    "update",
    "delete",
]

_SETTINGS_KEY = "GENERAL_MANAGER"
_DEFAULT_PERMISSIONS_KEY = "DEFAULT_PERMISSIONS"
_PERMISSION_ACTIONS: tuple[permission_type, ...] = (
    "read",
    "create",
    "update",
    "delete",
)
_FALLBACK_DEFAULT_PERMISSIONS: dict[permission_type, list[str]] = {
    "read": ["public"],
    "create": ["isAuthenticated"],
    "update": ["isAuthenticated"],
    "delete": ["isAuthenticated"],
}


def _get_default_permissions() -> dict[permission_type, list[str]]:
    """Return configured default CRUD permissions, falling back when absent."""
    config = getattr(settings, _SETTINGS_KEY, None)
    configured_defaults: Mapping[str, Any] | None = None
    if isinstance(config, Mapping):
        raw_defaults = config.get(_DEFAULT_PERMISSIONS_KEY)
        if isinstance(raw_defaults, Mapping):
            configured_defaults = raw_defaults

    defaults = {
        action: list(permissions)
        for action, permissions in _FALLBACK_DEFAULT_PERMISSIONS.items()
    }
    if configured_defaults is None:
        return defaults

    for action in _PERMISSION_ACTIONS:
        configured_permissions = configured_defaults.get(action.upper())
        if configured_permissions is None:
            configured_permissions = configured_defaults.get(action)
        if configured_permissions is not None:
            defaults[action] = list(configured_permissions)
    return defaults


class InvalidBasedOnConfigurationError(ValueError):
    """Raised when the configured `__based_on__` attribute is missing or invalid."""

    def __init__(self, attribute_name: str) -> None:
        """
        Initialize the exception for an invalid or missing based-on configuration attribute.

        Parameters:
            attribute_name (str): Name of the configured `__based_on__` attribute that is missing or invalid.
        """
        super().__init__(
            f"Based on configuration '{attribute_name}' is not valid or does not exist."
        )


class InvalidBasedOnTypeError(TypeError):
    """Raised when the `__based_on__` attribute does not resolve to a GeneralManager."""

    def __init__(self, attribute_name: str) -> None:
        """
        Initialize the exception indicating that the configured based-on attribute does not resolve to a GeneralManager.

        Parameters:
            attribute_name (str): Name of the configured based-on attribute that failed type validation; included in the exception message.
        """
        super().__init__(f"Based on object {attribute_name} is not a GeneralManager.")


class UnknownPermissionActionError(ValueError):
    """Raised when an unsupported permission action is encountered."""

    def __init__(self, action: str) -> None:
        """
        Initialize the exception for an unsupported permission action.

        Parameters:
            action (str): The permission action name that is not recognized; used to build the exception message "Action {action} not found."
        """
        super().__init__(f"Action {action} not found.")


class notExistent:
    pass


class ManagerBasedPermission(BasePermission):
    """Permission implementation driven by class-level configuration lists."""

    __based_on__: ClassVar[Optional[str]] = None
    __read__: ClassVar[list[str]] = _FALLBACK_DEFAULT_PERMISSIONS["read"]
    __create__: ClassVar[list[str]] = _FALLBACK_DEFAULT_PERMISSIONS["create"]
    __update__: ClassVar[list[str]] = _FALLBACK_DEFAULT_PERMISSIONS["update"]
    __delete__: ClassVar[list[str]] = _FALLBACK_DEFAULT_PERMISSIONS["delete"]
    _explicit_permission_attrs: ClassVar[frozenset[str]] = frozenset(
        {"__read__", "__create__", "__update__", "__delete__"},
    )
    _read_permissions: list[str]
    _create_permissions: list[str]
    _update_permissions: list[str]
    _delete_permissions: list[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Initialize per-subclass CRUD defaults once at class creation time."""
        super().__init_subclass__(**kwargs)

        cls._explicit_permission_attrs = frozenset(
            name
            for name in ("__read__", "__create__", "__update__", "__delete__")
            if name in cls.__dict__
        )

        default_permissions = _get_default_permissions()
        default_read = default_permissions["read"]
        default_write_create = default_permissions["create"]
        default_write_update = default_permissions["update"]
        default_write_delete = default_permissions["delete"]
        if cls.__based_on__ is not None:
            default_read = []
            default_write_create = []
            default_write_update = []
            default_write_delete = []

        if "__read__" not in cls.__dict__:
            cls.__read__ = list(default_read)
        if "__create__" not in cls.__dict__:
            cls.__create__ = list(default_write_create)
        if "__update__" not in cls.__dict__:
            cls.__update__ = list(default_write_update)
        if "__delete__" not in cls.__dict__:
            cls.__delete__ = list(default_write_delete)

    def __init__(
        self,
        instance: PermissionDataManager | GeneralManager,
        request_user: UserLike,
    ) -> None:
        """
        Initialise the permission object and gather default and attribute-level rules.

        Parameters:
            instance (PermissionDataManager | GeneralManager): Target data used for permission evaluation.
            request_user (UserLike): User whose permissions are being checked.
        """
        super().__init__(instance, request_user)
        if self.__class__ is ManagerBasedPermission:
            default_permissions = _get_default_permissions()
            self._set_effective_permissions(
                read_permissions=default_permissions["read"],
                create_permissions=default_permissions["create"],
                update_permissions=default_permissions["update"],
                delete_permissions=default_permissions["delete"],
            )
        else:
            self._set_effective_permissions(
                read_permissions=self.__class__.__read__,
                create_permissions=self.__class__.__create__,
                update_permissions=self.__class__.__update__,
                delete_permissions=self.__class__.__delete__,
            )

        self.__attribute_permissions = self.__get_attribute_permissions()
        self.__based_on_permission = self.__get_based_on_permission()
        self.__overall_results: Dict[permission_type, Optional[bool]] = {
            "create": None,
            "read": None,
            "update": None,
            "delete": None,
        }

    def __get_based_on_permission(self) -> Optional[BasePermission]:
        """
        Resolve and return a BasePermission instance from the manager attribute named by the class-level `__based_on__` configuration.

        If `__based_on__` is None or not configured on this class, returns None. If the referenced attribute exists on the target instance but is None, returns None. If the referenced attribute resolves to a manager that exposes a valid `Permission` subclass, constructs and returns that permission with the corresponding manager instance and the current request user.

        Returns:
            BasePermission | None: The resolved permission instance for the related manager, or `None` when no based-on permission applies.

        Raises:
            InvalidBasedOnConfigurationError: If the configured `__based_on__` attribute does not exist on the target instance.
            InvalidBasedOnTypeError: If the configured attribute exists but does not resolve to a `GeneralManager` or subclass.
        """
        from general_manager.manager.general_manager import GeneralManager

        __based_on__ = self.__based_on__
        if __based_on__ is None:
            return None

        basis_object = getattr(self.instance, __based_on__, notExistent)
        if basis_object is notExistent:
            raise InvalidBasedOnConfigurationError(__based_on__)
        if basis_object is None:
            default_permissions = _get_default_permissions()
            explicit_permission_attrs = self.__class__._explicit_permission_attrs
            if "__read__" not in explicit_permission_attrs:
                self._read_permissions = list(default_permissions["read"])
                self.__dict__["__read__"] = list(default_permissions["read"])
            if "__create__" not in explicit_permission_attrs:
                self._create_permissions = list(default_permissions["create"])
                self.__dict__["__create__"] = list(default_permissions["create"])
            if "__update__" not in explicit_permission_attrs:
                self._update_permissions = list(default_permissions["update"])
                self.__dict__["__update__"] = list(default_permissions["update"])
            if "__delete__" not in explicit_permission_attrs:
                self._delete_permissions = list(default_permissions["delete"])
                self.__dict__["__delete__"] = list(default_permissions["delete"])
            return None
        if not isinstance(basis_object, GeneralManager) and not (
            isinstance(basis_object, type) and issubclass(basis_object, GeneralManager)
        ):
            raise InvalidBasedOnTypeError(__based_on__)

        Permission = getattr(basis_object, "Permission", None)

        if Permission is None or not issubclass(
            Permission,
            BasePermission,
        ):
            return None

        return Permission(
            instance=getattr(self.instance, __based_on__),
            request_user=self.request_user,
        )

    def _set_effective_permissions(
        self,
        *,
        read_permissions: list[str],
        create_permissions: list[str],
        update_permissions: list[str],
        delete_permissions: list[str],
    ) -> None:
        """Store the effective CRUD permissions for this instance."""
        self._read_permissions = list(read_permissions)
        self._create_permissions = list(create_permissions)
        self._update_permissions = list(update_permissions)
        self._delete_permissions = list(delete_permissions)
        self.__dict__["__read__"] = list(read_permissions)
        self.__dict__["__create__"] = list(create_permissions)
        self.__dict__["__update__"] = list(update_permissions)
        self.__dict__["__delete__"] = list(delete_permissions)

    def __get_attribute_permissions(
        self,
    ) -> dict[str, dict[permission_type, list[str]]]:
        """Collect attribute-level permission overrides defined on the class."""
        attribute_permissions = {}
        for attribute in self.__class__.__dict__:
            if not attribute.startswith("__"):
                attribute_permissions[attribute] = getattr(self, attribute)
        return attribute_permissions

    def check_permission(
        self,
        action: permission_type,
        attribute: str,
    ) -> bool:
        """
        Determine whether the request user is allowed to perform a CRUD action on a specific attribute.

        Parameters:
            action (permission_type): CRUD action to evaluate ("create", "read", "update", "delete").
            attribute (str): Name of the attribute to check permission for.

        Returns:
            bool: True if the action is permitted on the attribute, False otherwise.

        Raises:
            UnknownPermissionActionError: If `action` is not one of "create", "read", "update", or "delete".
        """
        if self._is_superuser():
            self.__overall_results[action] = True
            return True
        if (
            self.__based_on_permission
            and not self.__based_on_permission.check_permission(action, attribute)
        ):
            return False

        if action == "create":
            permissions = self._create_permissions
        elif action == "read":
            permissions = self._read_permissions
        elif action == "update":
            permissions = self._update_permissions
        elif action == "delete":
            permissions = self._delete_permissions
        else:
            raise UnknownPermissionActionError(action)

        has_attribute_permissions = (
            attribute in self.__attribute_permissions
            and action in self.__attribute_permissions[attribute]
        )

        if not has_attribute_permissions:
            last_result = self.__overall_results.get(action)
            if last_result is not None:
                return last_result
            attribute_permission = True
        else:
            attribute_permission = self.__check_specific_permission(
                self.__attribute_permissions[attribute][action]
            )

        permission = self.__check_specific_permission(permissions)
        self.__overall_results[action] = permission
        return permission and attribute_permission

    def __check_specific_permission(
        self,
        permissions: list[str],
    ) -> bool:
        """Return True if any permission expression in the list evaluates to True."""
        if not permissions:
            return True
        for permission in permissions:
            if self.validate_permission_string(permission):
                return True
        return False

    def get_permission_filter(
        self,
    ) -> list[dict[Literal["filter", "exclude"], dict[str, str]]]:
        """
        Builds queryset filter and exclude mappings derived from this permission configuration.

        If a based-on permission exists, its filters and excludes are included with each key prefixed by the name in __based_on__. Then appends filters produced from this class's read permissions via _get_permission_filter.

        Returns:
            list[dict[Literal["filter", "exclude"], dict[str, str]]]: A list of dictionaries each containing "filter" and "exclude" mappings where keys are queryset lookups and values are lookup values.
        """
        if self._is_superuser():
            return [{"filter": {}, "exclude": {}}]
        __based_on__ = self.__based_on__
        filters: list[dict[Literal["filter", "exclude"], dict[str, str]]] = []

        if self.__based_on_permission is not None:
            base_permissions = self.__based_on_permission.get_permission_filter()
            for base_permission in base_permissions:
                filter = base_permission.get("filter", {})
                exclude = base_permission.get("exclude", {})
                filters.append(
                    {
                        "filter": {
                            f"{__based_on__}__{key}": value
                            for key, value in filter.items()
                        },
                        "exclude": {
                            f"{__based_on__}__{key}": value
                            for key, value in exclude.items()
                        },
                    }
                )

        for permission in self._read_permissions:
            filters.append(self._get_permission_filter(permission))

        return filters

    def describe_permissions(
        self,
        action: permission_type,
        attribute: str,
    ) -> tuple[str, ...]:
        """Return permission expressions considered for the given action/attribute."""
        if action == "create":
            base_permissions: tuple[str, ...] = tuple(self._create_permissions)
        elif action == "read":
            base_permissions = tuple(self._read_permissions)
        elif action == "update":
            base_permissions = tuple(self._update_permissions)
        elif action == "delete":
            base_permissions = tuple(self._delete_permissions)
        else:
            raise UnknownPermissionActionError(action)

        attribute_source = self.__attribute_permissions.get(attribute)
        if isinstance(attribute_source, dict):
            attribute_permissions = tuple(attribute_source.get(action, []))
        else:
            attribute_permissions = tuple()
        combined = base_permissions + attribute_permissions
        if self.__based_on_permission is not None:
            combined += self.__based_on_permission.describe_permissions(
                action, attribute
            )
        return combined
