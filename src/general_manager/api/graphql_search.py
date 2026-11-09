"""
Standalone search-subsystem helpers extracted from ``api/graphql.py``.

These functions hold no reference to the ``GraphQL`` class and can be
imported freely without creating circular imports.  The ``GraphQL`` class
exposes them as thin classmethods / staticmethods for backward compatibility.
"""

from __future__ import annotations
from dataclasses import replace

import json
from collections.abc import Callable, Generator, Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import (
    Any,
    TYPE_CHECKING,
    Literal,
    cast,
    get_args,
)
from uuid import UUID

import graphene
from graphql import GraphQLError, GraphQLID
from django.core.exceptions import FieldDoesNotExist
from django.db import models

from general_manager.conf import get_setting
from general_manager.api.graphql_identifiers import property_identifier_type
from general_manager.api.graphql_ordering import (
    GraphQLOrderingInputError,
    create_ordering_types,
    graphql_name_for_path,
    order_by_to_sort_terms,
)
from general_manager.bucket._ordering import SortTerm, sort_items
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.measurement.measurement import Measurement
from general_manager.utils.type_checks import safe_issubclass
from general_manager.search.backend_registry import get_search_backend
from general_manager.search.backend import SearchHit
from general_manager.search.utils import normalize_identification
from general_manager.search.identity import SearchFilters, build_search_filters
from general_manager.search.authorization import analyze_search_projection
from general_manager.search.config import IndexConfig, SearchConfigSpec
from general_manager.search.registry import (
    get_search_config,
    get_type_label,
    validate_filter_keys,
)
from general_manager.utils.filter_parser import create_filter_function
from general_manager.utils.executable_contract import (
    executable_matches,
    executable_signature,
)
from general_manager.api.graphql_errors import (
    MeasurementScalar,
    PageInfo,
    map_field_to_graphene_base_type,
    get_read_permission_filter,
)
from general_manager.api.graphql_relations import (
    get_graphql_manager_registry,
    resolve_general_manager_type,
)
from general_manager.api.graphql_resolvers import (
    can_read_instance,
    get_backend_shape,
    resolve_instance_check_reasons,
)

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo
    from general_manager.permission.base_permission import (
        PermissionConstraint,
        ReadPermissionPlan,
    )

logger = get_logger("api.graphql_search")

GraphQLFilterMapping = dict[str, object]
GraphQLSearchFilterItem = dict[str, object]
GraphQLSearchFilterInput = (
    GraphQLFilterMapping | str | list[GraphQLSearchFilterItem] | None
)
NormalizedFilterPlan = dict[str, GraphQLFilterMapping]
PermissionBackendFilters = list[GraphQLFilterMapping] | GraphQLFilterMapping | None
GrapheneFieldType = object
GrapheneReadMapper = Callable[
    [type, str, Mapping[str, object] | None],
    GrapheneFieldType,
]
SearchResolverPayload = dict[str, object]
SearchHitEntry = tuple[float | None, SearchHit, GeneralManager]
SearchTotalMode = Literal["exact", "bounded"]
_BAD_USER_INPUT_CODE = "BAD_USER_INPUT"
_PAGE_MUST_BE_POSITIVE = "page must be a positive integer."
_PAGE_SIZE_MUST_BE_POSITIVE = "pageSize must be a positive integer."
DEFAULT_GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT = 1000
SEARCH_SOURCE_AUTHORIZATION_LIMIT_ERROR = (
    "Search source authorization candidate limit exceeded."
)
SEARCH_TOTAL_MODE_ERROR = "must be one of: exact, bounded."
SEARCH_TOTAL_SCAN_LIMIT_ERROR = (
    "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT must be a positive integer."
)
_MALFORMED_NATIVE_ID = object()

_SEARCH_SCAN_LIMIT_EXCEEDED = (
    "Exact search total cannot be established within the server scan limit; "
    "narrow the query. Partial search pages cannot safely exclude unreadable content."
)
_SEARCH_BACKEND_SCAN_LIMIT_EXCEEDED = (
    "Search backend exceeded the requested scan limit."
)
_SEARCH_STORED_DATA_REQUIRED = (
    "Search backend must return complete stored data to authorize search projections."
)


class SearchOrderingError(GraphQLError):
    """GraphQL errors raised while validating generated search ordering."""

    @classmethod
    def enum_alias_collision(
        cls,
        graphql_name: str,
        existing: str,
        field: str,
    ) -> "SearchOrderingError":
        return cls(
            f"Search ordering enum member {graphql_name!r} maps to both "
            f"{existing!r} and {field!r}."
        )

    @classmethod
    def unsupported_field(
        cls,
        fields: str,
        manager_name: str,
        index_name: str,
    ) -> "SearchOrderingError":
        return cls(
            f"Ordering field(s) {fields} are not sortable for {manager_name} "
            f"on index {index_name!r}.",
            extensions={"code": _BAD_USER_INPUT_CODE},
        )


def _bad_user_input_error(message: str) -> GraphQLError:
    """Build a GraphQL user-input error with the shared extension code."""
    return GraphQLError(message, extensions={"code": _BAD_USER_INPUT_CODE})


def _resolve_search_pagination(
    page: int | None,
    page_size: int | None,
) -> tuple[int, int]:
    """
    Resolve omitted search pagination arguments and reject non-positive values.
    """
    current_page = 1 if page is None else page
    limit = 10 if page_size is None else page_size
    if current_page <= 0:
        raise GraphQLError(
            _PAGE_MUST_BE_POSITIVE,
            extensions={"code": _BAD_USER_INPUT_CODE},
        )
    if limit <= 0:
        raise GraphQLError(
            _PAGE_SIZE_MUST_BE_POSITIVE,
            extensions={"code": _BAD_USER_INPUT_CODE},
        )
    return current_page, limit


def _invalid_search_total_mode_error(setting_label: str) -> GraphQLError:
    """Build the validation error for an invalid total-mode value."""
    message = f"{setting_label} {SEARCH_TOTAL_MODE_ERROR}"
    return _bad_user_input_error(message)


def normalize_search_total_mode(total_mode: str | None = None) -> SearchTotalMode:
    """Resolve and validate the requested GraphQL search total-count mode."""
    if total_mode is None:
        raw_mode = get_setting("GRAPHQL_SEARCH_TOTAL_MODE", "exact")
        setting_label = 'GENERAL_MANAGER["GRAPHQL_SEARCH_TOTAL_MODE"]'
    else:
        raw_mode = total_mode
        setting_label = "totalMode"

    if not isinstance(raw_mode, str):
        raise _invalid_search_total_mode_error(setting_label)

    normalized = raw_mode.strip().lower()
    if normalized not in {"exact", "bounded"}:
        raise _invalid_search_total_mode_error(setting_label)
    return cast(SearchTotalMode, normalized)


def get_graphql_search_total_scan_limit() -> int:
    """Return the server-owned candidate budget shared by all searched types."""
    raw_limit = get_setting(
        "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT",
        DEFAULT_GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT,
    )
    if not isinstance(raw_limit, int) or isinstance(raw_limit, bool) or raw_limit <= 0:
        raise _bad_user_input_error(SEARCH_TOTAL_SCAN_LIMIT_ERROR)
    return raw_limit


def sort_search_hit_entries(
    entries: list[SearchHitEntry],
    *,
    terms: tuple[SortTerm, ...],
) -> None:
    """Sort retained search entries using GraphQL search response semantics."""
    if terms:
        entries[:] = sort_items(
            entries,
            terms,
            value_for=lambda entry, field: entry[1].data.get(field)
            if entry[1].data
            else None,
            identity_for=lambda entry: (
                entry[1].type,
                entry[1].identification,
                entry[1].index,
                entry[1].id,
            ),
        )
    else:
        entries.sort(key=lambda item: item[0] or 0, reverse=True)


def trim_search_hit_entries_to_window(
    entries: list[SearchHitEntry],
    *,
    requested_count: int,
    terms: tuple[SortTerm, ...],
) -> None:
    """Keep only entries that can still appear in the requested global page."""
    if len(entries) <= requested_count:
        return
    sort_search_hit_entries(entries, terms=terms)
    del entries[requested_count:]


# ---------------------------------------------------------------------------
# Filter normalisation
# ---------------------------------------------------------------------------


