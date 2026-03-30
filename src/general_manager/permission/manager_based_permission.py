"""Manager-based permission implementations with additive and override semantics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Literal, Optional

from general_manager.permission.base_permission import (
    BasePermission,
    ReadPermissionPlan,
    ReadPermissionReason,
    UserLike,
)

if TYPE_CHECKING:
    from general_manager.permission.permission_data_manager import (
        PermissionDataManager,
    )
    from general_manager.manager.general_manager import GeneralManager

type permission_type = Literal["create", "read", "update", "delete"]

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
    from django.conf import settings

    gm_config = getattr(settings, "GENERAL_MANAGER", {})
    raw_defaults = (
        gm_config.get(_DEFAULT_PERMISSIONS_KEY) if isinstance(gm_config, dict) else None
    )
    configured_defaults: Mapping[str, Any] | None = None
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
        super().__init__(
            f"Based on configuration '{attribute_name}' is not valid or does not exist."
        )


class InvalidBasedOnTypeError(TypeError):
    """Raised when the `__based_on__` attribute does not resolve to a GeneralManager."""

    def __init__(self, attribute_name: str) -> None:
        super().__init__(f"Based on object {attribute_name} is not a GeneralManager.")


class UnknownPermissionActionError(ValueError):
    """Raised when an unsupported permission action is encountered."""

    def __init__(self, action: str) -> None:
        super().__init__(f"Action {action} not found.")


class notExistent:
    pass


class _ConfiguredManagerPermission(BasePermission):
    """Shared manager-based permission implementation with pluggable merge semantics."""

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
    _read_instance_result: bool | None
    _is_class_context: bool

    def __init_subclass__(cls, **kwargs: Any) -> None:
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
        from general_manager.manager.general_manager import GeneralManager

        super().__init__(instance, request_user)
        self._is_class_context = isinstance(instance, type) and issubclass(
            instance,
            GeneralManager,
        )
        if self.__class__ in (
            _ConfiguredManagerPermission,
            AdditiveManagerPermission,
            OverrideManagerPermission,
            ManagerBasedPermission,
        ):
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
        self._read_instance_result = None

    def __get_based_on_permission(self) -> Optional[BasePermission]:
        from general_manager.manager.general_manager import GeneralManager

        __based_on__ = self.__based_on__
        if __based_on__ is None:
            return None

        basis_object = getattr(self.instance, __based_on__, notExistent)
        if basis_object is notExistent:
            if self._is_class_context:
                return None
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
        if Permission is None or not issubclass(Permission, BasePermission):
            return None

        return Permission(
            instance=getattr(self.instance, __based_on__),
            request_user=self.request_user,
        )

    @staticmethod
    def _merge_filter_group_parts(
        delegated_part: dict[str, Any],
        local_part: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Merge representable filters; conflicting keys fall back to instance checks."""
        merged = dict(delegated_part)
        had_conflict = False
        for key, value in local_part.items():
            if key in merged and merged[key] != value:
                had_conflict = True
                continue
            merged[key] = value
        return merged, had_conflict

    def _set_effective_permissions(
        self,
        *,
        read_permissions: list[str],
        create_permissions: list[str],
        update_permissions: list[str],
        delete_permissions: list[str],
    ) -> None:
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
        attribute_permissions = {}
        for attribute in self.__class__.__dict__:
            if not attribute.startswith("__"):
                attribute_permissions[attribute] = getattr(self, attribute)
        return attribute_permissions

    def _get_base_permissions(self, action: permission_type) -> list[str]:
        if action == "create":
            return self._create_permissions
        if action == "read":
            return self._read_permissions
        if action == "update":
            return self._update_permissions
        if action == "delete":
            return self._delete_permissions
        raise UnknownPermissionActionError(action)

    def _get_attribute_permission_expressions(
        self,
        action: permission_type,
        attribute: str,
    ) -> tuple[list[str], bool]:
        attribute_source = self.__attribute_permissions.get(attribute)
        if isinstance(attribute_source, dict) and action in attribute_source:
            return list(attribute_source[action]), True
        return [], False

    def _evaluate_local_permission(
        self,
        *,
        action: permission_type,
        attribute: str,
        base_permissions: list[str],
        attribute_permissions: list[str],
        has_attribute_permissions: bool,
    ) -> bool:
        raise NotImplementedError

    def _describe_local_permissions(
        self,
        *,
        action: permission_type,
        attribute: str,
        base_permissions: list[str],
        attribute_permissions: list[str],
        has_attribute_permissions: bool,
    ) -> tuple[str, ...]:
        raise NotImplementedError

    def check_permission(
        self,
        action: permission_type,
        attribute: str,
    ) -> bool:
        self._get_base_permissions(action)
        if self._is_superuser():
            self.__overall_results[action] = True
            return True
        if (
            self.__based_on_permission
            and not self.__based_on_permission.check_permission(action, attribute)
        ):
            return False

        base_permissions = self._get_base_permissions(action)
        attribute_permissions, has_attribute_permissions = (
            self._get_attribute_permission_expressions(action, attribute)
        )

        can_use_action_cache = (
            not has_attribute_permissions and self.__based_on_permission is None
        )

        if can_use_action_cache:
            last_result = self.__overall_results.get(action)
            if last_result is not None:
                return last_result

        permission = self._evaluate_local_permission(
            action=action,
            attribute=attribute,
            base_permissions=base_permissions,
            attribute_permissions=attribute_permissions,
            has_attribute_permissions=has_attribute_permissions,
        )
        if can_use_action_cache:
            self.__overall_results[action] = permission
        return permission

    def __check_specific_permission(
        self,
        permissions: list[str],
    ) -> bool:
        if not permissions:
            return True
        for permission in permissions:
            if self.validate_permission_string(permission):
                return True
        return False

    def _check_permission_list(self, permissions: list[str]) -> bool:
        return self.__check_specific_permission(permissions)

    def get_permission_filter(
        self,
    ) -> list[dict[Literal["filter", "exclude"], dict[str, str]]]:
        return self.get_read_permission_plan().filters

    def can_read_instance(self) -> bool:
        """Return whether the current user may see that this manager exists."""
        if self._is_superuser():
            self._read_instance_result = True
            return True
        if self._read_instance_result is not None:
            return self._read_instance_result
        if self.__based_on_permission is not None and not (
            self.__based_on_permission.can_read_instance()
        ):
            self._read_instance_result = False
            return False
        result = self._check_permission_list(self._read_permissions)
        self._read_instance_result = result
        return result

    def get_read_permission_plan(self) -> ReadPermissionPlan:
        """Return read prefilters plus whether row-level checks must still run."""
        if self._is_superuser():
            return ReadPermissionPlan(
                filters=[{"filter": {}, "exclude": {}}],
                requires_instance_check=False,
            )
        __based_on__ = self.__based_on__
        requires_instance_check = False
        instance_check_reasons: set[ReadPermissionReason] = set()
        delegated_filters: list[dict[Literal["filter", "exclude"], dict[str, Any]]] = [
            {"filter": {}, "exclude": {}}
        ]
        if self.__based_on_permission is not None:
            delegated_plan_method = getattr(
                self.__based_on_permission,
                "get_read_permission_plan",
                None,
            )
            delegated_plan: ReadPermissionPlan | None = None
            if callable(delegated_plan_method):
                plan_candidate = delegated_plan_method()
                if isinstance(plan_candidate, ReadPermissionPlan):
                    delegated_plan = plan_candidate
                elif isinstance(getattr(plan_candidate, "filters", None), list) and (
                    isinstance(
                        getattr(plan_candidate, "requires_instance_check", None),
                        bool,
                    )
                ):
                    raw_reasons = getattr(plan_candidate, "instance_check_reasons", ())
                    delegated_plan = ReadPermissionPlan(
                        filters=list(plan_candidate.filters),
                        requires_instance_check=plan_candidate.requires_instance_check,
                        instance_check_reasons=tuple(raw_reasons)
                        if isinstance(raw_reasons, (list, tuple))
                        else (),
                    )
            if delegated_plan is None:
                delegated_plan = ReadPermissionPlan(
                    filters=self.__based_on_permission.get_permission_filter(),
                    requires_instance_check=True,
                    instance_check_reasons=("no_prefilter_backend",),
                )
            requires_instance_check = (
                requires_instance_check or delegated_plan.requires_instance_check
            )
            instance_check_reasons.update(delegated_plan.instance_check_reasons)
            delegated_filters = []
            for delegated_filter_group in delegated_plan.filters:
                filter_dict = delegated_filter_group.get("filter", {})
                exclude_dict = delegated_filter_group.get("exclude", {})
                delegated_filters.append(
                    {
                        "filter": {
                            f"{__based_on__}__{key}": value
                            for key, value in filter_dict.items()
                        },
                        "exclude": {
                            f"{__based_on__}__{key}": value
                            for key, value in exclude_dict.items()
                        },
                    }
                )
        elif self.__based_on__ is not None and self._is_class_context:
            requires_instance_check = True
            instance_check_reasons.add("based_on_class_context")

        local_filters: list[dict[Literal["filter", "exclude"], dict[str, Any]]] = []
        for permission in self._read_permissions:
            permission_filter, is_filterable = self._get_permission_filter_info(
                permission
            )
            if is_filterable:
                local_filters.append(permission_filter)
            else:
                requires_instance_check = True
                instance_check_reasons.add("unfilterable_read_rule")

        if not local_filters:
            local_filters = [{"filter": {}, "exclude": {}}]

        combined_filters: list[dict[Literal["filter", "exclude"], dict[str, Any]]] = []
        for delegated_filter_group in delegated_filters:
            for local_filter_group in local_filters:
                combined_filter, filter_conflict = self._merge_filter_group_parts(
                    dict(delegated_filter_group.get("filter", {})),
                    dict(local_filter_group.get("filter", {})),
                )
                combined_exclude, exclude_conflict = self._merge_filter_group_parts(
                    dict(delegated_filter_group.get("exclude", {})),
                    dict(local_filter_group.get("exclude", {})),
                )
                requires_instance_check = (
                    requires_instance_check or filter_conflict or exclude_conflict
                )
                if filter_conflict or exclude_conflict:
                    instance_check_reasons.add("filter_key_conflict")
                combined_filters.append(
                    {
                        "filter": combined_filter,
                        "exclude": combined_exclude,
                    }
                )

        return ReadPermissionPlan(
            filters=combined_filters or [{"filter": {}, "exclude": {}}],
            requires_instance_check=requires_instance_check,
            instance_check_reasons=tuple(sorted(instance_check_reasons)),
        )

    def describe_permissions(
        self,
        action: permission_type,
        attribute: str,
    ) -> tuple[str, ...]:
        base_permissions = self._get_base_permissions(action)
        attribute_permissions, has_attribute_permissions = (
            self._get_attribute_permission_expressions(action, attribute)
        )
        combined = self._describe_local_permissions(
            action=action,
            attribute=attribute,
            base_permissions=base_permissions,
            attribute_permissions=attribute_permissions,
            has_attribute_permissions=has_attribute_permissions,
        )
        if self.__based_on_permission is not None:
            combined += self.__based_on_permission.describe_permissions(
                action, attribute
            )
        return combined


