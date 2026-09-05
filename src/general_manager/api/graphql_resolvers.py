"""
Resolver-construction helpers extracted from ``api/graphql.py``.

These standalone functions build Graphene resolver callables and apply
query modifiers (filtering, grouping, sorting, pagination). They hold no reference to the
``GraphQL`` class and can therefore be imported freely inside the package's
internal GraphQL implementation without circular imports. This module is not a
stable public import path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import (
    Awaitable,
    Generic,
    TYPE_CHECKING,
    TypeVar,
    TypedDict,
    cast,
)

from graphql import GraphQLError, OperationType
from graphql.language.ast import FieldNode, FragmentSpreadNode, InlineFragmentNode

from general_manager.logging import get_logger
from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.group_bucket import GroupBucket
from general_manager.bucket.request_bucket import RequestBucket
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement.measurement import Measurement
from general_manager.api.graphql_errors import get_read_permission_filter
from general_manager.api.graphql_relations import (
    get_graphql_manager_registry,
    resolve_general_manager_type,
)
from general_manager.api.graphql_prefetch import (
    collect_selected_graphql_property_names,
    plan_dependency_cache_prefetches,
    prefetch_dependency_cache_hits,
)
from general_manager.permission.graphql_capabilities import (
    get_capability_context,
    get_graphql_capabilities,
)
from general_manager.api.graphql_ordering import (
    GraphQLOrderingInputError,
    order_by_to_sort_terms,
)
from general_manager.utils.filter_parser import UnknownInputFieldError
from general_manager.utils.type_checks import safe_issubclass

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo
    from general_manager.permission.base_permission import (
        BasePermission,
        ReadPermissionPlan,
    )

GeneralManagerT = TypeVar("GeneralManagerT", bound=GeneralManager)
ResolverValueT = TypeVar("ResolverValueT")
GraphQLFilterInput = Mapping[str, object] | str | None
GraphQLFilterMapping = dict[str, object]
NormalizedFilterPlan = dict[str, GraphQLFilterMapping]
FilterNormalizer = Callable[[GraphQLFilterMapping], NormalizedFilterPlan]
ManagerFilterNormalizer = Callable[
    [type[GeneralManager], GraphQLFilterMapping],
    NormalizedFilterPlan,
]
GroupingValidator = Callable[
    [Bucket[GeneralManager], list[str] | None, object, "GraphQLResolveInfo"], None
]
BaseListGetter = Callable[[object, bool], Bucket[GeneralManager] | None]
Resolver = Callable[..., object]
logger = get_logger("api.graphql")


def _ensure_as_of_compatible(value: object) -> None:
    """Validate manager or bucket snapshot compatibility when supported."""
    ensure = getattr(value, "_ensure_as_of_compatible", None)
    if callable(ensure):
        ensure()


class PageInfoPayload(TypedDict):
    total_count: int | None
    page_size: int | None
    current_page: int
    total_pages: int | None


class ListResolverPayload(TypedDict):
    items: object
    pageInfo: PageInfoPayload


class UnsupportedExcludeNoneRelationFilterError(ValueError):
    """Raised when `none` relation filters are used in GraphQL exclude input."""

    def __init__(self) -> None:
        super().__init__(
            "`none` relation filters are not supported inside `exclude` inputs."
        )


@dataclass(frozen=True, slots=True)
class QueryParameterPlan:
    """Normalized filter and exclusion operations for a list query."""

    filters: GraphQLFilterMapping
    excludes: GraphQLFilterMapping
    normalized_excludes: GraphQLFilterMapping


@dataclass(slots=True)
class ReadAuthorizationResult(Generic[GeneralManagerT]):
    """Aggregate outcome from GraphQL read prefiltering and row authorization.

    Counts remain ``None`` when authorization can return a bucket lazily without
    inspecting or counting it. They are populated when a row-level gate actually
    scans candidates; deny-all plans use known zero counts.
    """

    queryset: Bucket[GeneralManagerT]
    candidate_count: int | None
    authorized_count: int | None
    denied_count: int | None
    backend_shape: str
    requires_instance_check: bool
    instance_check_reasons: tuple[str, ...]


@dataclass(slots=True)
class ReadAuthorizationPreparation(Generic[GeneralManagerT]):
    """Permission prefilters retained until the final row gate is applied."""

    queryset: Bucket[GeneralManagerT]
    permission_plan: ReadPermissionPlan
    backend_shape: str
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


def build_query_parameter_plan(
    filter_input: GraphQLFilterInput,
    exclude_input: GraphQLFilterInput,
    filter_normalizer: FilterNormalizer | None = None,
) -> QueryParameterPlan:
    """Parse and normalize filter and exclusion inputs once."""
    filters = parse_input(filter_input)
    excludes = parse_input(exclude_input)
    normalized_excludes: GraphQLFilterMapping = {}

    if filters and filter_normalizer is not None:
        normalized = filter_normalizer(filters)
        filters = normalized["filter"]
        normalized_excludes = normalized["exclude"]

    if excludes and filter_normalizer is not None:
        if contains_none_relation_filter(excludes):
            raise UnsupportedExcludeNoneRelationFilterError
        normalized = filter_normalizer(excludes)
        excludes = normalized["filter"]
        normalized_excludes = {**normalized_excludes, **normalized["exclude"]}

    return QueryParameterPlan(
        filters=filters,
        excludes=excludes,
        normalized_excludes=normalized_excludes,
    )


def partition_calculation_query_plan(
    manager_class: type[GeneralManager],
    plan: QueryParameterPlan,
) -> tuple[QueryParameterPlan, QueryParameterPlan]:
    """Partition calculation predicates into input and computed-property plans.

    Calculation inputs can constrain the combinations generated by a
    calculation bucket. Predicates rooted at GraphQL properties must be
    deferred until those combinations have been materialized. This helper only
    inspects interface metadata and the already-normalized plan; it does not
    instantiate managers or access descriptors. Manager-input ``_id`` aliases
    are classified with their declared input while preserving the original
    lookup for the bucket parser.
    """
    interface = manager_class.Interface
    input_fields = interface.input_fields
    input_names = set(input_fields)
    computed_names = set(interface.get_graph_ql_properties())
    known_names = input_names | computed_names

    def partition_mapping(
        mapping: GraphQLFilterMapping,
    ) -> tuple[GraphQLFilterMapping, GraphQLFilterMapping]:
        input_mapping: GraphQLFilterMapping = {}
        deferred_mapping: GraphQLFilterMapping = {}
        for lookup, value in mapping.items():
            root = lookup.partition("__")[0]
            classified_root = root
            if root not in known_names and root.endswith("_id"):
                alias_root = root.removesuffix("_id")
                alias_input = input_fields.get(alias_root)
                if (
                    alias_input is not None
                    and isinstance(alias_input.type, type)
                    and issubclass(alias_input.type, GeneralManager)
                ):
                    classified_root = alias_root
            if classified_root not in known_names:
                raise UnknownInputFieldError(root)
            if classified_root in input_names:
                input_mapping[lookup] = value
            else:
                deferred_mapping[lookup] = value
        return input_mapping, deferred_mapping

    input_filters, deferred_filters = partition_mapping(plan.filters)
    input_excludes, deferred_excludes = partition_mapping(plan.excludes)
    input_normalized_excludes, deferred_normalized_excludes = partition_mapping(
        plan.normalized_excludes
    )
    return (
        QueryParameterPlan(
            filters=input_filters,
            excludes=input_excludes,
            normalized_excludes=input_normalized_excludes,
        ),
        QueryParameterPlan(
            filters=deferred_filters,
            excludes=deferred_excludes,
            normalized_excludes=deferred_normalized_excludes,
        ),
    )


def apply_sorting(
    queryset: Bucket[GeneralManager] | GroupBucket[GeneralManager],
    order_by: object,
) -> Bucket[GeneralManager] | GroupBucket[GeneralManager]:
    """Sort by typed GraphQL order inputs, leaving empty input unchanged."""
    try:
        terms = order_by_to_sort_terms(order_by)
    except GraphQLOrderingInputError as exc:
        raise GraphQLError(str(exc)) from exc
    if not terms:
        return queryset
    return queryset.sort(*(term.signed_field for term in terms))


def apply_query_parameter_plan(
    queryset: Bucket[GeneralManager],
    plan: QueryParameterPlan,
    order_by: object,
) -> Bucket[GeneralManager]:
    """Apply a normalized query-parameter plan to *queryset*."""
    if plan.filters:
        queryset = queryset.filter(**plan.filters)
    if plan.excludes:
        queryset = queryset.exclude(**plan.excludes)
    if plan.normalized_excludes:
        queryset = queryset.exclude(**plan.normalized_excludes)

    return cast(
        Bucket[GeneralManager],
        apply_sorting(queryset, order_by),
    )


def apply_query_parameters(
    queryset: Bucket[GeneralManager],
    filter_input: GraphQLFilterInput,
    exclude_input: GraphQLFilterInput,
    order_by: object,
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
    Sorting normalizes Graphene enum values and strings into ordered sort-key
    tuples; an empty list performs no sort. Relation ``none`` filters inside
    GraphQL exclude input are rejected
    before normalization when any dictionary key at any depth, including the
    top level, is named ``"none"``, because that relation shape cannot be safely
    inverted. Bucket filter, exclude, and sort errors propagate unchanged.

    Parameters:
        filter_input: Filters to apply, as a mapping or JSON string.
        exclude_input: Exclusions to apply, as a mapping or JSON string.
        order_by: Typed GraphQL ordering inputs with independent directions.

    Returns:
        The queryset after filters, exclusions, and sorting are applied.

    Raises:
        UnsupportedExcludeNoneRelationFilterError: If an exclude input contains a nested relation ``none`` filter.
    """
    plan = build_query_parameter_plan(
        filter_input,
        exclude_input,
        filter_normalizer,
    )
    return apply_query_parameter_plan(queryset, plan, order_by)


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
    queryset without evaluating it to compute counts.
    """
    preparation = prepare_read_authorization(
        queryset,
        general_manager_class,
        info,
    )
    return finalize_read_authorization(
        preparation,
        general_manager_class,
        info,
        source=source,
    )


def finalize_read_authorization(
    preparation: ReadAuthorizationPreparation[GeneralManagerT],
    general_manager_class: type[GeneralManagerT],
    info: GraphQLResolveInfo,
    *,
    source: str,
) -> ReadAuthorizationResult[GeneralManagerT]:
    """Run the row gate for an already-prefiltered authorization preparation."""
    permission_plan = preparation.permission_plan
    if permission_plan.decision == "deny_all":
        return ReadAuthorizationResult(
            queryset=preparation.queryset,
            candidate_count=0,
            authorized_count=0,
            denied_count=0,
            backend_shape=preparation.backend_shape,
            requires_instance_check=False,
            instance_check_reasons=(),
        )

    if permission_plan.decision == "allow_all":
        return ReadAuthorizationResult(
            queryset=preparation.queryset,
            candidate_count=None,
            authorized_count=None,
            denied_count=None,
            backend_shape=preparation.backend_shape,
            requires_instance_check=False,
            instance_check_reasons=(),
        )

    result = filter_queryset_by_read_permission(
        preparation.queryset,
        general_manager_class,
        info,
        requires_instance_check=permission_plan.requires_instance_check,
        instance_check_reasons=preparation.instance_check_reasons,
        backend_shape=preparation.backend_shape,
    )
    if result.requires_instance_check:
        log_read_authorization_summary(
            general_manager_class=general_manager_class,
            source=source,
            result=result,
        )
    return result


def prepare_read_authorization(
    queryset: Bucket[GeneralManagerT],
    general_manager_class: type[GeneralManagerT],
    info: GraphQLResolveInfo,
    *,
    permission_plan: ReadPermissionPlan | None = None,
) -> ReadAuthorizationPreparation[GeneralManagerT]:
    """Apply only permission pushdowns, leaving any row gate for the caller.

    Request-backed resolvers use this seam to compile permission filters into
    their final upstream query before reading response provenance. Callers must
    subsequently pass the preparation through ``finalize_read_authorization``.
    """
    permission_plan = permission_plan or get_read_permission_filter(
        general_manager_class, info
    )
    backend_shape = get_backend_shape(general_manager_class)
    instance_check_reasons = resolve_instance_check_reasons(
        permission_plan,
        backend_shape=backend_shape,
    )

    if permission_plan.decision == "deny_all":
        return ReadAuthorizationPreparation(
            queryset=queryset.none(),
            permission_plan=permission_plan,
            backend_shape=backend_shape,
            instance_check_reasons=(),
        )

    if permission_plan.decision == "allow_all":
        return ReadAuthorizationPreparation(
            queryset=queryset,
            permission_plan=permission_plan,
            backend_shape=backend_shape,
            instance_check_reasons=(),
        )

    filtered_queryset: Bucket[GeneralManagerT] | None = queryset
    if (
        not permission_plan.requires_instance_check
        and len(permission_plan.filters) == 1
        and not permission_plan.filters[0].get("filter")
        and not permission_plan.filters[0].get("exclude")
    ):
        return ReadAuthorizationPreparation(
            queryset=queryset,
            permission_plan=permission_plan,
            backend_shape=backend_shape,
            instance_check_reasons=instance_check_reasons,
        )

    if permission_plan.filters:
        filtered_queryset = None
        for permission_filter in permission_plan.filters:
            filter_dict = permission_filter.get("filter", {})
            exclude_dict = permission_filter.get("exclude", {})
            if not filter_dict and not exclude_dict:
                qs_perm = queryset
            else:
                qs_perm = queryset.filter(**filter_dict).exclude(**exclude_dict)
            filtered_queryset = (
                qs_perm if filtered_queryset is None else filtered_queryset | qs_perm
            )
    assert filtered_queryset is not None

    return ReadAuthorizationPreparation(
        queryset=filtered_queryset,
        permission_plan=permission_plan,
        backend_shape=backend_shape,
        instance_check_reasons=instance_check_reasons,
    )


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
    class, the original queryset is returned without eagerly computing counts.
    Otherwise each candidate is checked with ``can_read_instance()`` and the
    originating bucket reconstructs the exact authorized instance subset using
    its backend-native representation.
    """
    if not requires_instance_check:
        return ReadAuthorizationResult(
            queryset=queryset,
            candidate_count=None,
            authorized_count=None,
            denied_count=None,
            backend_shape=backend_shape,
            requires_instance_check=False,
            instance_check_reasons=instance_check_reasons,
        )

    PermissionClass: type[BasePermission] | None = getattr(
        general_manager_class, "Permission", None
    )
    if PermissionClass is None:
        return ReadAuthorizationResult(
            queryset=queryset,
            candidate_count=None,
            authorized_count=None,
            denied_count=None,
            backend_shape=backend_shape,
            requires_instance_check=False,
            instance_check_reasons=instance_check_reasons,
        )

    authorized_instances: list[GeneralManagerT] = []
    candidate_count = 0
    for instance in queryset:
        candidate_count += 1
        if PermissionClass(instance, info.context.user).can_read_instance():
            authorized_instances.append(instance)

    authorized_queryset = queryset.with_instances(authorized_instances)
    authorized_count = len(authorized_instances)
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

    def __init__(self, field_name: str) -> None:
        super().__init__(f"{field_name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class EffectivePagination:
    """Normalized public pagination arguments and their observable defaults."""

    requested: bool
    page: int
    page_size: int | None


@dataclass(frozen=True, slots=True)
class RequestPaginationProvenance:
    """Upstream page metadata captured before local subset materialization."""

    page: int | None
    page_size: int | None
    total_count: int | None
    is_complete: bool

    @property
    def is_partial(self) -> bool:
        return not self.is_complete


def normalize_pagination(
    page: int | None,
    page_size: int | None,
) -> EffectivePagination:
    """Normalize list pagination once, preserving the unpaginated no-arg mode."""
    requested = page is not None or page_size is not None
    for field_name, value in (("page", page), ("pageSize", page_size)):
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
        ):
            raise InvalidPaginationValueError(field_name)
    if not requested:
        return EffectivePagination(requested=False, page=1, page_size=None)
    return EffectivePagination(
        requested=True,
        page=1 if page is None else page,
        page_size=10 if page_size is None else page_size,
    )