def parse_search_filters(
    filters: GraphQLSearchFilterInput,
) -> GraphQLFilterMapping:
    """
    Normalise search filters supplied as a dict, JSON string, or list of filter
    objects into a single lookup dict.

    Mapping inputs are returned unchanged. JSON strings may decode to either a
    mapping or the same list-of-filter-object shape accepted directly. In list
    entries, `values` takes precedence over `value`; when `values` is present
    and `op` is blank, `op` becomes `in`. The lookup key is `field` when `op` is
    blank and `field__op` otherwise. This helper does not validate operator
    names; backend validation or filter evaluation handles unsupported lookups.

    Parameters:
        filters: Filters to normalise. Accepts:
            - dict: returned as-is.
            - JSON string: parsed to a dict or list; invalid JSON → empty dict.
            - list of dicts: each dict should contain ``"field"`` and optionally
              ``"op"``, ``"value"``, or ``"values"``.

    Returns:
        Mapping of filter lookup strings to their values.

    Raises:
        No search-specific errors. Invalid JSON strings, decoded non-object
        values, and malformed list entries are ignored and normalize to an empty
        or partially populated mapping.
    """
    parsed: object = filters
    if isinstance(filters, str):
        try:
            parsed = json.loads(filters)
        except (json.JSONDecodeError, ValueError):
            parsed = None
    if isinstance(parsed, list):
        merged: GraphQLFilterMapping = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            field = item.get("field")
            if not field:
                continue
            op = (item.get("op") or "").strip()
            values = item.get("values")
            value = item.get("value")
            if values is not None and op == "":
                op = "in"
            key = f"{field}__{op}" if op else field
            merged[key] = values if values is not None else value
        return merged
    if isinstance(parsed, dict):
        return cast(GraphQLFilterMapping, parsed)
    return {}


# ---------------------------------------------------------------------------
# Permission-filter helpers
# ---------------------------------------------------------------------------


def merge_permission_filters(
    filters: GraphQLFilterMapping | None,
    permission_filters: list[PermissionConstraint],
) -> PermissionBackendFilters:
    """
    Combine a base filter with multiple permission-derived filter sets.

    Parameters:
        filters: Base filter to apply to each permission set; treated as empty
            if ``None``.
        permission_filters: Ordered read-permission alternatives. Each optional
            ``"filter"`` mapping is merged into the base filter for the backend
            prefilter stage. Optional ``"exclude"`` mappings are evaluated later
            during per-instance authorization.

    Returns:
        If *permission_filters* is empty, returns *filters* or ``None``.
    Otherwise returns a list of backend filter dicts (one per permission set),
    or ``None`` if the resulting list would be empty. When a user and
    permission mapping use the same lookup key, the backend group retains the
    complete user mapping and omits that permission criterion. The later
    per-instance permission check enforces every permission criterion against
    the hydrated manager. This preserves their conjunction without losing
    either value in the portable mapping format. When *permission_filters* is
    empty, a non-empty *filters* mapping is returned unchanged rather than
    copied. Empty permission constraints,
        ``{"filter": {}}``, and ``{"exclude": {}}`` all produce a copy of the
        base filter for that alternative; exclude-only constraints still rely on
        later per-instance authorization. This helper raises no search-specific
        errors.
    """
    if not permission_filters:
        return filters or None
    groups: list[GraphQLFilterMapping] = []
    for permission_filter in permission_filters:
        permission_mapping = permission_filter.get("filter", {})
        combined = dict(filters or {})
        for key, value in permission_mapping.items():
            combined.setdefault(key, value)
        groups.append(combined)
    return groups or None


def matches_filters(
    instance: GeneralManager,
    filters: GraphQLFilterMapping,
    *,
    empty_is_match: bool = True,
) -> bool:
    """
    Return ``True`` if *instance* satisfies every condition in *filters*.

    Lookup semantics come from ``create_filter_function``: supported final
    lookup segments are ``exact``, ``lt``, ``lte``, ``gt``, ``gte``,
    ``contains``, ``startswith``, ``endswith``, and ``in``. Attribute traversal
    uses ``getattr`` only, missing attributes evaluate to ``False``, string
    contains/starts/ends checks are case-sensitive, incompatible comparisons
    return ``False``, and ``None`` is compared like any other value for exact
    equality.

    Parameters:
        instance: The manager instance to evaluate.
        filters: Mapping of lookup expressions to values.
        empty_is_match: If ``True`` and *filters* is empty, return ``True``.

    Returns:
        ``True`` if all filter conditions pass, ``False`` otherwise.
    """
    if not filters:
        return empty_is_match
    for lookup, value in filters.items():
        func = create_filter_function(lookup, value)
        if not func(instance):
            return False
    return True


def passes_permission_filters(
    instance: GeneralManager,
    info: GraphQLResolveInfo,
    *,
    permission_plan: "ReadPermissionPlan | None" = None,
) -> bool:
    """
    Return ``True`` if the current user may read *instance*.

    Checks all per-manager read-permission filter sets.  Returns ``True`` if
    no permission filters are configured, or if at least one filter passes and
    its corresponding exclude does not match. An unrestricted permission
    alternative such as ``{}``, ``{"filter": {}}``, or ``{"exclude": {}}``
    counts as a matching filter alternative; when the permission plan still
    requires an instance check, ``can_read_instance()`` must also pass.

    Parameters:
        instance: The manager instance to evaluate.
        info: GraphQL resolver info containing the request context / user.
        permission_plan: Optional precomputed read-permission plan. When omitted,
            the plan is resolved for ``instance.__class__`` and ``info``.

    Returns:
        ``True`` when the permission plan allows the instance, otherwise
        ``False``.

    Raises:
        Exceptions from permission plan construction, filter evaluation, and
        per-instance permission checks propagate unchanged.
    """
    if permission_plan is None:
        permission_plan = get_read_permission_filter(instance.__class__, info)
    decision = getattr(permission_plan, "decision", "conditional")
    if decision == "allow_all":
        return True
    if decision == "deny_all":
        return False
    permission_filters = permission_plan.filters
    if not permission_filters:
        return can_read_instance(instance, info)

    for permission_filter in permission_filters:
        perm_filter = permission_filter.get("filter", {})
        perm_exclude = permission_filter.get("exclude", {})
        if matches_filters(instance, perm_filter) and not matches_filters(
            instance, perm_exclude, empty_is_match=False
        ):
            if not permission_plan.requires_instance_check:
                return True
            return can_read_instance(instance, info)
    return False


# ---------------------------------------------------------------------------
# GraphQL type builders
# ---------------------------------------------------------------------------


def create_search_union(
    type_map: dict[str, type[GeneralManager]],
    graphql_type_registry: dict[str, type[graphene.ObjectType]],
) -> type[graphene.Union] | None:
    """
    Build a Graphene Union type uniting the registered GraphQL object types
    for the provided manager classes.

    Parameters:
        type_map: Mapping of manager name → manager class for all searchable
            managers.
        graphql_type_registry: Registry keyed by manager class name and valued
            with the generated Graphene ObjectType for that manager.

    Returns:
        A ``SearchResultUnion`` Graphene Union type, or ``None`` if no
        registered ObjectTypes were found.
    """
    types: list[type[graphene.ObjectType]] = []
    for manager_class in type_map.values():
        gql_type = graphql_type_registry.get(manager_class.__name__)
        if gql_type is not None:
            types.append(gql_type)

    if not types:
        return None

    meta = type("Meta", (), {"types": tuple(types)})

    def resolve_type(
        _cls: type[graphene.Union],
        instance: object,
        _info: GraphQLResolveInfo,
    ) -> type[graphene.ObjectType] | None:
        """Map a GeneralManager instance to its registered GraphQL ObjectType."""
        if isinstance(instance, GeneralManager):
            return graphql_type_registry.get(instance.__class__.__name__)
        return None

    return type(
        "SearchResultUnion",
        (graphene.Union,),
        {"Meta": meta, "resolve_type": classmethod(resolve_type)},
    )


