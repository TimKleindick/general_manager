"""
Resolver-construction helpers extracted from ``api/graphql.py``.

These standalone functions build Graphene resolver callables and apply
query modifiers (filtering, pagination, grouping).  They hold no
reference to the ``GraphQL`` class and can therefore be imported freely
without risk of circular imports.
"""

from __future__ import annotations

import json
from typing import Any, Callable, TYPE_CHECKING, TypeVar, cast

import graphene  # type: ignore[import]

from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement.measurement import Measurement
from general_manager.api.graphql_errors import get_read_permission_filter

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo
    from general_manager.bucket.group_bucket import GroupBucket
    from general_manager.permission.base_permission import BasePermission

GeneralManagerT = TypeVar("GeneralManagerT", bound=GeneralManager)


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
    permission_filters = get_read_permission_filter(general_manager_class, info)
    if not permission_filters:
        return queryset

    filtered_queryset: Bucket[GeneralManagerT] = queryset.none()
    for perm_filter, perm_exclude in permission_filters:
        qs_perm = queryset.exclude(**perm_exclude).filter(**perm_filter)
        filtered_queryset = filtered_queryset | qs_perm

    return filtered_queryset


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
        manager_class = getattr(base_queryset, "_manager_class", fallback_manager_class)
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
    if field_name.endswith("_list") and issubclass(field_type, GeneralManager):
        return create_list_resolver(
            lambda self, _include_inactive: getattr(self, field_name), field_type
        )
    if issubclass(field_type, Measurement):
        return create_measurement_resolver(field_name)
    return create_normal_resolver(field_name)