def _request_pagination_provenance(
    queryset: Bucket[GeneralManager] | GroupBucket[GeneralManager],
) -> RequestPaginationProvenance | None:
    """Materialize request response metadata before any local subset operation."""
    if not isinstance(queryset, RequestBucket):
        return None
    return RequestPaginationProvenance(
        page=queryset.upstream_page,
        page_size=queryset.upstream_page_size,
        total_count=queryset.total_count,
        is_complete=queryset.response_is_complete,
    )


def _is_remote_request_bucket(
    queryset: Bucket[GeneralManager] | GroupBucket[GeneralManager],
) -> bool:
    return isinstance(queryset, RequestBucket) and bool(
        getattr(queryset._interface_cls, "supports_upstream_query_controls", False)
    )


def _request_global_operation_error(operation: str) -> GraphQLError:
    return GraphQLError(
        f"Request source cannot apply global {operation} to an incomplete response "
        "without declared upstream support."
    )


def _requested_request_global_operation(
    pagination: EffectivePagination,
    group_by: list[str] | None,
    order_by: object,
) -> str | None:
    """Return the first global Request operation requested by the client."""
    if pagination.requested:
        return "pagination"
    if group_by is not None:
        return "grouping"
    try:
        return "ordering" if order_by_to_sort_terms(order_by) else None
    except GraphQLOrderingInputError as exc:
        raise GraphQLError(str(exc)) from exc