def create_search_result_type(
    union_type: type[graphene.Union],
) -> type[graphene.ObjectType]:
    """
    Create a Graphene ObjectType representing paginated search results.

    Parameters:
        union_type: Union type whose members are the individual result types.

    Returns:
        A ``SearchResult`` Graphene ObjectType with ``results``, ``total``,
        ``total_is_exact``, ``page_info``, and ``took_ms`` fields.
    """
    return type(
        "SearchResult",
        (graphene.ObjectType,),
        {
            "results": graphene.List(union_type),
            "total": graphene.Int(),
            "total_is_exact": graphene.Boolean(),
            "page_info": graphene.Field(PageInfo),
            "took_ms": graphene.Int(),
        },
    )


# ---------------------------------------------------------------------------
# Filter-input type builders
# ---------------------------------------------------------------------------


def get_filter_options(
    attribute_type: type,
    attribute_name: str,
    map_field_to_graphene_read: GrapheneReadMapper,
    attr_info: Mapping[str, object] | None = None,
) -> Generator[
    tuple[
        str,
        type[graphene.ObjectType]
        | graphene.Scalar
        | MeasurementScalar
        | graphene.List
        | None,
    ],
    None,
    None,
]:
    """
    Yield ``(field_name, graphene_type)`` pairs for every filter variant of
    *attribute_name* / *attribute_type*.

    Emitted variants are:
    - GeneralManager relations: the base attribute with ``None`` so relation
      filter generation can handle it separately.
    - Scalar identifiers marked in metadata: base, ``exact`` and ``in`` use
      ``ID``/``[ID]``; range variants retain the native scalar mapper.
    - Other scalar fields named ``id`` retain their legacy ``exact``, ``in``
      and range operators, using their native scalar types.
    - ``Measurement``: base attribute plus ``gt/gte/lt/lte`` using
      ``MeasurementScalar``.
    - numeric, date, and datetime types: base attribute plus
      ``gt/gte/lt/lte`` using the mapper.
    - strings: base attribute plus ``exact``, ``icontains``, ``contains``,
      ``in``, ``startswith``, and ``endswith``. The ``in`` variant uses a list
      of the mapped base scalar, honoring a string ``graphql_scalar`` override
      in ``attr_info``.

    Parameters:
        attribute_type: The Python type declared for the attribute.
        attribute_name: Base name used to derive filter field names.
        map_field_to_graphene_read: Callable that maps a Python type + field
            name to the appropriate Graphene read type (passed to avoid a
            circular import from ``graphql.py``).
        attr_info: Optional additional attribute metadata passed through to
            ``map_field_to_graphene_read`` and other filter-type decisions.
            When present this should be the metadata mapping returned from
            ``Interface.get_attribute_types()`` for ``attribute_name`` and may
            include keys such as ``graphql_scalar`` that alter the generated
            Graphene type. When ``None``, the default field mapping behavior is
            used.

    Yields:
        ``(filter_field_name, graphene_input_type_or_None)`` tuples.
    """
    number_options = ["exact", "gt", "gte", "lt", "lte"]
    string_options = [
        "exact",
        "icontains",
        "contains",
        "in",
        "startswith",
        "endswith",
    ]

    manager_type = resolve_general_manager_type(
        attribute_type,
        get_graphql_manager_registry(),
    )
    normalized_type = manager_type or (
        attribute_type if isinstance(attribute_type, type) else str
    )

    if manager_type is not None:
        yield attribute_name, None
    elif (attr_info and attr_info.get("is_identifier")) or (
        attribute_name == "id" and not safe_issubclass(normalized_type, Measurement)
    ):
        # The legacy id filter operators also belong to business fields. Only
        # explicit identity metadata selects ID; the name preserves operators.
        is_identifier = bool(attr_info and attr_info.get("is_identifier"))
        native_info = {
            k: v for k, v in (attr_info or {}).items() if k != "is_identifier"
        }
        for option in (attribute_name, f"{attribute_name}__exact"):
            yield (
                option,
                graphene.ID()
                if is_identifier
                else map_field_to_graphene_read(
                    normalized_type, attribute_name, native_info
                ),
            )
        graphql_scalar = native_info.get("graphql_scalar")
        membership_type = (
            graphene.ID
            if is_identifier
            else map_field_to_graphene_base_type(
                normalized_type,
                graphql_scalar if isinstance(graphql_scalar, str) else None,
            )
        )
        yield f"{attribute_name}__in", graphene.List(membership_type)
        for option in ("gt", "gte", "lt", "lte"):
            yield (
                f"{attribute_name}__{option}",
                map_field_to_graphene_read(
                    normalized_type,
                    attribute_name,
                    native_info,
                ),
            )
        if safe_issubclass(normalized_type, str):
            for option in ("icontains", "contains", "startswith", "endswith"):
                yield (
                    f"{attribute_name}__{option}",
                    map_field_to_graphene_read(
                        normalized_type, attribute_name, native_info
                    ),
                )
    elif safe_issubclass(normalized_type, Measurement):
        yield attribute_name, MeasurementScalar()
        for option in number_options:
            yield f"{attribute_name}__{option}", MeasurementScalar()
        if attribute_name == "id":
            yield f"{attribute_name}__in", graphene.List(MeasurementScalar)
    else:
        yield (
            attribute_name,
            map_field_to_graphene_read(normalized_type, attribute_name, attr_info),
        )
        if safe_issubclass(normalized_type, (int, float, Decimal, date, datetime)):
            for option in number_options:
                yield (
                    f"{attribute_name}__{option}",
                    map_field_to_graphene_read(
                        normalized_type, attribute_name, attr_info
                    ),
                )
        elif safe_issubclass(normalized_type, str):
            graphql_scalar = attr_info.get("graphql_scalar") if attr_info else None
            base_type = map_field_to_graphene_base_type(
                normalized_type,
                graphql_scalar if isinstance(graphql_scalar, str) else None,
            )
            for option in string_options:
                if option == "in":
                    yield f"{attribute_name}__in", graphene.List(base_type)
                else:
                    yield (
                        f"{attribute_name}__{option}",
                        map_field_to_graphene_read(
                            normalized_type, attribute_name, attr_info
                        ),
                    )


def _get_filter_relation_info(
    field_type: type[GeneralManager],
    attribute_name: str,
    attr_info: Mapping[str, object],
) -> Mapping[str, object]:
    """Return explicit relation metadata or infer it for manager inputs."""
    if attr_info.get("relation_kind") is not None:
        return attr_info

    interface = getattr(field_type, "Interface", None)
    input_fields = getattr(interface, "input_fields", {})
    input_field = (
        input_fields.get(attribute_name) if isinstance(input_fields, dict) else None
    )
    input_type = getattr(input_field, "type", None)
    if (
        input_field is None
        or resolve_general_manager_type(
            input_type,
            get_graphql_manager_registry(),
        )
        is None
    ):
        return attr_info

    return {
        **attr_info,
        "relation_kind": "direct",
        "filter_lookup": attr_info.get("filter_lookup", attribute_name),
    }


_COMMON_FILTER_RELATION_INFO = _get_filter_relation_info


def get_relation_filter_option(
    attribute_type: object,
    attribute_name: str,
    attr_info: Mapping[str, object],
    graphql_filter_type_registry: dict[str, type[graphene.InputObjectType]],
    map_field_to_graphene_read: GrapheneReadMapper,
    remaining_depth: int,
) -> tuple[str, GrapheneFieldType] | None:
    """
    Build a nested relation filter field for direct and collection relations.

    Direct relations expose one nested input field. Collection relations expose
    an input object with ``any`` and ``none`` nested filters. When
    ``remaining_depth`` is exhausted, the attribute is not a manager relation,
    the relation kind is unsupported, or the nested type has no filterable
    fields, returns ``None``.

    Relation metadata is read from the interface attribute metadata:
    ``relation_kind="direct"`` represents a single related manager and
    ``relation_kind="collection"`` represents a collection. ``filter_lookup``
    is consumed later by ``normalize_filter_input()``.
    """
    if remaining_depth <= 0:
        return None
    manager_type = resolve_general_manager_type(
        attribute_type,
        get_graphql_manager_registry(),
    )
    if manager_type is None:
        return None

    relation_kind = attr_info.get("relation_kind")
    if relation_kind not in {"collection", "direct"}:
        return None

    nested_type = create_filter_options(
        manager_type,
        graphql_filter_type_registry,
        map_field_to_graphene_read,
        relation_depth=remaining_depth - 1,
        _remaining_depth=remaining_depth - 1,
    )
    if nested_type is None:
        return None

    if relation_kind == "collection":
        relation_type_name = (
            f"{manager_type.__name__}{attribute_name.title().replace('_', '')}"
            f"RelationFilterTypeDepth{remaining_depth - 1}"
        )
        if relation_type_name not in graphql_filter_type_registry:
            relation_filter = type(
                relation_type_name,
                (graphene.InputObjectType,),
                {
                    "any": graphene.InputField(nested_type),
                    "none": graphene.InputField(nested_type),
                    "_general_manager_filter_owner": manager_type,
                },
            )
            from general_manager.api.graphql_filter_contract import (
                register_filter_input,
            )

            # Collections declare no direct predicate, but their generated codec
            # must be pinned for the enclosing filter to remain attestable.
            register_filter_input(
                relation_filter, manager_type, {}, filter_source_signature(manager_type)
            )
            graphql_filter_type_registry[relation_type_name] = relation_filter
        return (
            attribute_name,
            graphene.InputField(graphql_filter_type_registry[relation_type_name]),
        )

    if relation_kind == "direct":
        return attribute_name, graphene.InputField(nested_type)

    return None


