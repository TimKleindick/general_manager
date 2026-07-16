"""
Resolver-construction helpers extracted from ``api/graphql.py``.

These standalone functions build Graphene resolver callables and apply
query modifiers (filtering, pagination, grouping). They hold no reference to the
``GraphQL`` class and can therefore be imported freely inside the package's
internal GraphQL implementation without circular imports. This module is not a
stable public import path.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Generic, TYPE_CHECKING, TypeVar, TypedDict, cast

import graphene
from graphql.language.ast import FieldNode, FragmentSpreadNode, InlineFragmentNode

from general_manager.logging import get_logger
from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.group_bucket import GroupBucket
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement.measurement import Measurement
from general_manager.api.graphql_errors import get_read_permission_filter
from general_manager.api.graphql_relations import resolve_general_manager_type
from general_manager.api.graphql_prefetch import (
    collect_selected_graphql_property_names,
    plan_dependency_cache_prefetches,
    prefetch_dependency_cache_hits,
)
from general_manager.permission.graphql_capabilities import (
    get_capability_context,
    get_graphql_capabilities,
)
from general_manager.utils.type_checks import safe_issubclass

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo
    from general_manager.permission.base_permission import (
        BasePermission,
        ReadPermissionPlan,
    )

GeneralManagerT = TypeVar("GeneralManagerT", bound=GeneralManager)
GraphQLFilterInput = Mapping[str, object] | str | None
GraphQLFilterMapping = dict[str, object]
NormalizedFilterPlan = dict[str, GraphQLFilterMapping]
FilterNormalizer = Callable[[GraphQLFilterMapping], NormalizedFilterPlan]
ManagerFilterNormalizer = Callable[
    [type[GeneralManager], GraphQLFilterMapping],
    NormalizedFilterPlan,
]
BaseListGetter = Callable[[object, bool], Bucket[GeneralManager] | None]
Resolver = Callable[..., object]
logger = get_logger("api.graphql")


class PageInfoPayload(TypedDict):
    total_count: int
    page_size: int | None
    current_page: int
    total_pages: int


class ListResolverPayload(TypedDict):
    items: object
    pageInfo: PageInfoPayload


class UnsupportedExcludeNoneRelationFilterError(ValueError):
    """Raised when `none` relation filters are used in GraphQL exclude input."""

    def __init__(self) -> None:
        super().__init__(
            "`none` relation filters are not supported inside `exclude` inputs."
        )


@dataclass(slots=True)
class ReadAuthorizationResult(Generic[GeneralManagerT]):
    """Aggregate outcome from GraphQL read prefiltering and row authorization."""

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


def parse_input(input_val: GraphQLFilterInput) -> GraphQLFilterMapping:
    """
    Normalise a filter or exclude input into a plain dictionary.

    Accepts a mapping, a JSON-encoded object string, or ``None``. Returns a
    plain dict for mapping inputs and object strings. Returns an empty dict for
    ``None``, unparseable JSON strings, or decoded non-object JSON.
    """
    if input_val is None:
        return {}
    if isinstance(input_val, str):
        try:
            decoded = json.loads(input_val)
        except (json.JSONDecodeError, ValueError):
            return {}
        if isinstance(decoded, dict):
            return cast(GraphQLFilterMapping, decoded)
        return {}
    return dict(input_val)


def contains_none_relation_filter(input_val: object) -> bool:
    """Return True when a nested relation filter contains a ``none`` operator."""
    if isinstance(input_val, dict):
        if "none" in input_val:
            return True
        return any(contains_none_relation_filter(value) for value in input_val.values())
    if isinstance(input_val, list):
        return any(contains_none_relation_filter(value) for value in input_val)
    return False


# ---------------------------------------------------------------------------
# Queryset modifiers
# ---------------------------------------------------------------------------


def apply_query_parameters(
    queryset: Bucket[GeneralManager],
    filter_input: GraphQLFilterInput,
    exclude_input: GraphQLFilterInput,
    sort_by: graphene.Enum | None,
    reverse: bool,
    *,
    filter_normalizer: FilterNormalizer | None = None,
) -> Bucket[GeneralManager]:
    """
    Apply filtering, exclusion, and sorting to *queryset*.

    Filter and exclude inputs may be mappings, JSON object strings, malformed
    strings, or ``None``. Malformed and non-object JSON become empty mappings.
    When a filter normalizer is supplied, its ``"filter"`` mapping is applied as
    a queryset filter and its ``"exclude"`` mapping is saved for a later
    queryset exclude. Explicit ``filter`` input is normalized and applied before
    explicit ``exclude`` input. Explicit ``exclude`` input is normalized next,
    then explicit excludes are applied before the accumulated normalized
    excludes. Normalizers must return both ``"filter"`` and ``"exclude"`` keys.
    Missing normalizer keys propagate the resulting ``KeyError``.
    Sorting uses ``sort_by.value`` when present and otherwise uses ``sort_by``
    itself. Relation ``none`` filters inside GraphQL exclude input are rejected
    before normalization when any dictionary key at any depth, including the
    top level, is named ``"none"``, because that relation shape cannot be safely
    inverted. Bucket filter, exclude, and sort errors propagate unchanged.

    Parameters:
        filter_input: Filters to apply, as a mapping or JSON string.
        exclude_input: Exclusions to apply, as a mapping or JSON string.
        sort_by: Field to sort by (Graphene Enum value).
        reverse: If ``True``, reverse the sort order.

    Returns:
        The queryset after filters, exclusions, and sorting are applied.

    Raises:
        UnsupportedExcludeNoneRelationFilterError: If an exclude input contains a nested relation ``none`` filter.
    """
    normalized_excludes: GraphQLFilterMapping = {}

    filters = parse_input(filter_input)
    if filters and filter_normalizer is not None:
        normalized = filter_normalizer(filters)
        filters = normalized["filter"]
        normalized_excludes = normalized["exclude"]
    if filters:
        queryset = queryset.filter(**filters)

    excludes = parse_input(exclude_input)
    if excludes and filter_normalizer is not None:
        if contains_none_relation_filter(excludes):
            raise UnsupportedExcludeNoneRelationFilterError
        normalized = filter_normalizer(excludes)
        excludes = normalized["filter"]
        normalized_excludes = {**normalized_excludes, **normalized["exclude"]}
    if excludes:
        queryset = queryset.exclude(**excludes)
    if normalized_excludes:
        queryset = queryset.exclude(**normalized_excludes)

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

    This is the list-resolver convenience wrapper around
    ``apply_read_authorization(..., source="list")``. It returns only the
    authorized queryset and discards the count/logging metadata.

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
) -> ReadAuthorizationResult[GeneralManagerT]:
    """
    Apply read prefilters plus the final row gate and emit aggregate observability.

    Permission constraints are evaluated as alternatives against the original
    queryset and unioned. If the permission plan requires instance checks, the
    final row gate runs and an aggregate log event is emitted only if the final
    authorization result still requires instance checks.
    Unrestricted plans that do not require instance checks return the original
    queryset with matching candidate and authorized counts.
    """
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
) -> ReadAuthorizationResult[GeneralManagerT]:
    """
    Apply final row-level read authorization to a bucket.

    When an instance gate is not required, or the manager has no Permission
    class, the original queryset is returned with all candidates authorized.
    Otherwise each candidate is checked with ``can_read_instance()``. Authorized
    rows with an ``identification["id"]`` are rebuilt through an ``id__in``
    filter; authorized rows without an id are unioned back as concrete manager
    instances.
    """
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

    authorized_ids: list[object] = []
    authorized_instances: list[GeneralManagerT] = []
    candidate_count = 0
    for instance in queryset:
        candidate_count += 1
        if PermissionClass(instance, info.context.user).can_read_instance():
            identification = cast(Mapping[str, object], instance.identification)
            instance_id = identification.get("id")
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
    """
    Classify the manager's interface into a stable backend-shape label.

    Labels are ``database`` for DatabaseInterface, ``read_only`` for
    ReadOnlyInterface, ``existing_model`` for ExistingModelInterface, ``request``
    for RequestInterface, ``calculation`` for CalculationInterface, ``custom``
    for any other interface class, and ``unknown`` when the manager has no
    class-shaped Interface.
    """
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
    """
    Return stable reason labels for why the final instance gate was required.

    Plan reasons are deduplicated and sorted. When an instance check is required
    for a non-database backend and the plan gave no reason, ``no_prefilter_backend``
    is added.
    """
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
    general_manager_class: type[GeneralManagerT],
    source: str,
    result: ReadAuthorizationResult[GeneralManagerT],
) -> None:
    """
    Emit one aggregate structured log event for a read-authorization pass.

    The log context contains ``source``, ``manager``, ``backend_shape``,
    candidate/authorized/denied counts, ``requires_instance_check``, and
    ``instance_check_reasons`` as a list.
    """
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


class InvalidPaginationValueError(ValueError):
    """Raised when pagination arguments cannot produce a valid page."""

    def __init__(self) -> None:
        super().__init__("pagination values must be non-negative")


def apply_pagination(
    queryset: Bucket[GeneralManager] | GroupBucket[GeneralManager],
    page: int | None,
    page_size: int | None,
) -> Bucket[GeneralManager] | GroupBucket[GeneralManager]:
    """
    Return a paginated slice of *queryset*.

    Returns the full queryset when neither ``page`` nor ``page_size`` is
    given. Defaults to page 1 / size 10 when only one parameter is provided.
    Negative ``page`` or ``page_size`` values raise ``ValueError`` before
    slicing. Falsey explicit values such as ``page=0`` or ``page_size=0`` also
    fall back through those defaults for slicing.
    The returned object keeps the same bucket/group-bucket shape as the slice
    operation exposes. Slice errors from the bucket implementation propagate
    unchanged.
    """
    if page is not None and page < 0:
        raise InvalidPaginationValueError
    if page_size is not None and page_size < 0:
        raise InvalidPaginationValueError
    if page is not None or page_size is not None:
        page = page or 1
        page_size = page_size or 10
        offset = (page - 1) * page_size
        queryset = cast(
            Bucket[GeneralManager] | GroupBucket[GeneralManager],
            queryset[offset : offset + page_size],
        )
    return queryset


def apply_grouping(
    queryset: Bucket[GeneralManager],
    group_by: list[str] | None,
) -> Bucket[GeneralManager] | GroupBucket[GeneralManager]:
    """
    Group *queryset* by the specified fields.

    ``group_by=None`` returns the original queryset unchanged.
    ``group_by=[""]`` calls ``queryset.group_by()`` so the bucket chooses its
    default grouping keys. Every other list, including an empty list, is
    expanded into ``queryset.group_by(*group_by)``. Validation errors from the
    bucket implementation propagate unchanged.
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
    """
    Return ``True`` if the request user may read *field_name* on *instance*.

    When the manager defines a Permission class, this calls
    ``Permission(instance, user).check_permission("read", field_name)``.
    Managers without a Permission class default to allowing the field read.
    """
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
    """
    Return whether the request user may see that *instance* exists.

    When the manager defines a Permission class, this calls
    ``Permission(instance, user).can_read_instance()``. Managers without a
    Permission class default to allowing the instance read.
    """
    PermissionClass: type[BasePermission] | None = getattr(instance, "Permission", None)
    if PermissionClass:
        return PermissionClass(instance, info.context.user).can_read_instance()
    return True