def _forward_remote_request_controls(
    queryset: Bucket[GeneralManager],
    pagination: EffectivePagination,
    order_by: object,
) -> tuple[Bucket[GeneralManager], bool]:
    """Forward supported RemoteManager page/order controls before materialization."""
    if not _is_remote_request_bucket(queryset):
        return queryset, False
    try:
        terms = order_by_to_sort_terms(order_by)
    except GraphQLOrderingInputError as exc:
        raise GraphQLError(str(exc)) from exc
    controls: dict[str, object] = {}
    if pagination.requested:
        controls["page"] = pagination.page
        controls["page_size"] = pagination.page_size
    if terms:
        controls["ordering"] = [term.signed_field for term in terms]
    if not controls:
        return queryset, False
    return queryset.filter(**controls), bool(terms)


def apply_pagination(
    queryset: Bucket[GeneralManager] | GroupBucket[GeneralManager],
    page: int | None,
    page_size: int | None,
    *,
    normalized: EffectivePagination | None = None,
) -> Bucket[GeneralManager] | GroupBucket[GeneralManager]:
    """
    Return a paginated slice of *queryset*.

    Returns the full queryset when neither ``page`` nor ``page_size`` is
    given. Defaults to page 1 / size 10 when only one parameter is provided.
    Zero, negative, boolean, and non-integer controls raise ``ValueError``
    before slicing.
    Already-empty grouped buckets are returned unchanged when pagination is
    requested, preserving their shape without invoking an invalid empty slice.
    The returned object keeps the same bucket/group-bucket shape as the slice
    operation exposes. Slice errors from the bucket implementation propagate
    unchanged.
    """
    effective = normalized or normalize_pagination(page, page_size)
    if effective.requested:
        if isinstance(queryset, GroupBucket) and len(queryset) == 0:
            return queryset
        offset = (effective.page - 1) * cast(int, effective.page_size)
        queryset = cast(
            Bucket[GeneralManager] | GroupBucket[GeneralManager],
            queryset[offset : offset + cast(int, effective.page_size)],
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


def check_read_permission_for_user(
    instance: GeneralManager,
    user: object,
    field_name: str,
) -> bool:
    """
    Return ``True`` if *user* may read *field_name* on *instance*.

    When the manager defines a Permission class, this calls
    ``Permission(instance, user).check_permission("read", field_name)``.
    Managers without a Permission class default to allowing the field read.
    """
    PermissionClass: type[BasePermission] | None = getattr(instance, "Permission", None)
    if PermissionClass:
        return PermissionClass(instance, user).check_permission("read", field_name)
    return True


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
    return check_read_permission_for_user(instance, info.context.user, field_name)


def resolve_with_read_permission(
    instance: GeneralManager,
    info: GraphQLResolveInfo,
    field_name: str,
    value_factory: Callable[[], ResolverValueT],
) -> ResolverValueT | None | Awaitable[ResolverValueT | None]:
    """Resolve a field after checking read permission.

    Only subscription permission evaluation is offloaded to a worker thread;
    query and mutation resolution remains synchronous.
    """
    user = info.context.user
    operation = getattr(getattr(info, "operation", None), "operation", None)
    if operation is not OperationType.SUBSCRIPTION:
        if not check_read_permission_for_user(instance, user, field_name):
            return None
        return value_factory()

    async def resolve_subscription_value() -> ResolverValueT | None:
        allowed = await asyncio.to_thread(
            check_read_permission_for_user,
            instance,
            user,
            field_name,
        )
        if not allowed:
            return None
        return value_factory()

    return resolve_subscription_value()


def can_read_instance_for_user(
    instance: GeneralManager,
    user: object,
) -> bool:
    """
    Return whether *user* may see that *instance* exists.

    When the manager defines a Permission class, this calls
    ``Permission(instance, user).can_read_instance()``. Managers without a
    Permission class default to allowing the instance read.
    """
    PermissionClass: type[BasePermission] | None = getattr(instance, "Permission", None)
    if PermissionClass:
        return PermissionClass(instance, user).can_read_instance()
    return True


def can_read_instance(
    instance: GeneralManager,
    info: GraphQLResolveInfo,
) -> bool:
    """Return whether the request user may see that *instance* exists."""
    return can_read_instance_for_user(instance, info.context.user)


# ---------------------------------------------------------------------------
# Resolver factories
# ---------------------------------------------------------------------------


def measurement_to_graphql_payload(
    value: object,
    target_unit: str | None = None,
) -> dict[str, object] | None:
    """Convert a measurement value to the payload consumed by ``MeasurementType``.

    This helper deliberately performs no manager permission or historical-context
    checks.  Those checks belong to the resolver that owns the value; output
    dataclasses are already materialized values and must not be treated as
    managers.  ``target_unit`` is applied before the magnitude/unit payload is
    built so manager and output resolvers share one conversion implementation.
    """
    if not isinstance(value, Measurement):
        return None
    if target_unit:
        value = value.to(target_unit)
    return {
        "value": value.quantity.magnitude,
        "unit": value.unit,
    }


def resolve_measurement_output(
    value: object,
    target_unit: str | None = None,
) -> object:
    """Recursively convert Measurement values for GraphQL object/list outputs."""
    if value is None or isinstance(value, Measurement):
        return measurement_to_graphql_payload(value, target_unit)
    if isinstance(value, list):
        return [resolve_measurement_output(item, target_unit) for item in value]
    if isinstance(value, tuple):
        return tuple(resolve_measurement_output(item, target_unit) for item in value)
    if isinstance(value, set):
        return [resolve_measurement_output(item, target_unit) for item in value]
    return measurement_to_graphql_payload(value, target_unit)


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
    ) -> object:
        _ensure_as_of_compatible(self)

        def resolve_measurement() -> object:
            return resolve_measurement_output(
                getattr(self, field_name),
                target_unit,
            )

        return resolve_with_read_permission(
            self,
            info,
            field_name,
            resolve_measurement,
        )

    return resolver