def create_filter_options(
    field_type: type[GeneralManager],
    graphql_filter_type_registry: dict[str, type[graphene.InputObjectType]],
    map_field_to_graphene_read: GrapheneReadMapper,
    *,
    relation_depth: int = 1,
    _remaining_depth: int | None = None,
) -> type[graphene.InputObjectType] | None:
    """
    Create (or retrieve from cache) a Graphene InputObjectType exposing all
    filter fields for *field_type*.

    Generated type names include the manager class name and remaining relation
    depth. The caller-owned registry is the only cache; changing interface
    metadata or relation depth requires using a fresh registry entry/name to
    rebuild the type.

    Parameters:
        field_type: Manager class whose Interface and GraphQLProperties
            determine the available filter fields.
        graphql_filter_type_registry: Shared registry dict for caching
            generated filter types (mutated in-place).
        map_field_to_graphene_read: Callable mapping Python type + field name
            to a Graphene read type (passed to avoid circular imports).
        relation_depth: Maximum nested manager-relation depth to generate.
        _remaining_depth: Internal recursion counter; callers should leave it
            unset.

    Returns:
        A Graphene ``InputObjectType`` for *field_type*, or ``None`` if no
        filterable fields exist.

    Raises:
        Exceptions from interface metadata access, GraphQL property metadata, or
        Graphene dynamic type creation propagate unchanged.
    """
    remaining_depth = relation_depth if _remaining_depth is None else _remaining_depth
    graphene_filter_type_name = f"{field_type.__name__}FilterTypeDepth{remaining_depth}"
    if graphene_filter_type_name in graphql_filter_type_registry:
        return graphql_filter_type_registry[graphene_filter_type_name]

    filter_fields: dict[str, GrapheneFieldType] = {}
    filter_contracts: dict[str, dict[str, str]] = {}
    for attr_name, attr_info in field_type.Interface.get_attribute_types().items():
        # Native constructor selections do not add a new GraphQL filter language.
        input_field = getattr(field_type.Interface, "input_fields", {}).get(attr_name)
        if getattr(input_field, "is_collection", False) is True:
            continue
        effective_attr_info = _get_filter_relation_info(
            field_type,
            attr_name,
            attr_info,
        )
        attr_type = effective_attr_info["type"]
        manager_type = resolve_general_manager_type(
            attr_type,
            get_graphql_manager_registry(),
        )
        if manager_type is not None:
            relation_option = get_relation_filter_option(
                manager_type,
                attr_name,
                effective_attr_info,
                graphql_filter_type_registry,
                map_field_to_graphene_read,
                remaining_depth,
            )
            if relation_option is not None:
                key, value = relation_option
                filter_fields[key] = value
                # Only singular relations with the same executable lookup prove
                # an output relation path. Collection/custom lookups fail closed.
                if (
                    effective_attr_info.get("relation_kind") == "direct"
                    and effective_attr_info.get("filter_lookup") == attr_name
                ):
                    filter_contracts[key] = {
                        "source": "interface_filter",
                        "field": attr_name,
                        "operator": "relation",
                        "manager": manager_type.__name__,
                    }
            continue
        for key, value in get_filter_options(
            cast(type, attr_type),
            attr_name,
            map_field_to_graphene_read,
            effective_attr_info,
        ):
            if value is not None:
                filter_fields[key] = value
                filter_contracts[key] = {
                    "source": "interface_filter",
                    "field": attr_name,
                    "operator": key[len(attr_name) + 2 :]
                    if key != attr_name
                    else "exact",
                }
    for prop_name, prop in field_type.Interface.get_graph_ql_properties().items():
        if not prop.filterable:
            continue
        hints = [t for t in get_args(prop.graphql_type_hint) if t is not type(None)]
        prop_type = hints[0] if hints else cast(type, prop.graphql_type_hint)
        prop_info: dict[str, object] = {}
        if prop.is_identifier:
            prop_type, _ = property_identifier_type(
                prop.graphql_type_hint,
                owner_name=field_type.__name__,
                field_name=prop_name,
            )
            prop_info["is_identifier"] = True
        for key, value in get_filter_options(
            prop_type, prop_name, map_field_to_graphene_read, prop_info
        ):
            if value is not None:
                filter_fields[key] = value
                # A custom property replacing a generated attribute cannot
                # inherit that attribute's executable predicate declaration.
                filter_contracts.pop(key, None)

    if not filter_fields:
        return None

    # Preserve source identity for consumers that restrict manager exposure. This
    # metadata is not a GraphQL field and does not alter public filter execution.
    filter_class = type(
        graphene_filter_type_name,
        (graphene.InputObjectType,),
        {
            **filter_fields,
            "_general_manager_filter_owner": field_type,
            "_general_manager_filter_contracts": filter_contracts,
        },
    )
    from general_manager.api.graphql_filter_contract import register_filter_input

    register_filter_input(
        filter_class, field_type, filter_contracts, filter_source_signature(field_type)
    )
    graphql_filter_type_registry[graphene_filter_type_name] = filter_class
    return filter_class


def _filter_identity_inputs(field_type: type[GeneralManager]) -> dict[str, Any]:
    """Collect constructor inputs used by the common identity filter path."""
    inputs = getattr(field_type.Interface, "input_fields", {})
    if not isinstance(inputs, Mapping):
        return {}
    names = {"id"}
    names.update(
        name
        for name, info in field_type.Interface.get_attribute_types().items()
        if info.get("is_identifier")
    )
    return {name: inputs[name] for name in sorted(names) if name in inputs}


def filter_source_signature(field_type: type[GeneralManager]) -> object:
    """Pin the shared normalizer's actual attribute/relation/ID semantics."""
    from general_manager.manager.input import Input, InputDomain

    attributes = field_type.Interface.get_attribute_types()

    def token(value: object) -> object:
        if isinstance(value, type):
            return ("type", id(value))
        if value is None or type(value) in (str, int, float, bool):
            return (type(value), value)
        return ("unsupported", id(value))

    fields = tuple(
        (
            name,
            tuple(
                (key, token(info.get(key)))
                for key in (
                    "type",
                    "relation_kind",
                    "filter_lookup",
                    "graphql_scalar",
                    "is_identifier",
                    "is_primary_key",
                )
            ),
        )
        for name, original in attributes.items()
        for info in (_get_filter_relation_info(field_type, name, original),)
    )
    # Domain/custom normalization cannot establish raw constructor ID equality.
    input_bindings = tuple(
        (
            name,
            type(identifier),
            identifier.type,
            getattr(identifier.cast, "__func__", identifier.cast),
            getattr(identifier.normalize, "__func__", identifier.normalize),
            identifier.normalizer,
            isinstance(identifier.possible_values, InputDomain),
            Input.cast,
            Input.normalize,
        )
        for name, identifier in _filter_identity_inputs(field_type).items()
    )
    converter_bindings = tuple(
        (
            name,
            getattr(converter, "__self__", None),
            getattr(converter, "__func__", converter),
        )
        for name, info in attributes.items()
        if info.get("is_identifier")
        for converter in (_identity_filter_converter(field_type, name),)
    )
    return fields, input_bindings, converter_bindings


