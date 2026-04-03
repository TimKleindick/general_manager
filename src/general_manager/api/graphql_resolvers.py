"""
Resolver-construction helpers extracted from ``api/graphql.py``.

These standalone functions build Graphene resolver callables and apply
query modifiers (filtering, pagination, grouping).  They hold no
reference to the ``GraphQL`` class and can therefore be imported freely
without risk of circular imports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Generic, TYPE_CHECKING, TypeVar, cast

import graphene  # type: ignore[import]

from general_manager.logging import get_logger
from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement.measurement import Measurement
from general_manager.api.graphql_errors import get_read_permission_filter
from general_manager.utils.type_checks import safe_issubclass

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo
    from general_manager.bucket.group_bucket import GroupBucket
    from general_manager.permission.base_permission import (
        BasePermission,
        ReadPermissionPlan,
    )

GeneralManagerT = TypeVar("GeneralManagerT", bound=GeneralManager)
logger = get_logger("api.graphql")


@dataclass(slots=True)
class ReadAuthorizationResult(Generic[GeneralManagerT]):
    queryset: Bucket[GeneralManagerT]
    candidate_count: int
    authorized_count: int
    denied_count: int
    backend_shape: str
    requires_instance_check: bool
    instance_check_reasons: tuple[str, ...]


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------


def parse_input(input_val: dict[str, Any] | str | None) -> dict[str, Any]:
    """
    Normalise a filter or exclude input into a plain dictionary.

    Accepts a dict, a JSON-encoded string, or ``None``.  Returns an empty
    dict for ``None`` or unparseable JSON strings.
    """
    if input_val is None:
        return {}
    if isinstance(input_val, str):
        try:
            return json.loads(input_val)
        except (json.JSONDecodeError, ValueError):
            return {}
    return input_val


# ---------------------------------------------------------------------------
# Queryset modifiers
# ---------------------------------------------------------------------------


def apply_query_parameters(
    queryset: Bucket[GeneralManager],
    filter_input: dict[str, Any] | str | None,
    exclude_input: dict[str, Any] | str | None,
    sort_by: graphene.Enum | None,
    reverse: bool,
) -> Bucket[GeneralManager]:
    """
    Apply filtering, exclusion, and sorting to *queryset*.

    Parameters:
        filter_input: Filters to apply, as a dict or JSON string.
        exclude_input: Exclusions to apply, as a dict or JSON string.
        sort_by: Field to sort by (Graphene Enum value).
        reverse: If ``True``, reverse the sort order.

    Returns:
        The queryset after filters, exclusions, and sorting are applied.
    """
    filters = parse_input(filter_input)
    if filters:
        queryset = queryset.filter(**filters)

    excludes = parse_input(exclude_input)
    if excludes:
        queryset = queryset.exclude(**excludes)

    if sort_by:
        sort_by_str = cast(str, getattr(sort_by, "value", sort_by))
        queryset = queryset.sort(sort_by_str, reverse=reverse)

    return queryset


def apply_permission_filters(
    queryset: Bucket[GeneralManagerT],
    general_manager_class: type[GeneralManagerT],
    info: GraphQLResolveInfo,
) -> Bucket[GeneralManagerT]:
    """
    Apply permission-based filters to *queryset* for the current user.

    Parameters:
        queryset: Queryset to constrain.
        general_manager_class: Manager class providing permission rules.
        info: GraphQL resolver info containing the request user.

    Returns:
        Queryset constrained by read permissions.
    """
    result = apply_read_authorization(
        queryset,
        general_manager_class,
        info,
        source="list",
    )
    return result.queryset


def apply_read_authorization(
    queryset: Bucket[GeneralManagerT],
    general_manager_class: type[GeneralManagerT],
    info: GraphQLResolveInfo,
    *,
    source: str,
) -> ReadAuthorizationResult:
    """Apply read prefilters plus the final row gate and emit aggregate observability."""
    permission_plan = get_read_permission_filter(general_manager_class, info)
    backend_shape = get_backend_shape(general_manager_class)
    instance_check_reasons = resolve_instance_check_reasons(
        permission_plan,
        backend_shape=backend_shape,
    )

    filtered_queryset = queryset
    if not permission_plan.requires_instance_check and permission_plan.filters == [
        {"filter": {}, "exclude": {}}
    ]:
        candidate_count = len(queryset)
        result = ReadAuthorizationResult(
            queryset=queryset,
            candidate_count=candidate_count,
            authorized_count=candidate_count,
            denied_count=0,
            backend_shape=backend_shape,
            requires_instance_check=False,
            instance_check_reasons=instance_check_reasons,
        )
        return result

    if permission_plan.filters:
        filtered_queryset = queryset.none()
        for permission_filter in permission_plan.filters:
            filter_dict = permission_filter.get("filter", {})
            exclude_dict = permission_filter.get("exclude", {})
            if not filter_dict and not exclude_dict:
                qs_perm = queryset
            else:
                qs_perm = queryset.filter(**filter_dict).exclude(**exclude_dict)
            filtered_queryset = filtered_queryset | qs_perm

    result = filter_queryset_by_read_permission(
        filtered_queryset,
        general_manager_class,
        info,
        requires_instance_check=permission_plan.requires_instance_check,
        instance_check_reasons=instance_check_reasons,
        backend_shape=backend_shape,
    )
    if result.requires_instance_check:
        log_read_authorization_summary(
            general_manager_class=general_manager_class,
            source=source,
            result=result,
        )
    return result


def filter_queryset_by_read_permission(
    queryset: Bucket[GeneralManagerT],
    general_manager_class: type[GeneralManagerT],
    info: GraphQLResolveInfo,
    *,
    requires_instance_check: bool = True,
    instance_check_reasons: tuple[str, ...] = (),
    backend_shape: str = "unknown",
) -> ReadAuthorizationResult:
    """Apply final row-level read authorization to a bucket."""
    if not requires_instance_check:
        candidate_count = len(queryset)
        return ReadAuthorizationResult(
            queryset=queryset,
            candidate_count=candidate_count,
            authorized_count=candidate_count,
            denied_count=0,
            backend_shape=backend_shape,
            requires_instance_check=False,
            instance_check_reasons=instance_check_reasons,
        )

    PermissionClass: type[BasePermission] | None = getattr(
        general_manager_class, "Permission", None
    )
    if PermissionClass is None:
        candidate_count = len(queryset)
        return ReadAuthorizationResult(
            queryset=queryset,
            candidate_count=candidate_count,
            authorized_count=candidate_count,
            denied_count=0,
            backend_shape=backend_shape,
            requires_instance_check=False,
            instance_check_reasons=instance_check_reasons,
        )

    authorized_ids: list[Any] = []
    authorized_instances: list[GeneralManagerT] = []
    candidate_count = 0
    for instance in queryset:
        candidate_count += 1
        if PermissionClass(instance, info.context.user).can_read_instance():
            instance_id = getattr(instance, "identification", {}).get("id")
            if instance_id is None:
                authorized_instances.append(instance)
            else:
                authorized_ids.append(instance_id)

    authorized_queryset = (
        queryset.filter(id__in=authorized_ids) if authorized_ids else queryset.none()
    )
    for instance in authorized_instances:
        authorized_queryset = authorized_queryset | instance
    authorized_count = len(authorized_ids) + len(authorized_instances)
    return ReadAuthorizationResult(
        queryset=authorized_queryset,
        candidate_count=candidate_count,
        authorized_count=authorized_count,
        denied_count=max(candidate_count - authorized_count, 0),
        backend_shape=backend_shape,
        requires_instance_check=True,
        instance_check_reasons=instance_check_reasons,
    )


def get_backend_shape(general_manager_class: type[GeneralManager]) -> str:
    """Classify the manager's interface into a stable backend-shape label."""
    from general_manager.interface import (
        CalculationInterface,
        DatabaseInterface,
        ExistingModelInterface,
        ReadOnlyInterface,
        RequestInterface,
    )

    interface = getattr(general_manager_class, "Interface", None)
    if not isinstance(interface, type):
        return "unknown"
    if issubclass(interface, DatabaseInterface):
        return "database"
    if issubclass(interface, ReadOnlyInterface):
        return "read_only"
    if issubclass(interface, ExistingModelInterface):
        return "existing_model"
    if issubclass(interface, RequestInterface):
        return "request"
    if issubclass(interface, CalculationInterface):
        return "calculation"
    return "custom"