class AdditiveManagerPermission(_ConfiguredManagerPermission):
    """Manager-based permissions where attribute rules add an extra gate."""

    def _evaluate_local_permission(
        self,
        *,
        action: permission_type,
        attribute: str,
        base_permissions: list[str],
        attribute_permissions: list[str],
        has_attribute_permissions: bool,
    ) -> bool:
        del action, attribute
        base_allowed = self._check_permission_list(base_permissions)
        if not has_attribute_permissions:
            return base_allowed
        attribute_allowed = self._check_permission_list(attribute_permissions)
        return base_allowed and attribute_allowed

    def _describe_local_permissions(
        self,
        *,
        action: permission_type,
        attribute: str,
        base_permissions: list[str],
        attribute_permissions: list[str],
        has_attribute_permissions: bool,
    ) -> tuple[str, ...]:
        del action, attribute, has_attribute_permissions
        return tuple(base_permissions) + tuple(attribute_permissions)


class OverrideManagerPermission(_ConfiguredManagerPermission):
    """Manager-based permissions where attribute rules replace the CRUD base rule."""

    def _evaluate_local_permission(
        self,
        *,
        action: permission_type,
        attribute: str,
        base_permissions: list[str],
        attribute_permissions: list[str],
        has_attribute_permissions: bool,
    ) -> bool:
        del action, attribute
        if has_attribute_permissions:
            return self._check_permission_list(attribute_permissions)
        return self._check_permission_list(base_permissions)

    def _describe_local_permissions(
        self,
        *,
        action: permission_type,
        attribute: str,
        base_permissions: list[str],
        attribute_permissions: list[str],
        has_attribute_permissions: bool,
    ) -> tuple[str, ...]:
        del action, attribute
        if has_attribute_permissions:
            return tuple(attribute_permissions)
        return tuple(base_permissions)


class ManagerBasedPermission(AdditiveManagerPermission):
    """Deprecated compatibility alias for `AdditiveManagerPermission`."""


__all__ = [
    "AdditiveManagerPermission",
    "InvalidBasedOnConfigurationError",
    "InvalidBasedOnTypeError",
    "ManagerBasedPermission",
    "OverrideManagerPermission",
    "UnknownPermissionActionError",
]