_COMMON_FILTER_SOURCE_SIGNATURE = filter_source_signature


def normalize_id_filter_value(
    field_type: type[GeneralManager],
    lookup: str,
    value: object,
    *,
    _skip_malformed_native_id: bool = False,
) -> object:
    """
    Convert confirmed identity equality operands to their native types.

    Actual ORM primary keys use the declared constructor input, independent of
    the model column name. Raw relation references use the target field's
    ``to_python`` conversion, including ``to_field`` references. Ordered lookups
    and non-list/tuple ``in`` values pass through unchanged. Declared native
    properties use their validated annotation and the standard Input cast;
    collection operands retain the backend's existing element comparisons.
    Equality lists retain production adapters' OR grammar. Constructor and
    write validation remain separate. Null operands and elements remain null.

    Raises:
        Exceptions from ``Input.cast()`` or ORM field conversion propagate.
    """
    parts = lookup.split("__")
    attribute_name = parts[0]
    operator = parts[1] if len(parts) == 2 else "exact"
    if len(parts) > 2 or operator not in {"exact", "in"}:
        return value

    interface = getattr(field_type, "Interface", None)
    if interface is None:
        return value

    def cast_identity(converter: Callable[[object], object], item: object) -> object:
        """Cast one identity without hiding metadata or custom-converter failures."""
        try:
            return _cast_identity_scalar(converter, item)
        except (AttributeError, TypeError, ValueError) as exc:
            # The metadata and converter have already been selected above. At
            # this point only the proven built-in scalar conversion may be
            # ignored for an indexed candidate; application converters and
            # metadata failures must remain observable.
            if _skip_malformed_native_id:
                plain_input = (
                    _identity_filter_uses_plain_input(field_type, attribute_name)
                    if not isinstance(exc, AttributeError)
                    else _identity_filter_uses_plain_input(
                        field_type, attribute_name, expected_type=UUID
                    )
                )
                if plain_input:
                    return _MALFORMED_NATIVE_ID
            raise

    prop = interface.get_graph_ql_properties().get(attribute_name)
    if prop is not None:
        if not prop.is_identifier:
            return value
        native_type, _ = property_identifier_type(
            prop.graphql_type_hint,
            owner_name=field_type.__name__,
            field_name=attribute_name,
        )
        scalar_input = Input(native_type)
        if operator == "in" and not isinstance(value, (list, tuple)):
            return value
        if isinstance(value, (list, tuple)):
            return [cast_identity(scalar_input.cast, item) for item in value]
        return cast_identity(scalar_input.cast, value)

    converter = _identity_filter_converter(field_type, attribute_name)
    if converter is None:
        return value

    if operator == "in" and not isinstance(value, (list, tuple)):
        return value
    if isinstance(value, (list, tuple)):
        return [cast_identity(converter, item) for item in value]
    return cast_identity(converter, value)


def _identity_filter_converter(
    field_type: type[GeneralManager], attribute_name: str
) -> Callable[[object], object] | None:
    """Select the actual identity converter without invoking it.

    Execution and predicate attestation share this choice. Backend-specific
    conversion retains its runtime behavior but does not declare equality proof.
    """
    interface = field_type.Interface
    metadata: Mapping[str, object] = interface.get_attribute_types().get(
        attribute_name, {}
    )
    if not metadata.get("is_identifier"):
        return None
    input_fields = getattr(interface, "input_fields", {})
    input_field = input_fields.get(attribute_name)
    model = getattr(interface, "_model", None)
    converter: Callable[[object], object] | None = None
    if isinstance(model, type) and issubclass(model, models.Model):
        try:
            model_field = model._meta.get_field(attribute_name)
        except FieldDoesNotExist:
            return None
        if model_field.primary_key:
            # ORM constructors expose the actual PK under their public id input,
            # even when the model column has another name.
            input_field = input_fields.get("id")
        if input_field is None:
            target_field = getattr(model_field, "target_field", model_field)
            converter = target_field.to_python
    if input_field is not None:
        converter = input_field.cast
    return converter


def _identity_filter_uses_plain_input(
    field_type: type[GeneralManager],
    attribute_name: str,
    *,
    expected_type: type | None = None,
) -> bool:
    """Identify the built-in scalar cast used by a native ORM identity.

    Search may discard malformed backend IDs only for the standard, callback-free
    input declaration. Custom normalizers, domains, validators and input classes
    keep their converter errors observable to the application. When
    ``expected_type`` is supplied, the declaration must use that exact scalar;
    this keeps exceptional conversion handling narrower than ordinary casts.
    """
    interface = field_type.Interface
    input_fields = getattr(interface, "input_fields", {})
    if type(input_fields) is not dict:
        return False
    input_field = input_fields.get(attribute_name)
    model = getattr(interface, "_model", None)
    if isinstance(model, type) and issubclass(model, models.Model):
        try:
            model_field = model._meta.get_field(attribute_name)
        except FieldDoesNotExist:
            return False
        if model_field.primary_key:
            input_field = input_fields.get("id")
    if type(input_field) is not Input:
        return False
    plain_input: Input[Any] = input_field
    from general_manager.api.graphql_filter_contract import (
        _INPUT_CAST,
        _INPUT_NORMALIZE,
    )

    if (
        getattr(plain_input.cast, "__func__", None) is not _INPUT_CAST
        or getattr(plain_input.normalize, "__func__", None) is not _INPUT_NORMALIZE
        or getattr(plain_input.cast, "__self__", None) is not plain_input
        or getattr(plain_input.normalize, "__self__", None) is not plain_input
    ):
        return False
    from general_manager.manager.input import _COMMON_INPUT_EXECUTABLES

    if not all(
        executable_matches(function, signature)
        for function, signature in _COMMON_INPUT_EXECUTABLES
    ):
        return False
    return (
        any(
            plain_input.type is scalar_type
            for scalar_type in (str, bool, int, float, Decimal, date, datetime, UUID)
        )
        and (expected_type is None or plain_input.type is expected_type)
        and plain_input.possible_values is None
        and plain_input.min_value is None
        and plain_input.max_value is None
        and plain_input.validator is None
        and plain_input.normalizer is None
        and not plain_input.depends_on
    )


def _cast_identity_scalar(
    converter: Callable[[object], object], value: object
) -> object:
    """Use existing ID validation before native casting can truncate JSON values."""
    if value is not None and not isinstance(value, UUID):
        GraphQLID.parse_value(value)
    return converter(value)


_COMMON_NORMALIZE_ID_FILTER_VALUE = normalize_id_filter_value


def normalize_filter_input(
    field_type: type[GeneralManager],
    filter_input: GraphQLFilterMapping,
) -> NormalizedFilterPlan:
    """
    Flatten nested GraphQL relation filters into backend filter and exclude kwargs.

    Direct relation filters are prefixed with the relation's ``filter_lookup``.
    Collection ``any`` filters become positive filters; collection ``none``
    filters are inverted into excludes. Nested excludes from ``none`` are
    inverted back into positive filters. Non-relation keys and malformed nested
    values pass through unchanged.

    Direct relation metadata is expected to use ``relation_kind="direct"`` and
    collection metadata ``relation_kind="collection"``. ``filter_lookup`` is the
    prefix used for flattened lookup keys and defaults to the attribute name.

    Returns:
        A mapping with ``"filter"`` and ``"exclude"`` dictionaries.

    Raises:
        Exceptions from interface metadata access or ID casting propagate
        unchanged.
    """
    interface = getattr(field_type, "Interface", None)
    get_attribute_types = getattr(interface, "get_attribute_types", None)
    if not callable(get_attribute_types):
        return {"filter": dict(filter_input), "exclude": {}}

    filters: GraphQLFilterMapping = {}
    excludes: GraphQLFilterMapping = {}
    attr_types = get_attribute_types()

    for key, value in filter_input.items():
        attr_info = attr_types.get(key)
        if not attr_info or not isinstance(value, dict):
            filters[key] = normalize_id_filter_value(field_type, key, value)
            continue

        effective_attr_info = _get_filter_relation_info(field_type, key, attr_info)
        attr_type = effective_attr_info.get("type")
        relation_kind = effective_attr_info.get("relation_kind")
        lookup = effective_attr_info.get("filter_lookup", key)
        manager_type = resolve_general_manager_type(
            attr_type,
            get_graphql_manager_registry(),
        )
        if manager_type is None or relation_kind is None:
            filters[key] = value
            continue

        if relation_kind == "direct":
            nested = normalize_filter_input(manager_type, value)
            for nested_key, nested_value in nested["filter"].items():
                filters[f"{lookup}__{nested_key}"] = nested_value
            for nested_key, nested_value in nested["exclude"].items():
                excludes[f"{lookup}__{nested_key}"] = nested_value
            continue

        if relation_kind == "collection":
            any_value = value.get("any")
            none_value = value.get("none")
            if isinstance(any_value, dict):
                nested = normalize_filter_input(manager_type, any_value)
                for nested_key, nested_value in nested["filter"].items():
                    filters[f"{lookup}__{nested_key}"] = nested_value
                for nested_key, nested_value in nested["exclude"].items():
                    excludes[f"{lookup}__{nested_key}"] = nested_value
            if isinstance(none_value, dict):
                nested = normalize_filter_input(manager_type, none_value)
                for nested_key, nested_value in nested["filter"].items():
                    excludes[f"{lookup}__{nested_key}"] = nested_value
                for nested_key, nested_value in nested["exclude"].items():
                    filters[f"{lookup}__{nested_key}"] = nested_value
            continue

        filters[key] = value

    return {"filter": filters, "exclude": excludes}