def resolve_instance_check_reasons(
    permission_plan: ReadPermissionPlan,
    *,
    backend_shape: str,
) -> tuple[str, ...]:
    """Return stable reason labels for why the final instance gate was required."""
    reasons = set(permission_plan.instance_check_reasons)
    if (
        permission_plan.requires_instance_check
        and not reasons
        and backend_shape != "database"
    ):
        reasons.add("no_prefilter_backend")
    return tuple(sorted(reasons))


def log_read_authorization_summary(
    *,
    general_manager_class: type[GeneralManager],
    source: str,
    result: ReadAuthorizationResult,
) -> None:
    """Emit one aggregate structured log event for a read-authorization pass."""
    logger.info(
        "graphql read authorization summary",
        context={
            "source": source,
            "manager": general_manager_class.__name__,
            "backend_shape": result.backend_shape,
            "candidate_count": result.candidate_count,
            "authorized_count": result.authorized_count,
            "denied_count": result.denied_count,
            "requires_instance_check": result.requires_instance_check,
            "instance_check_reasons": list(result.instance_check_reasons),
        },
    )


def apply_pagination(
    queryset: Bucket[GeneralManager] | GroupBucket[GeneralManager],
    page: int | None,
    page_size: int | None,
) -> Bucket[GeneralManager] | GroupBucket[GeneralManager]:
    """
    Return a paginated slice of *queryset*.

    Returns the full queryset when neither ``page`` nor ``page_size`` is
    given.  Defaults to page 1 / size 10 when only one parameter is provided.
    """
    if page is not None or page_size is not None:
        page = page or 1
        page_size = page_size or 10
        offset = (page - 1) * page_size
        queryset = cast(Bucket, queryset[offset : offset + page_size])
    return queryset