def create_normal_resolver(field_name: str) -> Resolver:
    """
    Return a resolver for a scalar (non-list, non-Measurement) field.

    The generated resolver returns ``None`` when field read permission is denied;
    otherwise it returns ``getattr(self, field_name)``.
    """

    def resolver(self: GeneralManager, info: GraphQLResolveInfo) -> object:
        _ensure_as_of_compatible(self)
        return resolve_with_read_permission(
            self,
            info,
            field_name,
            lambda: getattr(self, field_name),
        )

    return resolver


def create_list_resolver(
    base_getter: BaseListGetter,
    fallback_manager_class: type[GeneralManager],
    filter_normalizer: ManagerFilterNormalizer | None = None,
    grouping_validator: GroupingValidator | None = None,
) -> Resolver:
    """
    Build a resolver for list fields that applies filters, permissions, and pagination.

    The generated resolver accepts nullable ``filter``, ``exclude``,
    ``order_by``, ``page``, ``page_size``, and ``group_by`` values.
    ``include_inactive`` defaults to ``False``. The generated GraphQL schema
    uses camelCase names such as ``orderBy``, ``pageSize``, ``groupBy``, and
    ``includeInactive``. ``order_by`` accepts ordered typed objects; omitted or
    empty input leaves the existing order unchanged.

    The resolver obtains a base bucket from ``base_getter(self,
    include_inactive)``. Only ``None`` triggers fallback: ``Manager.all()`` when
    ``include_inactive`` is false and ``Manager.filter(include_inactive=True)``
    when it is true. Other falsey bucket-like values are used as returned. It
    then infers the manager class from
    ``base_queryset._manager_class`` when that is a GeneralManager subclass,
    otherwise using ``fallback_manager_class``. That manager class drives
    permission checks, filter normalization, dependency prefetching, and
    capability warmups. For non-calculation managers, the resolver applies
    permission prefilters and the permission row gate before all user-supplied
    query predicates. For calculation managers, it applies input predicates
    before authorization to limit generated candidates, fences that exact
    candidate subset before permission prefilters, then applies deferred
    computed-property predicates afterward. Permission constraints can only
    narrow the user-selected input domain. Grouping, sorting, and pagination
    remain after those predicate phases. Sorting therefore applies to records
    when grouping is omitted and to grouped manager objects when grouping is
    active. It computes ``total_count`` after grouping and sorting and before
    pagination. Non-grouped page items are
    materialized to a list; grouped results remain a
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
        reports effective pagination values: supplied single controls default
        the missing coordinate to page 1 / size 10, while ordinary no-argument
        lists remain unpaginated with ``page_size=None``. ``total_pages`` is
        zero for an exact empty result and ``None`` when the source total is
        incomplete. Invalid pagination values raise
        ``InvalidPaginationValueError`` before slicing.
    """

    def resolver(
        self: GeneralManager,
        info: GraphQLResolveInfo,
        filter: GraphQLFilterInput = None,
        exclude: GraphQLFilterInput = None,
        order_by: object = None,
        page: int | None = None,
        page_size: int | None = None,
        group_by: list[str] | None = None,
        include_inactive: bool = False,
    ) -> ListResolverPayload:
        _ensure_as_of_compatible(self)
        base_queryset = base_getter(self, include_inactive)
        if base_queryset is None:
            if include_inactive:
                base_queryset = fallback_manager_class.filter(include_inactive=True)
            else:
                base_queryset = fallback_manager_class.all()
        _ensure_as_of_compatible(base_queryset)
        manager_class = getattr(base_queryset, "_manager_class", None)
        if not (
            isinstance(manager_class, type)
            and issubclass(manager_class, GeneralManager)
        ):
            manager_class = fallback_manager_class
        bound_filter_normalizer = None
        if filter_normalizer is not None:

            def bound_filter_normalizer(
                filters: GraphQLFilterMapping,
            ) -> NormalizedFilterPlan:
                return filter_normalizer(manager_class, filters)

        plan = build_query_parameter_plan(
            filter,
            exclude,
            filter_normalizer=bound_filter_normalizer,
        )
        effective_pagination = normalize_pagination(page, page_size)
        from general_manager.interface import CalculationInterface

        interface = getattr(manager_class, "Interface", None)
        is_request_source = isinstance(base_queryset, RequestBucket)
        remote_ordering_forwarded = False
        request_provenance: RequestPaginationProvenance | None = None
        authorization_result: ReadAuthorizationResult[GeneralManager]
        if isinstance(interface, type) and issubclass(interface, CalculationInterface):
            input_plan, deferred_plan = partition_calculation_query_plan(
                manager_class,
                plan,
            )
            input_queryset = apply_query_parameter_plan(
                base_queryset,
                input_plan,
                None,
            )
            if (
                input_plan.filters
                or input_plan.excludes
                or input_plan.normalized_excludes
            ):
                input_queryset = input_queryset.with_instances(input_queryset)
            authorization_result = apply_read_authorization(
                input_queryset,
                manager_class,
                info,
                source="list",
            )
            qs = authorization_result.queryset
            qs = apply_query_parameter_plan(qs, deferred_plan, None)
        elif is_request_source:
            permission_plan = get_read_permission_filter(manager_class, info)
            if permission_plan.decision == "deny_all":
                authorization_result = finalize_read_authorization(
                    prepare_read_authorization(
                        base_queryset,
                        manager_class,
                        info,
                        permission_plan=permission_plan,
                    ),
                    manager_class,
                    info,
                    source="list",
                )
                qs = authorization_result.queryset
            else:
                unsupported_operation = _requested_request_global_operation(
                    effective_pagination,
                    group_by,
                    order_by,
                )
                if (
                    len(permission_plan.filters) > 1
                    and unsupported_operation is not None
                ):
                    raise _request_global_operation_error(unsupported_operation)
                planned_source = apply_query_parameter_plan(base_queryset, plan, None)
                authorization_preparation = prepare_read_authorization(
                    planned_source,
                    manager_class,
                    info,
                    permission_plan=permission_plan,
                )
                planned_source = authorization_preparation.queryset
                if isinstance(planned_source, RequestBucket):
                    planned_source, remote_ordering_forwarded = (
                        _forward_remote_request_controls(
                            planned_source,
                            effective_pagination,
                            order_by,
                        )
                    )
                    authorization_preparation.queryset = planned_source
                    request_provenance = _request_pagination_provenance(planned_source)
                else:
                    request_provenance = RequestPaginationProvenance(
                        page=None,
                        page_size=None,
                        total_count=None,
                        is_complete=False,
                    )
                assert request_provenance is not None
                if request_provenance.is_partial:
                    if effective_pagination.requested and (
                        request_provenance.page != effective_pagination.page
                        or request_provenance.page_size
                        != effective_pagination.page_size
                    ):
                        raise _request_global_operation_error("pagination")
                    if group_by is not None:
                        raise _request_global_operation_error("grouping")
                    if order_by is not None and not remote_ordering_forwarded:
                        try:
                            has_ordering = bool(order_by_to_sort_terms(order_by))
                        except GraphQLOrderingInputError as exc:
                            raise GraphQLError(str(exc)) from exc
                        if has_ordering:
                            raise _request_global_operation_error("ordering")
                authorization_result = finalize_read_authorization(
                    authorization_preparation,
                    manager_class,
                    info,
                    source="list",
                )
                qs = authorization_result.queryset
        else:
            authorization_result = apply_read_authorization(
                base_queryset,
                manager_class,
                info,
                source="list",
            )
            qs = authorization_result.queryset
            qs = apply_query_parameter_plan(qs, plan, None)
        if grouping_validator is not None:
            grouping_validator(qs, group_by, order_by, info)
        qs_grouped = apply_grouping(qs, group_by)
        qs_sorted = apply_sorting(
            qs_grouped,
            None if remote_ordering_forwarded else order_by,
        )

        if request_provenance is None or request_provenance.is_complete:
            total_count: int | None = len(qs_sorted)
        elif authorization_result.requires_instance_check:
            total_count = None
        else:
            total_count = request_provenance.total_count

        metadata_pagination = effective_pagination
        if (
            not effective_pagination.requested
            and request_provenance is not None
            and request_provenance.page is not None
        ):
            metadata_pagination = EffectivePagination(
                requested=True,
                page=request_provenance.page,
                page_size=request_provenance.page_size,
            )

        upstream_page_matches = (
            request_provenance is not None
            and request_provenance.page == metadata_pagination.page
            and request_provenance.page_size == metadata_pagination.page_size
            and metadata_pagination.requested
            and group_by is None
        )
        qs_paginated = (
            qs_sorted
            if upstream_page_matches
            else apply_pagination(
                qs_sorted,
                page,
                page_size,
                normalized=metadata_pagination,
            )
        )
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
            "page_size": metadata_pagination.page_size,
            "current_page": metadata_pagination.page,
            "total_pages": (
                None
                if total_count is None
                else 0
                if total_count == 0
                else (
                    (total_count + metadata_pagination.page_size - 1)
                    // metadata_pagination.page_size
                    if metadata_pagination.page_size is not None
                    else 1
                )
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
    manager_field_type = resolve_general_manager_type(
        field_type,
        get_graphql_manager_registry(),
    )
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