# ---------------------------------------------------------------------------
_COMMON_FILTER_NORMALIZER = normalize_filter_input
_COMMON_ID_SCALAR = GraphQLID
_COMMON_FILTER_EXECUTABLES = tuple(
    (name, function, executable_signature(function))
    for name, function in (
        ("normalize_filter_input", _COMMON_FILTER_NORMALIZER),
        ("normalize_id_filter_value", _COMMON_NORMALIZE_ID_FILTER_VALUE),
        ("_cast_identity_scalar", _cast_identity_scalar),
        ("_get_filter_relation_info", _COMMON_FILTER_RELATION_INFO),
        ("filter_source_signature", _COMMON_FILTER_SOURCE_SIGNATURE),
        ("_filter_identity_inputs", _filter_identity_inputs),
        ("_identity_filter_converter", _identity_filter_converter),
    )
)


def common_filter_execution_valid() -> bool:
    """Revalidate the original common normalizer's concrete executable helpers."""
    return GraphQLID is _COMMON_ID_SCALAR and all(
        globals().get(name) is function and executable_matches(function, signature)
        for name, function, signature in _COMMON_FILTER_EXECUTABLES
    )


# Top-level search query registration
# ---------------------------------------------------------------------------


def _search_sortable_field_union(
    manager_classes: Iterable[type[GeneralManager]],
) -> dict[str, str]:
    """Return the configured sortable-field union for the global search enum."""
    fields: dict[str, str] = {}
    for manager_class in manager_classes:
        config = get_search_config(manager_class)
        if config is None:
            continue
        for index_config in config.indexes:
            for field in index_config.sorts:
                graphql_name = graphql_name_for_path(field)
                existing = fields.get(graphql_name)
                if existing is not None and existing != field:
                    raise SearchOrderingError.enum_alias_collision(
                        graphql_name,
                        existing,
                        field,
                    )
                fields[graphql_name] = field
    return fields


def _validate_search_ordering(
    manager_classes: Iterable[type[GeneralManager]],
    *,
    index_name: str,
    terms: tuple[SortTerm, ...],
) -> None:
    """Ensure every selected manager exposes every term on the selected index."""
    if not terms:
        return
    requested_fields = {term.field for term in terms}
    for manager_class in manager_classes:
        config = get_search_config(manager_class)
        configured_fields = (
            {
                field
                for index_config in config.indexes
                if index_config.name == index_name
                for field in index_config.sorts
            }
            if config is not None
            else set()
        )
        unsupported = requested_fields - configured_fields
        if unsupported:
            fields = ", ".join(sorted(unsupported))
            raise SearchOrderingError.unsupported_field(
                fields,
                manager_class.__name__,
                index_name,
            )