# ---------------------------------------------------------------------------
# Resolver factories
# ---------------------------------------------------------------------------


def create_measurement_resolver(field_name: str) -> Resolver:
    """
    Return a resolver for a :class:`~general_manager.measurement.Measurement` field.

    The resolver checks read permission, then returns a ``{"value": …,
    "unit": …}`` dict (with optional unit conversion via ``target_unit``). It
    returns ``None`` when read permission is denied or the resolved attribute is
    not a Measurement instance.
    """

    def resolver(
        self: GeneralManager,
        info: GraphQLResolveInfo,
        target_unit: str | None = None,
    ) -> dict[str, object] | None:
        if not check_read_permission(self, info, field_name):
            return None
        result = getattr(self, field_name)
        if not isinstance(result, Measurement):
            return None
        if target_unit:
            result = result.to(target_unit)
        return {
            "value": result.quantity.magnitude,
            "unit": result.unit,
        }

    return resolver


def create_normal_resolver(field_name: str) -> Resolver:
    """
    Return a resolver for a scalar (non-list, non-Measurement) field.

    The generated resolver returns ``None`` when field read permission is denied;
    otherwise it returns ``getattr(self, field_name)``.
    """

    def resolver(self: GeneralManager, info: GraphQLResolveInfo) -> object:
        if not check_read_permission(self, info, field_name):
            return None
        return getattr(self, field_name)

    return resolver