def apply_grouping(
    queryset: Bucket[GeneralManager],
    group_by: list[str] | None,
) -> Bucket[GeneralManager] | GroupBucket[GeneralManager]:
    """
    Group *queryset* by the specified fields.

    ``group_by=[""]`` groups by all default fields; a non-empty list groups
    by those fields explicitly.  Returns the original queryset unchanged when
    ``group_by`` is ``None``.
    """
    if group_by is not None:
        if group_by == [""]:
            return queryset.group_by()
        else:
            return queryset.group_by(*group_by)
    return queryset


# ---------------------------------------------------------------------------
# Permission check
# ---------------------------------------------------------------------------


def check_read_permission(
    instance: GeneralManager,
    info: GraphQLResolveInfo,
    field_name: str,
) -> bool:
    """Return ``True`` if the request user may read *field_name* on *instance*."""
    PermissionClass: type[BasePermission] | None = getattr(instance, "Permission", None)
    if PermissionClass:
        return PermissionClass(instance, info.context.user).check_permission(
            "read", field_name
        )
    return True


def can_read_instance(
    instance: GeneralManager,
    info: GraphQLResolveInfo,
) -> bool:
    """Return whether the request user may see that *instance* exists."""
    PermissionClass: type[BasePermission] | None = getattr(instance, "Permission", None)
    if PermissionClass:
        return PermissionClass(instance, info.context.user).can_read_instance()
    return True