def register_search_query(
    query_fields: dict[str, GrapheneFieldType],
    manager_registry: dict[str, type[GeneralManager]],
    graphql_type_registry: dict[str, type[graphene.ObjectType]],
    search_union: type[graphene.Union] | None,
    search_result_type: type[graphene.ObjectType] | None,
) -> tuple[type[graphene.Union] | None, type[graphene.ObjectType] | None]:
    """
    Register a global ``search`` GraphQL field covering all searchable managers.

    Mutates *query_fields* in-place to add the ``"search"`` field.  Returns the
    (possibly newly created) ``search_union`` and ``search_result_type`` so
    callers can persist them as class-level state.

    Manager search order follows ``manager_registry.values()`` filtered to
    managers with search configuration, unless the resolver receives ``types``;
    unknown names in ``types`` are ignored. ``total`` is the post-permission
    authorized total, never the backend raw total. Both total modes fail on
    scan-budget exhaustion: a partial raw prefix can depend on unreadable
    content. ``total_is_exact`` remains true on successful responses.
    Typed ordering validates selected search-index
    capabilities before any backend request. Configured filter-key validation
    runs on the parsed top-level search filters before permission
    filters or relation normalizers are applied; exclude-derived permission keys
    are not validated by this search helper.

    Parameters:
        query_fields: Mutable dict of registered query fields; will be updated.
        manager_registry: Mapping of manager name → manager class.
        graphql_type_registry: Registry of manager name → Graphene ObjectType.
        search_union: Current cached search union (or ``None``).
        search_result_type: Current cached result type (or ``None``).

    Returns:
        ``(updated_search_union, updated_search_result_type)``; either may be
        ``None`` if no searchable managers were found.

    Raises:
        Exceptions from Graphene field/type creation, search backend lookup,
        backend search calls, manager construction, permission checks, filter
        validation, and sort comparison propagate unless explicitly handled by
        the resolver. Invalid configured search filter keys are converted to
        ``GraphQLError``.
    """
    if "search" in query_fields:
        return search_union, search_result_type

    search_configs = {
        manager_class: config
        for manager_class in manager_registry.values()
        if (config := get_search_config(manager_class)) is not None
    }

    def index_definitions(config: SearchConfigSpec | None) -> tuple[object, ...]:
        """Snapshot mutable index declarations for startup-proof invalidation."""
        # IndexConfig is frozen, but its application-supplied sequences/mapping
        # can be mutable. Detach the values used to guard startup proof reuse.
        return (
            tuple(
                (
                    item.name,
                    tuple(item.fields),
                    tuple(item.filters),
                    tuple(item.sorts),
                    item.boost,
                    item.min_score,
                    frozenset(item.identity_fields.items()),
                )
                for item in config.indexes
            )
            if config is not None
            else ()
        )

    configured_indexes = {
        manager: index_definitions(config) for manager, config in search_configs.items()
    }
    type_map = {
        manager_class.__name__: manager_class for manager_class in search_configs
    }
    if not type_map:
        return search_union, search_result_type

    union_type = create_search_union(type_map, graphql_type_registry)
    if union_type is None:
        return None, search_result_type

    result_type = create_search_result_type(union_type)
    sortable_union = _search_sortable_field_union(type_map.values())
    ordering_types = create_ordering_types(
        None,
        scope="",
        field_paths=sortable_union,
    )
    # Classify definitions once per schema. No user or object decisions survive
    # a request, and rebuilding the schema rebuilds every projection proof.
    projection_indexes: dict[tuple[type[GeneralManager], str], IndexConfig] = {}
    for manager, config in search_configs.items():
        for index_config in config.indexes:
            # SearchIndexer selects the first matching declaration. Preserve the
            # same duplicate-name rule for the schema-lifetime projection proof.
            projection_indexes.setdefault((manager, index_config.name), index_config)
    projection_plans = {
        (manager, index_name): analyze_search_projection(
            manager, search_configs[manager], index_config, manager_registry
        )
        for (manager, index_name), index_config in projection_indexes.items()
    }

    def resolver(
        _root: object,
        info: GraphQLResolveInfo,
        query: str,
        index: str | None = None,
        types: list[str] | None = None,
        filters: GraphQLSearchFilterInput = None,
        order_by: object = None,
        total_mode: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> SearchResolverPayload:
        """
        Execute a cross-manager full-text search with permission filtering.

        Static projection proofs permit database candidate batches; other
        projections retain full source and operand authorization. Both paths
        retain native instance membership and runtime relation gates before
        counting and pagination. Index freshness belongs to index maintenance.
        Individually indexed
        instances outside the native domain cannot contribute results or totals. Omitted ``page`` and
        ``page_size`` values default to ``1`` and ``10`` respectively. Supplied
        non-positive values raise a ``GraphQLError``.
        """
        index_name = index or "global"
        current_page, limit = _resolve_search_pagination(page, page_size)
        offset = (current_page - 1) * limit
        parsed_filters = parse_search_filters(filters)
        if parsed_filters:
            try:
                validate_filter_keys(index_name, parsed_filters)
            except ValueError as exc:
                raise GraphQLError(str(exc)) from exc
        backend = get_search_backend()
        manager_classes: list[type[GeneralManager]]
        if types:
            manager_classes = []
            seen_type_names: set[str] = set()
            for type_name in types:
                if type_name in seen_type_names:
                    continue
                seen_type_names.add(type_name)
                manager_class = type_map.get(type_name)
                if manager_class is not None:
                    manager_classes.append(manager_class)
        else:
            manager_classes = list(type_map.values())
        try:
            ordering_terms = order_by_to_sort_terms(
                order_by,
                allowed_fields=sortable_union.values(),
            )
        except GraphQLOrderingInputError as exc:
            raise _bad_user_input_error(str(exc)) from exc
        _validate_search_ordering(
            manager_classes,
            index_name=index_name,
            terms=ordering_terms,
        )

        hits: list[SearchHitEntry] = []
        total = 0
        took_ms: int | None = None
        requested_count = offset + limit
        fetch_limit = max(requested_count, limit)
        normalize_search_total_mode(total_mode)
        total_scan_limit = get_graphql_search_total_scan_limit()
        scanned_hits = 0

        from general_manager.api.graphql_query_permissions import (
            MAX_QUERY_PERMISSION_CANDIDATES,
            QueryFieldAuthorizationBudget,
        )

        source_candidate_count = 0
        authorization_budget = QueryFieldAuthorizationBudget()

        def authorized_source_candidates(
            source: Iterable[GeneralManager], plan: ReadPermissionPlan
        ) -> Generator[GeneralManager, None, None]:
            nonlocal source_candidate_count
            for candidate in source:
                if source_candidate_count >= MAX_QUERY_PERMISSION_CANDIDATES:
                    raise GraphQLError(SEARCH_SOURCE_AUTHORIZATION_LIMIT_ERROR)
                source_candidate_count += 1
                if passes_permission_filters(candidate, info, permission_plan=plan):
                    yield candidate

        for manager_class in manager_classes:
            type_label = get_type_label(manager_class)
            permission_plan = get_read_permission_filter(manager_class, info)
            decision = getattr(permission_plan, "decision", "conditional")
            if decision == "deny_all":
                continue
            from general_manager.api.graphql_query_permissions import (
                authorize_query_fields,
                INCOMPLETE_ERROR,
                QueryRelationPermissionError,
            )
            from general_manager.bucket.request_bucket import RequestBucket
            from general_manager.bucket.database_bucket import DatabaseBucket

            authorized_instances: dict[str, GeneralManager] = {}
            search_config = get_search_config(manager_class)
            projection_plan = projection_plans.get((manager_class, index_name))
            selected_index = projection_indexes.get((manager_class, index_name))
            if search_config != search_configs.get(manager_class) or index_definitions(
                search_config
            ) != configured_indexes.get(manager_class):
                # Changed projection definitions cannot inherit a startup fast
                # approval. Keep the existing validation errors and full checks
                # until the schema is rebuilt with the new configuration.
                selected_index = (
                    next(
                        (
                            item
                            for item in search_config.indexes
                            if item.name == index_name
                        ),
                        None,
                    )
                    if search_config is not None
                    else None
                )
                projection_plan = (
                    replace(
                        analyze_search_projection(
                            manager_class,
                            search_config,
                            selected_index,
                            manager_registry,
                        ),
                        fast=False,
                    )
                    if search_config is not None and selected_index is not None
                    else None
                )
            if selected_index is None:
                # The manager has no projection for this requested index. Do
                # not let stale backend entries establish a search domain.
                continue
            fast_source = None
            if projection_plan is not None and projection_plan.error is not None:
                raise GraphQLError(projection_plan.error)
            if search_config is not None:
                custom_projection = search_config.to_document is not None
                operand_paths = set(parsed_filters)
                operand_paths.update(term.field for term in ordering_terms)
                document_fields = {field.name for field in selected_index.iter_fields()}
                document_fields.update(selected_index.filters)
                document_fields.update(selected_index.sorts)
                # The selected declaration owns this manager's projection
                # proof. Duplicate names use the first declaration everywhere,
                # matching SearchIndexer and the schema-lifetime plan.
                operand_paths.update(document_fields)
                # The index envelope exposes a synthetic manager-type filter.
                # A real manager attribute with that name still needs permission.
                if not any(
                    "type" in fields
                    for fields in (
                        manager_class.Interface.get_attribute_types(),
                        manager_class.Interface.get_graph_ql_properties(),
                        manager_class.Interface.get_attributes(),
                        manager_class.Interface.input_fields,
                    )
                ):
                    operand_paths = {
                        path
                        for path in operand_paths
                        if path.split("__", 1)[0] != "type"
                    }
                if custom_projection:
                    from general_manager.search.projection import (
                        SearchProjectionError,
                        projection_filter_fields,
                        projection_source_paths,
                    )

                    document_operands = {
                        path for path in operand_paths if path not in parsed_filters
                    }
                    try:
                        document_operands.update(
                            projection_filter_fields(
                                manager_class,
                                selected_index,
                                (
                                    path
                                    for path in parsed_filters
                                    if path in operand_paths
                                ),
                            )
                        )
                        if (
                            projection_plan is not None
                            and document_operands <= projection_plan.document_fields
                        ):
                            # The schema already validated all configured sources.
                            # Reuse definitions only; runtime row/field/relation
                            # authorization still happens below for each actor.
                            operand_paths = set(projection_plan.paths)
                        else:
                            operand_paths = set(
                                projection_source_paths(
                                    manager_class, search_config, document_operands
                                )
                            )
                    except SearchProjectionError as exc:
                        raise GraphQLError(str(exc)) from exc
                source_candidates = manager_class.all()
                from general_manager.search.projection import (
                    SearchProjectionError,
                    projection_filter_fields,
                )

                requested_fields_proven = True
                try:
                    projection_filter_fields(
                        manager_class,
                        selected_index,
                        (path for path in parsed_filters if path in operand_paths),
                    )
                except SearchProjectionError:
                    # Aggregate index metadata may admit a key configured only
                    # by another manager; it has no proof on this projection.
                    requested_fields_proven = False
                # Native row authorization precedes operand checks. Projection
                # provenance never substitutes for the source instance gate.
                if (
                    projection_plan is not None
                    and projection_plan.fast
                    and requested_fields_proven
                    and type(source_candidates) is DatabaseBucket
                    and set(manager_class.Interface.input_fields) == {"id"}
                ):
                    fast_source = source_candidates
                else:
                    candidates = list(
                        authorized_source_candidates(source_candidates, permission_plan)
                    )
                    if projection_plan is not None:
                        authorize_query_fields(
                            candidates,
                            info,
                            projection_plan.paths,
                            native_paths=True,
                            budget=authorization_budget,
                        )
                    if not custom_projection:
                        # Backend lookup grammar is separate from exact indexed
                        # source paths, including relation identity leaves.
                        requested_operands = set(parsed_filters).intersection(
                            operand_paths
                        )
                        requested_operands.update(term.field for term in ordering_terms)
                        authorize_query_fields(
                            candidates,
                            info,
                            requested_operands,
                            budget=authorization_budget,
                        )
                    # Preserve the exact authorized instances, including
                    # calculation input domains and remote completeness.
                    authorized_instances = {
                        normalize_identification(candidate.identification): candidate
                        for candidate in candidates
                    }
                if (
                    isinstance(source_candidates, RequestBucket)
                    and not source_candidates.response_is_complete
                ):
                    raise GraphQLError(INCOMPLETE_ERROR)
            # Shared indexes may give the same field different native types.
            # Keep parsed user filters and permission constraints untouched.
            custom_projection = (
                search_config is not None and search_config.to_document is not None
            )
            manager_filters = (
                dict(parsed_filters)
                if custom_projection
                else {
                    key: normalize_id_filter_value(manager_class, key, value)
                    for key, value in parsed_filters.items()
                }
            )
            backend_shape = get_backend_shape(manager_class)
            instance_check_reasons = resolve_instance_check_reasons(
                permission_plan,
                backend_shape=backend_shape,
            )
            filter_groups: SearchFilters = (
                manager_filters or None
                if decision == "allow_all" or custom_projection
                else merge_permission_filters(
                    manager_filters,
                    permission_plan.filters,
                )
            )
            filter_groups = build_search_filters(
                manager_class, index_name, filter_groups
            )
            total_hits_for_manager = 0
            candidate_hits_for_manager = 0
            offset_cursor = 0
            manager_total_is_exact = True
            skip_malformed_native_ids = (
                fast_source is not None and common_filter_execution_valid()
            )
            while True:
                remaining_scan_budget = total_scan_limit - scanned_hits
                if remaining_scan_budget <= 0:
                    manager_total_is_exact = False
                    break
                query_limit = min(fetch_limit, remaining_scan_budget)
                result = backend.search(
                    index_name,
                    query,
                    filters=filter_groups,
                    limit=query_limit,
                    offset=offset_cursor,
                    types=[type_label],
                    sort=tuple(term.signed_field for term in ordering_terms)
                    if ordering_terms
                    else None,
                )
                took_ms = (
                    result.took_ms
                    if took_ms is None
                    else took_ms + (result.took_ms or 0)
                )
                if len(result.hits) > query_limit:
                    raise GraphQLError(
                        _SEARCH_BACKEND_SCAN_LIMIT_EXCEEDED,
                        extensions={"code": "SEARCH_SCAN_LIMIT_EXCEEDED"},
                    )
                if result.exhausted is False and len(result.hits) < query_limit:
                    # A backend cap may yield an empty or short page before the
                    # actual result set ends. Its prefix cannot establish an
                    # authorized total or page.
                    manager_total_is_exact = False
                    break
                if not result.hits:
                    break
                scanned_hits += len(result.hits)
                offset_cursor += len(result.hits)
                if fast_source is not None:
                    identifiers = []
                    for hit in result.hits:
                        if hit.type != type_label:
                            continue
                        try:
                            identification = hit.identification
                            if not isinstance(identification, Mapping) or set(
                                identification
                            ) != {"id"}:
                                continue
                            identifier = identification["id"]
                            # Reject malformed GraphQL IDs before the native
                            # converter. Converter and configuration failures
                            # remain observable to their owning layer.
                            if not isinstance(identifier, UUID):
                                GraphQLID.parse_value(identifier)
                        except (GraphQLError, KeyError, TypeError, ValueError):
                            continue
                        if skip_malformed_native_ids:
                            normalized_identifier = normalize_id_filter_value(
                                manager_class,
                                "id",
                                identifier,
                                _skip_malformed_native_id=True,
                            )
                        else:
                            normalized_identifier = normalize_id_filter_value(
                                manager_class, "id", identifier
                            )
                        if normalized_identifier is _MALFORMED_NATIVE_ID:
                            continue
                        identifiers.append(normalized_identifier)
                    # Bucket filtering keeps the native source restriction and
                    # reuses its batch ORM hydration. Arbitrary indexed IDs never
                    # acquire membership by construction.
                    candidate_source = fast_source.filter(pk__in=identifiers)
                    if projection_plan is not None and projection_plan.has_relations:
                        candidate_source = candidate_source._with_empty_relation_probes(
                            path.split("__", 1)[0] for path in projection_plan.paths
                        )
                    authorized_instances = (
                        {
                            normalize_identification(
                                candidate.identification
                            ): candidate
                            for candidate in candidate_source
                        }
                        if identifiers
                        else {}
                    )
                for hit in result.hits:
                    if hit.type != type_label:
                        logger.debug(
                            "discarded search hit with unexpected type",
                            context={
                                "expected_type": type_label,
                                "received_type": hit.type,
                            },
                        )
                        continue
                    try:
                        instance = authorized_instances.get(
                            normalize_identification(hit.identification)
                        )
                    except (TypeError, ValueError, KeyError) as exc:
                        logger.debug(
                            "invalid search result identification",
                            context={
                                "manager": hit.type,
                                "identification": hit.identification,
                            },
                            exc_info=exc,
                        )
                        continue
                    if instance is None:
                        logger.debug(
                            "discarded search hit outside authorized source domain",
                            context={
                                "manager": hit.type,
                                "identification": hit.identification,
                            },
                        )
                        continue
                    candidate_hits_for_manager += 1
                    if not passes_permission_filters(
                        instance,
                        info,
                        permission_plan=permission_plan,
                    ):
                        continue
                    if hit.data is None:
                        raise GraphQLError(_SEARCH_STORED_DATA_REQUIRED)
                    if (
                        projection_plan is not None
                        and set(hit.data) != projection_plan.document_fields
                    ):
                        # A document from an older schema may retain fields
                        # absent from today's proof. Those values can still
                        # affect backend matching and rank until reindexing.
                        continue
                    if (
                        fast_source is not None
                        and projection_plan is not None
                        and projection_plan.has_relations
                    ):
                        try:
                            authorize_query_fields(
                                [instance],
                                info,
                                projection_plan.paths,
                                native_paths=True,
                                budget=authorization_budget,
                            )
                        except QueryRelationPermissionError:
                            # Remove the entire projection before count, ranking
                            # and pagination. Filtering nested output alone leaves
                            # hidden text in the index and is insufficient.
                            continue
                    total_hits_for_manager += 1
                    hits.append((hit.score, hit, instance))
                trim_search_hit_entries_to_window(
                    hits,
                    requested_count=requested_count,
                    terms=ordering_terms,
                )
                if result.exhausted is True:
                    break
                if len(result.hits) < query_limit:
                    break
                if scanned_hits >= total_scan_limit:
                    manager_total_is_exact = False
                    break
            total += total_hits_for_manager
            if not manager_total_is_exact:
                # A raw scan prefix can change with unreadable values. Even
                # bounded mode must not expose that prefix as an authorized page.
                raise GraphQLError(
                    _SEARCH_SCAN_LIMIT_EXCEEDED,
                    extensions={"code": "SEARCH_SCAN_LIMIT_EXCEEDED"},
                )
            if decision == "conditional" and permission_plan.requires_instance_check:
                logger.info(
                    "graphql read authorization summary",
                    context={
                        "source": "search",
                        "manager": manager_class.__name__,
                        "backend_shape": backend_shape,
                        "candidate_count": candidate_hits_for_manager,
                        "authorized_count": total_hits_for_manager,
                        "denied_count": max(
                            candidate_hits_for_manager - total_hits_for_manager,
                            0,
                        ),
                        "requires_instance_check": True,
                        "instance_check_reasons": list(instance_check_reasons),
                    },
                )

        sort_search_hit_entries(hits, terms=ordering_terms)

        items: list[GeneralManager] = []
        for _, _hit, instance in hits[offset : offset + limit]:
            items.append(instance)

        return {
            "results": items,
            "total": total,
            "total_is_exact": True,
            "page_info": {
                "total_count": total,
                "page_size": limit,
                "current_page": current_page,
                "total_pages": (total + limit - 1) // limit,
            },
            "took_ms": took_ms,
        }

    query_fields["search"] = graphene.Field(
        result_type,
        query=graphene.String(required=True),
        index=graphene.String(),
        types=graphene.List(graphene.String),
        filters=graphene.JSONString(),
        total_mode=graphene.String(),
        page=graphene.Int(),
        page_size=graphene.Int(),
        **(
            {
                "order_by": graphene.Argument(
                    graphene.List(graphene.NonNull(ordering_types.input_type))
                )
            }
            if ordering_types
            else {}
        ),
        resolver=resolver,
    )

    return union_type, result_type