def create_list_resolver(
    base_getter: BaseListGetter,
    fallback_manager_class: type[GeneralManager],
    filter_normalizer: ManagerFilterNormalizer | None = None,
) -> Resolver:
    """
    Build a resolver for list fields that applies filters, permissions, and pagination.

    The generated resolver accepts nullable ``filter``, ``exclude``, ``sort_by``,
    ``page``, ``page_size``, and ``group_by`` values. ``reverse`` and
    ``include_inactive`` default to ``False``. The generated GraphQL schema uses
    camelCase names such as ``sortBy``, ``pageSize``, ``groupBy``, and
    ``includeInactive``. If ``sort_by`` is ``None``, no sorting is attempted even
    when ``reverse`` is true.

    The resolver obtains a base bucket from ``base_getter(self,
    include_inactive)``. Only ``None`` triggers fallback: ``Manager.all()`` when
    ``include_inactive`` is false and ``Manager.filter(include_inactive=True)``
    when it is true. Other falsey bucket-like values are used as returned. It
    then infers the manager class from
    ``base_queryset._manager_class`` when that is a GeneralManager subclass,
    otherwise using ``fallback_manager_class``. That manager class drives
    permission checks, filter normalization, dependency prefetching, and
    capability warmups. The resolver applies permission prefilters and the
    permission row gate before user-supplied query arguments,
    then explicit filters, normalized filter-side excludes, explicit excludes,
    normalized exclude-side excludes, sorting, optional grouping, and pagination.
    It computes ``total_count`` after grouping and before pagination. Non-grouped
    page items are materialized to a list; grouped results remain a
    ``GroupBucket`` and are returned as the Python-side ``items`` value. When
    grouping is active, pagination slices the group bucket before it is returned.
    Dependency-cache prefetch runs only for materialized item lists when the
    GraphQL selection includes dependency-cache-backed properties. Capability
    warmup runs only for materialized item lists when ``items.capabilities`` is
    selected and the manager declares GraphQL capabilities. Filtering, grouping,
    pagination, permission, prefetch, and capability errors propagate unchanged,
    except for unsupported ``none`` relation filters inside ``exclude`` input,
    which raise ``UnsupportedExcludeNoneRelationFilterError``.

    Parameters:
        base_getter: Callable returning the base queryset; receives the
            parent object and the ``include_inactive`` flag.
        fallback_manager_class: Manager used when *base_getter* returns
            ``None``.

    Returns:
        A Graphene-compatible resolver function returning ``{"items": ..., "pageInfo": ...}``.
        ``pageInfo`` contains ``total_count``, ``page_size``,
        ``current_page``, and ``total_pages`` using the Python-side field names;
        Graphene exposes them as camelCase in the GraphQL schema. ``current_page``
        is ``page or 1``. ``page_size`` reports the original ``page_size``
        argument, not the effective slicing default. ``total_pages`` is computed
        from a truthy original ``page_size`` and otherwise reported as ``1``.
        Negative pagination values raise ``InvalidPaginationValueError`` before
        slicing.
    """

    def resolver(
        self: GeneralManager,
        info: GraphQLResolveInfo,
        filter: GraphQLFilterInput = None,
        exclude: GraphQLFilterInput = None,
        sort_by: graphene.Enum | None = None,
        reverse: bool = False,
        page: int | None = None,
        page_size: int | None = None,
        group_by: list[str] | None = None,
        include_inactive: bool = False,
    ) -> ListResolverPayload:
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
        bound_filter_normalizer = None
        if filter_normalizer is not None:

            def bound_filter_normalizer(
                filters: GraphQLFilterMapping,
            ) -> NormalizedFilterPlan:
                return filter_normalizer(manager_class, filters)

        qs = apply_query_parameters(
            qs,
            filter,
            exclude,
            sort_by,
            reverse,
            filter_normalizer=bound_filter_normalizer,
        )
        qs_grouped = apply_grouping(qs, group_by)

        total_count = len(qs_grouped)

        qs_paginated = apply_pagination(qs_grouped, page, page_size)
        items: object
        if hasattr(qs_paginated, "groups"):
            items = qs_paginated
        else:
            items = list(cast(Iterable[GeneralManager], qs_paginated))
        if isinstance(items, list):
            selected_property_names = collect_selected_graphql_property_names(
                info,
                manager_class,
                root_field="items",
            )
            if selected_property_names:
                prefetch_plans = plan_dependency_cache_prefetches(
                    items,
                    manager_class,
                    selected_property_names,
                    can_read_field=lambda instance, property_name: (
                        check_read_permission(
                            instance,
                            info,
                            property_name,
                        )
                    ),
                )
                prefetch_dependency_cache_hits(prefetch_plans)
        if isinstance(items, list) and selection_includes_path(
            info, ("items", "capabilities")
        ):
            capability_declarations = get_graphql_capabilities(manager_class)
            if capability_declarations:
                get_capability_context(info).warm(
                    capability_declarations,
                    items,
                )

        page_info: PageInfoPayload = {
            "total_count": total_count,
            "page_size": page_size,
            "current_page": page or 1,
            "total_pages": (
                ((total_count + page_size - 1) // page_size) if page_size else 1
            ),
        }
        return {
            "items": items,
            "pageInfo": page_info,
        }

    return resolver


def selection_includes_path(
    info: GraphQLResolveInfo,
    path: tuple[str, ...],
) -> bool:
    """
    Return whether the current field selection includes the nested path.

    Direct fields, inline fragments, and named fragments are traversed. Named
    fragments are guarded by a visited set so cyclic fragment spreads terminate.
    """
    field_nodes = getattr(info, "field_nodes", ())
    return any(
        _selection_set_includes_path(
            getattr(field_node, "selection_set", None),
            path,
            info,
            frozenset(),
        )
        for field_node in field_nodes
    )


def _selection_set_includes_path(
    selection_set: object,
    path: tuple[str, ...],
    info: GraphQLResolveInfo,
    visited: frozenset[str],
) -> bool:
    if selection_set is None or not path:
        return False
    target, *rest = path
    selections = cast(Iterable[object], getattr(selection_set, "selections", ()))
    for selection in selections:
        if isinstance(selection, FieldNode):
            if selection.name.value != target:
                continue
            if not rest:
                return True
            if _selection_set_includes_path(
                selection.selection_set,
                tuple(rest),
                info,
                visited,
            ):
                return True
        elif isinstance(selection, InlineFragmentNode):
            if _selection_set_includes_path(
                selection.selection_set,
                path,
                info,
                visited,
            ):
                return True
        elif isinstance(selection, FragmentSpreadNode):
            fragment_name = selection.name.value
            if fragment_name in visited:
                continue
            fragment = info.fragments.get(fragment_name)
            if fragment and _selection_set_includes_path(
                fragment.selection_set,
                path,
                info,
                visited | frozenset((fragment_name,)),
            ):
                return True
    return False


def create_resolver(
    field_name: str,
    field_type: object,
    filter_normalizer: ManagerFilterNormalizer | None = None,
) -> Resolver:
    """
    Return the appropriate resolver for *field_name* based on *field_type*.

    Dispatches to :func:`create_list_resolver` for ``GeneralManager`` list
    fields, :func:`create_measurement_resolver` for
    :class:`~general_manager.measurement.Measurement` fields, and
    :func:`create_normal_resolver` for everything else.
    """
    manager_field_type = resolve_general_manager_type(field_type)
    if field_name.endswith("_list") and manager_field_type is not None:
        return create_list_resolver(
            lambda self, _include_inactive: cast(
                Bucket[GeneralManager],
                getattr(self, field_name),
            ),
            manager_field_type,
            filter_normalizer,
        )
    if safe_issubclass(field_type, Measurement):
        return create_measurement_resolver(field_name)
    return create_normal_resolver(field_name)