# ---------------------------------------------------------------------------
# Resolver factories
# ---------------------------------------------------------------------------


def create_measurement_resolver(field_name: str) -> Callable[..., Any]:
    """
    Return a resolver for a :class:`~general_manager.measurement.Measurement` field.

    The resolver checks read permission, then returns a ``{"value": …,
    "unit": …}`` dict (with optional unit conversion via ``target_unit``).
    """

    def resolver(
        self: GeneralManager,
        info: GraphQLResolveInfo,
        target_unit: str | None = None,
    ) -> dict[str, Any] | None:
        if not check_read_permission(self, info, field_name):
            return None
        result = getattr(self, field_name)
        if not isinstance(result, Measurement):
            return None
        if target_unit:
            result = result.to(target_unit)
        return {
            "value": result.quantity.magnitude,
            "unit": str(result.quantity.units),
        }

    return resolver


def create_normal_resolver(field_name: str) -> Callable[..., Any]:
    """Return a resolver for a scalar (non-list, non-Measurement) field."""

    def resolver(self: GeneralManager, info: GraphQLResolveInfo) -> Any:
        if not check_read_permission(self, info, field_name):
            return None
        return getattr(self, field_name)

    return resolver


def create_list_resolver(
    base_getter: Callable[[Any, bool], Any],
    fallback_manager_class: type[GeneralManager],
) -> Callable[..., Any]:
    """
    Build a resolver for list fields that applies filters, permissions, and pagination.

    Parameters:
        base_getter: Callable returning the base queryset; receives the
            parent object and the ``include_inactive`` flag.
        fallback_manager_class: Manager used when *base_getter* returns
            ``None``.

    Returns:
        A Graphene-compatible resolver function.
    """

    def resolver(
        self: GeneralManager,
        info: GraphQLResolveInfo,
        filter: dict[str, Any] | str | None = None,
        exclude: dict[str, Any] | str | None = None,
        sort_by: graphene.Enum | None = None,
        reverse: bool = False,
        page: int | None = None,
        page_size: int | None = None,
        group_by: list[str] | None = None,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        base_queryset = base_getter(self, include_inactive)
        if base_queryset is None:
            if include_inactive:
                base_queryset = fallback_manager_class.filter(include_inactive=True)
            else:
                base_queryset = fallback_manager_class.all()
        manager_class = getattr(base_queryset, "_manager_class", None)
        if not (
            isinstance(manager_class, type)
            and issubclass(manager_class, GeneralManager)
        ):
            manager_class = fallback_manager_class
        qs = apply_permission_filters(base_queryset, manager_class, info)
        qs = apply_query_parameters(qs, filter, exclude, sort_by, reverse)
        qs_grouped = apply_grouping(qs, group_by)

        total_count = len(qs_grouped)

        qs_paginated = apply_pagination(qs_grouped, page, page_size)

        page_info = {
            "total_count": total_count,
            "page_size": page_size,
            "current_page": page or 1,
            "total_pages": (
                ((total_count + page_size - 1) // page_size) if page_size else 1
            ),
        }
        return {
            "items": qs_paginated,
            "pageInfo": page_info,
        }

    return resolver


def create_resolver(field_name: str, field_type: type) -> Callable[..., Any]:
    """
    Return the appropriate resolver for *field_name* based on *field_type*.

    Dispatches to :func:`create_list_resolver` for ``GeneralManager`` list
    fields, :func:`create_measurement_resolver` for
    :class:`~general_manager.measurement.Measurement` fields, and
    :func:`create_normal_resolver` for everything else.
    """
    if field_name.endswith("_list") and safe_issubclass(field_type, GeneralManager):
        return create_list_resolver(
            lambda self, _include_inactive: getattr(self, field_name), field_type
        )
    if safe_issubclass(field_type, Measurement):
        return create_measurement_resolver(field_name)
    return create_normal_resolver(field_name)
