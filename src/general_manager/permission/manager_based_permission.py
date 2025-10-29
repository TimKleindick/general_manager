"""Default permission implementation leveraging manager configuration."""

from __future__ import annotations
from typing import TYPE_CHECKING, Literal, Optional, Dict
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

    __based_on__: Optional[str] = None
    __read__: list[str]
    __create__: list[str]
    __update__: list[str]
    __delete__: list[str]

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
        self.__set_permissions()

        self.__attribute_permissions = self.__get_attribute_permissions()
        self.__based_on_permission = self.__get_based_on_permission()
        self.__overall_results: Dict[permission_type, Optional[bool]] = {
            "create": None,
            "read": None,
            "update": None,
            "delete": None,
        }

    def __set_permissions(self, skip_based_on: bool = False) -> None:
        """Populate CRUD permissions using class-level defaults and overrides."""
        default_read = ["public"]
        default_write = ["isAuthenticated"]

        if self.__based_on__ is not None and not skip_based_on:
            default_read = []
            default_write = []

        self.__read__ = getattr(self.__class__, "__read__", default_read)
        self.__create__ = getattr(self.__class__, "__create__", default_write)
        self.__update__ = getattr(self.__class__, "__update__", default_write)
        self.__delete__ = getattr(self.__class__, "__delete__", default_write)

    def __get_based_on_permission(self) -> Optional[BasePermission]:
        """
        Resolve and return a BasePermission instance from the manager attribute named by the class-level `__based_on__` configuration.

        If `__based_on__` is None or not configured on this class, returns None. If the referenced attribute exists on the target instance but is None, resets permissions to skip based-on evaluation and returns None. If the referenced attribute resolves to a manager that exposes a valid `Permission` subclass, constructs and returns that permission with the corresponding manager instance and the current request user.

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
            self.__set_permissions(skip_based_on=True)
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
            permissions = self.__create__
        elif action == "read":
            permissions = self.__read__
        elif action == "update":
            permissions = self.__update__
        elif action == "delete":
            permissions = self.__delete__
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

        for permission in self.__read__:
            filters.append(self._get_permission_filter(permission))

        return filters

    def describe_permissions(
        self,
        action: permission_type,
        attribute: str,
    ) -> tuple[str, ...]:
        """Return permission expressions considered for the given action/attribute."""
        if action == "create":
            base_permissions: tuple[str, ...] = tuple(self.__create__)
        elif action == "read":
            base_permissions = tuple(self.__read__)
        elif action == "update":
            base_permissions = tuple(self.__update__)
        elif action == "delete":
            base_permissions = tuple(self.__delete__)
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
