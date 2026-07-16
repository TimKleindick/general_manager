"""
Standalone search-subsystem helpers extracted from ``api/graphql.py``.

These functions hold no reference to the ``GraphQL`` class and can be
imported freely without creating circular imports.  The ``GraphQL`` class
exposes them as thin classmethods / staticmethods for backward compatibility.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Generator, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import (
    TYPE_CHECKING,
    Literal,
    cast,
    get_args,
)

import graphene
from graphql import GraphQLError

from django.utils import timezone

from general_manager.conf import get_setting
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement.measurement import Measurement
from general_manager.utils.type_checks import safe_issubclass
from general_manager.search.backend_registry import get_search_backend
from general_manager.search.backend import SearchHit
from general_manager.search.registry import (
    get_search_config,
    validate_filter_keys,
)
from general_manager.utils.filter_parser import create_filter_function
from general_manager.api.graphql_errors import (
    MeasurementScalar,
    map_field_to_graphene_base_type,
    get_read_permission_filter,
)
from general_manager.api.graphql_relations import resolve_general_manager_type
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
SortableValue = float | datetime | str | None
SearchTotalMode = Literal["exact", "bounded"]
_BAD_USER_INPUT_CODE = "BAD_USER_INPUT"
_PAGE_MUST_BE_POSITIVE = "page must be a positive integer."
_PAGE_SIZE_MUST_BE_POSITIVE = "pageSize must be a positive integer."
DEFAULT_GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT = 1000
SEARCH_TOTAL_MODE_ERROR = "must be one of: exact, bounded."
SEARCH_TOTAL_SCAN_LIMIT_ERROR = (
    "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT must be a positive integer."
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
    """Return the configured positive scan limit for bounded total counts."""
    raw_limit = get_setting(
        "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT",
        DEFAULT_GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT,
    )
    if not isinstance(raw_limit, int) or isinstance(raw_limit, bool) or raw_limit <= 0:
        raise _bad_user_input_error(SEARCH_TOTAL_SCAN_LIMIT_ERROR)
    return raw_limit


def normalize_search_sort_value(value: object) -> SortableValue:
    """Normalise a search sort value to a comparable type."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value)
        return value
    if isinstance(value, date):
        return timezone.make_aware(datetime.combine(value, datetime.min.time()))
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return value
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed
    return str(value)


def sort_search_hit_entries(
    entries: list[SearchHitEntry],
    *,
    sort_by: str | None,
    sort_desc: bool,
) -> None:
    """Sort retained search entries using GraphQL search response semantics."""
    if sort_by:

        def _sort_key(
            item: SearchHitEntry,
        ) -> tuple[bool, SortableValue]:
            """Return a null-last comparable key for an indexed search hit."""
            value = item[1].data.get(sort_by) if item[1].data else None
            normalized = normalize_search_sort_value(value)
            return (normalized is None, normalized)

        entries.sort(key=_sort_key)
        if sort_desc:
            null_start = next(
                (index for index, item in enumerate(entries) if _sort_key(item)[0]),
                len(entries),
            )
            entries[:null_start] = reversed(entries[:null_start])
    else:
        entries.sort(key=lambda item: item[0] or 0, reverse=True)


