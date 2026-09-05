"""Manager-based permission implementations with additive and override semantics."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from general_manager.permission.base_permission import (
    BasePermission,
    PermissionConstraint,
    ReadPermissionDecision,
    ReadPermissionPlan,
    ReadPermissionReason,
    UserLike,
)
from general_manager.permission.permission_checks import PermissionFilterDecision

if TYPE_CHECKING:
    from general_manager.permission.permission_data_manager import (
        PermissionDataManager,
    )
    from general_manager.manager.general_manager import GeneralManager

type permission_type = Literal["create", "read", "update", "delete"]


@dataclass(frozen=True, slots=True)
class _ReadPermissionFragment:
    """Normalized planning outcome for one registered permission fragment."""

    decision: ReadPermissionDecision
    constraint: PermissionConstraint | None = None
    requires_instance_check: bool = False


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
    configured_defaults: Mapping[str, object] | None = None
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
            defaults[action] = (
                list(configured_permissions)
                if isinstance(configured_permissions, Iterable)
                and not isinstance(configured_permissions, str | bytes | bytearray)
                else [str(configured_permissions)]
            )
    return defaults


class InvalidBasedOnConfigurationError(ValueError):
    """Raised when the configured `__based_on__` attribute is missing or invalid.

    Instance-level delegated permissions require the configured attribute to
    exist. Missing attributes raise this error; class-level contexts instead
    defer to row-level instance checks because no concrete delegated object is
    available yet.
    """

    def __init__(self, attribute_name: str) -> None:
        super().__init__(
            f"Based on configuration '{attribute_name}' is not valid or does not exist."
        )


class InvalidBasedOnTypeError(TypeError):
    """Raised when the `__based_on__` attribute does not resolve to a GeneralManager.

    The delegated object must be a ``GeneralManager`` instance or manager class
    after any dictionary/id coercion through the configured manager field type.
    """

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

    __based_on__: ClassVar[str | None] = None
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

    def __init_subclass__(cls, **kwargs: object) -> None:
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
        instance: PermissionDataManager[GeneralManager] | GeneralManager,
        request_user: UserLike | object,
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
        self.__overall_results: dict[permission_type, bool | None] = {
            "create": None,
            "read": None,
            "update": None,
            "delete": None,
        }
        self._read_instance_result = None

    def __get_based_on_permission(self) -> BasePermission | None:
        from general_manager.manager.general_manager import GeneralManager
        from general_manager.permission.permission_data_manager import (
            PermissionDataManager,
        )

        __based_on__ = self.__based_on__
        if __based_on__ is None:
            return None

        basis_object = getattr(self.instance, __based_on__, notExistent)
        if (
            basis_object is not None
            and basis_object is not notExistent
            and not isinstance(basis_object, GeneralManager)
            and isinstance(self.instance, PermissionDataManager)
            and self.instance.manager is not None
        ):
            field_type = self.instance.manager.Interface.get_field_type(__based_on__)
            if isinstance(field_type, type) and issubclass(field_type, GeneralManager):
                if isinstance(basis_object, dict):
                    basis_object = field_type(**basis_object)
                else:
                    basis_object = field_type(id=basis_object)
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

        return cast(
            BasePermission,
            Permission(
                instance=basis_object,
                request_user=self.request_user,
            ),
        )

    @staticmethod
    def _merge_filter_group_parts(
        delegated_part: dict[str, object],
        local_part: dict[str, object],
    ) -> tuple[dict[str, object], bool]:
        """Merge representable filters; conflicting keys fall back to instance checks."""
        merged = dict(delegated_part)
        had_conflict = False
        for key, value in local_part.items():
            if key in merged and merged[key] != value:
                had_conflict = True
                continue
            merged[key] = value
        return merged, had_conflict

    def _plan_permission_fragment(self, fragment: str) -> _ReadPermissionFragment:
        """Normalize one callback into a static, row, or instance outcome."""
        result = self._get_permission_filter_result(fragment)
        if result is PermissionFilterDecision.ALLOW_ALL:
            return _ReadPermissionFragment(decision="allow_all")
        if result is PermissionFilterDecision.DENY_ALL:
            return _ReadPermissionFragment(decision="deny_all")
        if result is None:
            return _ReadPermissionFragment(
                decision="conditional",
                requires_instance_check=True,
            )
        return _ReadPermissionFragment(
            decision="conditional",
            constraint=result,
        )

    def _plan_permission_expression(self, expression: str) -> ReadPermissionPlan:
        """Compose the ``&``-joined fragments in one read alternative."""
        combined_filter: dict[str, object] = {}
        combined_exclude: dict[str, object] = {}
        has_constraint = False
        requires_instance_check = False
        reasons: set[ReadPermissionReason] = set()

        for fragment_name in expression.split("&"):
            fragment = self._plan_permission_fragment(fragment_name)
            if fragment.decision == "deny_all":
                return ReadPermissionPlan(
                    filters=[],
                    requires_instance_check=False,
                    decision="deny_all",
                )
            if fragment.decision == "allow_all":
                continue
            if fragment.requires_instance_check:
                requires_instance_check = True
                reasons.add("unfilterable_read_rule")
            if fragment.constraint is None:
                continue
            has_constraint = True
            combined_filter, filter_conflict = self._merge_filter_group_parts(
                combined_filter,
                dict(fragment.constraint.get("filter", {})),
            )
            fragment_exclude = dict(fragment.constraint.get("exclude", {}))
            if (
                combined_exclude
                and fragment_exclude
                and combined_exclude != fragment_exclude
            ):
                requires_instance_check = True
                reasons.add("compound_exclude_semantics")
            combined_exclude, exclude_conflict = self._merge_filter_group_parts(
                combined_exclude,
                fragment_exclude,
            )
            if filter_conflict or exclude_conflict:
                requires_instance_check = True
                reasons.add("filter_key_conflict")

        if not has_constraint and not requires_instance_check:
            return ReadPermissionPlan(
                filters=[],
                requires_instance_check=False,
                decision="allow_all",
            )
        return ReadPermissionPlan(
            filters=(
                [{"filter": combined_filter, "exclude": combined_exclude}]
                if has_constraint
                else []
            ),
            requires_instance_check=requires_instance_check,
            instance_check_reasons=tuple(sorted(reasons)),
            decision="conditional",
        )

    def _plan_local_read_permissions(self) -> ReadPermissionPlan:
        """Compose configured read expressions as authorization alternatives."""
        if not self._read_permissions:
            return ReadPermissionPlan(
                filters=[],
                requires_instance_check=False,
                decision="allow_all",
            )

        surviving_plans: list[ReadPermissionPlan] = []
        for expression in self._read_permissions:
            expression_plan = self._plan_permission_expression(expression)
            if expression_plan.decision == "allow_all":
                return expression_plan
            if expression_plan.decision != "deny_all":
                surviving_plans.append(expression_plan)

        if not surviving_plans:
            return ReadPermissionPlan(
                filters=[],
                requires_instance_check=False,
                decision="deny_all",
            )

        requires_instance_check = any(
            plan.requires_instance_check for plan in surviving_plans
        )
        reasons = {
            reason for plan in surviving_plans for reason in plan.instance_check_reasons
        }
        has_unrestricted_alternative = any(
            plan.requires_instance_check and not plan.filters
            for plan in surviving_plans
        )
        filters = (
            []
            if has_unrestricted_alternative
            else [constraint for plan in surviving_plans for constraint in plan.filters]
        )
        return ReadPermissionPlan(
            filters=filters,
            requires_instance_check=requires_instance_check,
            instance_check_reasons=tuple(sorted(reasons)),
            decision="conditional",
        )

    @staticmethod
    def _coerce_read_permission_plan(candidate: object) -> ReadPermissionPlan | None:
        """Copy a plan-shaped object while applying compatibility defaults."""
        filters = getattr(candidate, "filters", None)
        requires_instance_check = getattr(candidate, "requires_instance_check", None)
        if not isinstance(filters, list) or not isinstance(
            requires_instance_check, bool
        ):
            return None

        raw_reasons = getattr(candidate, "instance_check_reasons", ())
        reasons = tuple(raw_reasons) if isinstance(raw_reasons, (list, tuple)) else ()
        raw_decision = getattr(candidate, "decision", "conditional")
        decision: ReadPermissionDecision = (
            cast(ReadPermissionDecision, raw_decision)
            if raw_decision in ("allow_all", "deny_all", "conditional")
            else "conditional"
        )
        return ReadPermissionPlan(
            filters=cast(list[PermissionConstraint], list(filters)),
            requires_instance_check=requires_instance_check,
            instance_check_reasons=cast(tuple[ReadPermissionReason, ...], reasons),
            decision=decision,
        )

    @staticmethod
    def _prefix_read_permission_plan(
        plan: ReadPermissionPlan,
        prefix: str,
    ) -> ReadPermissionPlan:
        """Prefix delegated constraints while preserving the plan decision."""
        if plan.decision != "conditional":
            return ReadPermissionPlan(
                filters=[],
                requires_instance_check=False,
                decision=plan.decision,
            )
        return ReadPermissionPlan(
            filters=[
                {
                    "filter": {
                        f"{prefix}__{key}": value
                        for key, value in constraint.get("filter", {}).items()
                    },
                    "exclude": {
                        f"{prefix}__{key}": value
                        for key, value in constraint.get("exclude", {}).items()
                    },
                }
                for constraint in plan.filters
            ],
            requires_instance_check=plan.requires_instance_check,
            instance_check_reasons=plan.instance_check_reasons,
            decision="conditional",
        )

    def _and_read_permission_plans(
        self,
        delegated_plan: ReadPermissionPlan,
        local_plan: ReadPermissionPlan,
    ) -> ReadPermissionPlan:
        """Compose delegated and local plans as an outer authorization AND."""
        if delegated_plan.decision == "deny_all" or local_plan.decision == "deny_all":
            return ReadPermissionPlan(
                filters=[],
                requires_instance_check=False,
                decision="deny_all",
            )
        if delegated_plan.decision == "allow_all":
            return local_plan
        if local_plan.decision == "allow_all":
            return delegated_plan

        requires_instance_check = (
            delegated_plan.requires_instance_check or local_plan.requires_instance_check
        )
        reasons = set(delegated_plan.instance_check_reasons)
        reasons.update(local_plan.instance_check_reasons)
        delegated_filters = delegated_plan.filters or [{"filter": {}, "exclude": {}}]
        local_filters = local_plan.filters or [{"filter": {}, "exclude": {}}]
        combined_filters: list[PermissionConstraint] = []
        for delegated_filter_group in delegated_filters:
            for local_filter_group in local_filters:
                combined_filter, filter_conflict = self._merge_filter_group_parts(
                    dict(delegated_filter_group.get("filter", {})),
                    dict(local_filter_group.get("filter", {})),
                )
                delegated_exclude = dict(delegated_filter_group.get("exclude", {}))
                local_exclude = dict(local_filter_group.get("exclude", {}))
                if (
                    delegated_exclude
                    and local_exclude
                    and delegated_exclude != local_exclude
                ):
                    requires_instance_check = True
                    reasons.add("compound_exclude_semantics")
                combined_exclude, exclude_conflict = self._merge_filter_group_parts(
                    delegated_exclude,
                    local_exclude,
                )
                if filter_conflict or exclude_conflict:
                    requires_instance_check = True
                    reasons.add("filter_key_conflict")
                combined_filters.append(
                    {
                        "filter": combined_filter,
                        "exclude": combined_exclude,
                    }
                )
        if combined_filters == [{"filter": {}, "exclude": {}}]:
            combined_filters = []
        return ReadPermissionPlan(
            filters=combined_filters,
            requires_instance_check=requires_instance_check,
            instance_check_reasons=tuple(sorted(reasons)),
            decision="conditional",
        )

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
        attribute_permissions: dict[str, dict[permission_type, list[str]]] = {}
        for permission_class in reversed(self.__class__.__mro__):
            for attribute, value in vars(permission_class).items():
                if attribute.startswith("__") or not isinstance(value, dict):
                    continue
                attribute_permissions[attribute] = cast(
                    dict[permission_type, list[str]], value
                )
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

    def check_operation_permission(self, action: permission_type) -> bool:
        """Evaluate the CRUD-level permission for an operation without field data."""
        base_permissions = self._get_base_permissions(action)
        if self._is_superuser():
            self.__overall_results[action] = True
            return True
        if self.__based_on_permission is not None and not (
            self.__based_on_permission.check_operation_permission(action)
        ):
            return False

        can_use_action_cache = self.__based_on_permission is None
        if can_use_action_cache:
            last_result = self.__overall_results.get(action)
            if last_result is not None:
                return last_result

        permission = self._check_permission_list(base_permissions)
        if can_use_action_cache:
            self.__overall_results[action] = permission
        return permission

    def get_permission_filter(
        self,
    ) -> list[PermissionConstraint]:
        plan = self.get_read_permission_plan()
        return plan.filters or [{"filter": {}, "exclude": {}}]

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
        """Compose static decisions, prefilters, and required row-level checks.

        Fragments joined with ``&`` are planned as AND, while entries in
        ``__read__`` are alternatives planned as OR. Delegated ``__based_on__``
        authorization is an outer AND whose filter and exclude keys are prefixed
        with ``"<based_on>__"``. Unfilterable rules, unresolved class context,
        and filter conflicts retain an instance gate with sorted reason labels.
        """
        if self._is_superuser():
            return ReadPermissionPlan(
                filters=[],
                requires_instance_check=False,
                decision="allow_all",
            )
        local_plan = self._plan_local_read_permissions()
        if local_plan.decision == "deny_all":
            return local_plan
        __based_on__ = self.__based_on__
        if self.__based_on_permission is None and not (
            __based_on__ is not None and self._is_class_context
        ):
            return local_plan

        delegated_plan: ReadPermissionPlan
        if self.__based_on_permission is not None:
            delegated_plan_method = getattr(
                self.__based_on_permission,
                "get_read_permission_plan",
                None,
            )
            delegated_plan_candidate: ReadPermissionPlan | None = None
            if callable(delegated_plan_method):
                plan_candidate = delegated_plan_method()
                delegated_plan_candidate = self._coerce_read_permission_plan(
                    plan_candidate
                )
            if delegated_plan_candidate is None:
                delegated_plan_candidate = ReadPermissionPlan(
                    filters=self.__based_on_permission.get_permission_filter(),
                    requires_instance_check=True,
                    instance_check_reasons=("no_prefilter_backend",),
                )
            delegated_plan = self._prefix_read_permission_plan(
                delegated_plan_candidate,
                cast(str, __based_on__),
            )
        else:
            delegated_plan = ReadPermissionPlan(
                filters=[],
                requires_instance_check=True,
                instance_check_reasons=("based_on_class_context",),
            )
        return self._and_read_permission_plans(delegated_plan, local_plan)

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

    def describe_operation_permissions(
        self,
        action: permission_type,
    ) -> tuple[str, ...]:
        combined = tuple(self._get_base_permissions(action))
        if self.__based_on_permission is not None:
            combined += self.__based_on_permission.describe_operation_permissions(
                action
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