def trim_search_hit_entries_to_window(
    entries: list[SearchHitEntry],
    *,
    requested_count: int,
    sort_by: str | None,
    sort_desc: bool,
) -> None:
    """Keep only entries that can still appear in the requested global page."""
    if len(entries) <= requested_count:
        return
    sort_search_hit_entries(entries, sort_by=sort_by, sort_desc=sort_desc)
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
        Otherwise returns a list of merged dicts (one per permission set), or
        ``None`` if the resulting list would be empty.
        Permission filter keys override matching base filter keys in each merged
        group. When *permission_filters* is empty, a non-empty *filters* mapping
        is returned unchanged rather than copied. Empty permission constraints,
        ``{"filter": {}}``, and ``{"exclude": {}}`` all produce a copy of the
        base filter for that alternative; exclude-only constraints still rely on
        later per-instance authorization. This helper raises no search-specific
        errors.
    """
    if not permission_filters:
        return filters or None
    groups: list[GraphQLFilterMapping] = []
    for permission_filter in permission_filters:
        combined = dict(filters or {})
        combined.update(permission_filter.get("filter", {}))
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
        ``total_is_exact``, ``took_ms``, and ``raw`` fields.
    """
    return type(
        "SearchResult",
        (graphene.ObjectType,),
        {
            "results": graphene.List(union_type),
            "total": graphene.Int(),
            "total_is_exact": graphene.Boolean(),
            "took_ms": graphene.Int(),
            "raw": graphene.JSONString(),
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
    - ``id``: ``id``, ``id__exact``, ``id__in``, plus range variants
      ``id__gt/gte/lt/lte`` using the normal mapper.
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

    manager_type = resolve_general_manager_type(attribute_type)
    normalized_type = manager_type or (
        attribute_type if isinstance(attribute_type, type) else str
    )

    if manager_type is not None:
        yield attribute_name, None
    elif attribute_name == "id":
        yield attribute_name, graphene.ID()
        yield f"{attribute_name}__exact", graphene.ID()
        yield f"{attribute_name}__in", graphene.List(graphene.ID)
        for option in ("gt", "gte", "lt", "lte"):
            yield (
                f"{attribute_name}__{option}",
                map_field_to_graphene_read(
                    normalized_type,
                    attribute_name,
                    attr_info,
                ),
            )
    elif safe_issubclass(normalized_type, Measurement):
        yield attribute_name, MeasurementScalar()
        for option in number_options:
            yield f"{attribute_name}__{option}", MeasurementScalar()
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
    manager_type = resolve_general_manager_type(attribute_type)
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
            graphql_filter_type_registry[relation_type_name] = type(
                relation_type_name,
                (graphene.InputObjectType,),
                {
                    "any": graphene.InputField(nested_type),
                    "none": graphene.InputField(nested_type),
                },
            )
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
    for attr_name, attr_info in field_type.Interface.get_attribute_types().items():
        attr_type = attr_info["type"]
        manager_type = resolve_general_manager_type(attr_type)
        if manager_type is not None:
            relation_option = get_relation_filter_option(
                manager_type,
                attr_name,
                attr_info,
                graphql_filter_type_registry,
                map_field_to_graphene_read,
                remaining_depth,
            )
            if relation_option is not None:
                key, value = relation_option
                filter_fields[key] = value
            continue
        filter_fields = {
            **filter_fields,
            **{
                k: v
                for k, v in get_filter_options(
                    attr_type, attr_name, map_field_to_graphene_read, attr_info
                )
                if v is not None
            },
        }
    for prop_name, prop in field_type.Interface.get_graph_ql_properties().items():
        if not prop.filterable:
            continue
        hints = [t for t in get_args(prop.graphql_type_hint) if t is not type(None)]
        prop_type = hints[0] if hints else cast(type, prop.graphql_type_hint)
        filter_fields = {
            **filter_fields,
            **{
                k: v
                for k, v in get_filter_options(
                    prop_type, prop_name, map_field_to_graphene_read
                )
                if v is not None
            },
        }

    if not filter_fields:
        return None

    filter_class = type(
        graphene_filter_type_name,
        (graphene.InputObjectType,),
        filter_fields,
    )
    graphql_filter_type_registry[graphene_filter_type_name] = filter_class
    return filter_class


def normalize_id_filter_value(
    field_type: type[GeneralManager],
    lookup: str,
    value: object,
) -> object:
    """
    Cast equality-style ID filter values to the manager's identifier type.

    Only ``id``, ``id__exact``, and list/tuple-valued ``id__in`` lookups are
    cast. When the interface does not expose an ``id`` input, or ``id__in`` is
    not list/tuple-shaped, the original value is returned unchanged. Other
    iterables are not cast specially.

    Raises:
        Exceptions from the interface input field's ``cast()`` method propagate
        unchanged.
    """
    if lookup not in {"id", "id__exact", "id__in"}:
        return value

    interface = getattr(field_type, "Interface", None)
    input_fields = getattr(interface, "input_fields", {})
    id_input = input_fields.get("id") if isinstance(input_fields, dict) else None
    if id_input is None:
        return value

    if lookup == "id__in":
        if not isinstance(value, (list, tuple)):
            return value
        return [id_input.cast(item) for item in value]
    return id_input.cast(value)


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

        attr_type = attr_info.get("type")
        relation_kind = attr_info.get("relation_kind")
        lookup = attr_info.get("filter_lookup", key)
        manager_type = resolve_general_manager_type(attr_type)
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
# Top-level search query registration
# ---------------------------------------------------------------------------


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
    unknown names in ``types`` are ignored. In exact total mode, ``total`` in
    the resolver payload is the post-permission authorized total, not the
    backend raw total. In bounded mode, ``total`` is the authorized count found
    before the per-manager scan cap and ``total_is_exact`` tells callers whether
    that count is exact. Sorting uses hit ``data[sort_by]`` when available and
    does not validate the sort field ahead of comparison. Configured filter-key
    validation runs on the parsed top-level search filters before permission
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

    type_map = {
        manager_class.__name__: manager_class
        for manager_class in manager_registry.values()
        if get_search_config(manager_class) is not None
    }
    if not type_map:
        return search_union, search_result_type

    union_type = create_search_union(type_map, graphql_type_registry)
    if union_type is None:
        return None, search_result_type

    result_type = create_search_result_type(union_type)

    def resolver(
        _root: object,
        info: GraphQLResolveInfo,
        query: str,
        index: str | None = None,
        types: list[str] | None = None,
        filters: GraphQLSearchFilterInput = None,
        sort_by: str | None = None,
        sort_desc: bool = False,
        total_mode: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> SearchResolverPayload:
        """
        Execute a cross-manager full-text search with permission filtering.

        The resolver parses user filters, validates configured filter keys,
        searches each selected manager type, instantiates manager objects from
        hit identification, applies read permission filters/instance checks, and
        returns paginated authorized manager instances. Omitted ``page`` and
        ``page_size`` values default to ``1`` and ``10`` respectively. Supplied
        non-positive values raise a ``GraphQLError``. Backend raw payloads are
        collected once per backend request in the ``raw`` list.
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
            manager_classes = [type_map[name] for name in types if name in type_map]
        else:
            manager_classes = list(type_map.values())

        hits: list[SearchHitEntry] = []
        total = 0
        took_ms: int | None = None
        raw: list[object] = []
        requested_count = offset + limit
        fetch_limit = max(requested_count, limit)
        resolved_total_mode = normalize_search_total_mode(total_mode)
        total_scan_limit = (
            max(get_graphql_search_total_scan_limit(), requested_count)
            if resolved_total_mode == "bounded"
            else None
        )
        total_is_exact = True

        for manager_class in manager_classes:
            type_label = manager_class.__name__
            permission_plan = get_read_permission_filter(manager_class, info)
            backend_shape = get_backend_shape(manager_class)
            instance_check_reasons = resolve_instance_check_reasons(
                permission_plan,
                backend_shape=backend_shape,
            )
            filter_groups = merge_permission_filters(
                parsed_filters,
                permission_plan.filters,
            )
            total_hits_for_manager = 0
            candidate_hits_for_manager = 0
            offset_cursor = 0
            scanned_hits_for_manager = 0
            manager_total_is_exact = True
            while True:
                query_limit = fetch_limit
                if total_scan_limit is not None:
                    remaining_scan_budget = total_scan_limit - scanned_hits_for_manager
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
                    sort_by=sort_by,
                    sort_desc=sort_desc,
                )
                took_ms = (
                    result.took_ms
                    if took_ms is None
                    else took_ms + (result.took_ms or 0)
                )
                raw.append(result.raw)
                if not result.hits:
                    break
                scanned_hits_for_manager += len(result.hits)
                offset_cursor += len(result.hits)
                for hit in result.hits:
                    try:
                        instance = manager_class(**hit.identification)
                    except (TypeError, ValueError, KeyError) as exc:
                        logger.debug(
                            "failed to instantiate search result",
                            context={
                                "manager": hit.type,
                                "identification": hit.identification,
                            },
                            exc_info=exc,
                        )
                        continue
                    candidate_hits_for_manager += 1
                    if not passes_permission_filters(
                        instance,
                        info,
                        permission_plan=permission_plan,
                    ):
                        continue
                    total_hits_for_manager += 1
                    hits.append((hit.score, hit, instance))
                trim_search_hit_entries_to_window(
                    hits,
                    requested_count=requested_count,
                    sort_by=sort_by,
                    sort_desc=sort_desc,
                )
                if len(result.hits) < query_limit:
                    break
                if (
                    total_scan_limit is not None
                    and scanned_hits_for_manager >= total_scan_limit
                ):
                    manager_total_is_exact = False
                    break
            total += total_hits_for_manager
            if not manager_total_is_exact:
                total_is_exact = False
            if permission_plan.requires_instance_check:
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

        sort_search_hit_entries(hits, sort_by=sort_by, sort_desc=sort_desc)

        items: list[GeneralManager] = []
        for _, _hit, instance in hits[offset : offset + limit]:
            items.append(instance)

        return {
            "results": items,
            "total": total,
            "total_is_exact": total_is_exact,
            "took_ms": took_ms,
            "raw": raw,
        }

    query_fields["search"] = graphene.Field(
        result_type,
        query=graphene.String(required=True),
        index=graphene.String(),
        types=graphene.List(graphene.String),
        filters=graphene.JSONString(),
        sort_by=graphene.String(),
        sort_desc=graphene.Boolean(),
        total_mode=graphene.String(),
        page=graphene.Int(),
        page_size=graphene.Int(),
        resolver=resolver,
    )

    return union_type, result_type
