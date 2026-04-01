"""
Standalone search-subsystem helpers extracted from ``api/graphql.py``.

These functions hold no reference to the ``GraphQL`` class and can be
imported freely without creating circular imports.  The ``GraphQL`` class
exposes them as thin classmethods / staticmethods for backward compatibility.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import (
    Any,
    Callable,
    Generator,
    Literal,
    Mapping,
    TYPE_CHECKING,
    Type,
    cast,
    get_args,
)

import graphene  # type: ignore[import]
from graphql import GraphQLError

from django.utils import timezone

from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement.measurement import Measurement
from general_manager.search.backend_registry import get_search_backend
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
from general_manager.api.graphql_resolvers import (
    can_read_instance,
    get_backend_shape,
    resolve_instance_check_reasons,
)

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo

logger = get_logger("api.graphql_search")


# ---------------------------------------------------------------------------
# Filter normalisation
# ---------------------------------------------------------------------------


def parse_search_filters(
    filters: dict[str, Any] | str | list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """
    Normalise search filters supplied as a dict, JSON string, or list of filter
    objects into a single lookup dict.

    Parameters:
        filters: Filters to normalise. Accepts:
            - dict: returned as-is.
            - JSON string: parsed to a dict or list; invalid JSON → empty dict.
            - list of dicts: each dict should contain ``"field"`` and optionally
              ``"op"``, ``"value"``, or ``"values"``.

    Returns:
        dict[str, Any]: Mapping of filter lookup strings to their values.
    """
    parsed: Any = filters
    if isinstance(filters, str):
        try:
            parsed = json.loads(filters)
        except (json.JSONDecodeError, ValueError):
            parsed = None
    if isinstance(parsed, list):
        merged: dict[str, Any] = {}
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
        return parsed
    return {}


# ---------------------------------------------------------------------------
# Permission-filter helpers
# ---------------------------------------------------------------------------


def merge_permission_filters(
    filters: dict[str, Any] | None,
    permission_filters: list[dict[Literal["filter", "exclude"], dict[str, Any]]],
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """
    Combine a base filter with multiple permission-derived filter sets.

    Parameters:
        filters: Base filter to apply to each permission set; treated as empty
            if ``None``.
        permission_filters: Sequence of filter/exclude mappings; only the
            filter part is merged here.

    Returns:
        If *permission_filters* is empty, returns *filters* or ``None``.
        Otherwise returns a list of merged dicts (one per permission set), or
        ``None`` if the resulting list would be empty.
    """
    if not permission_filters:
        return filters or None
    groups: list[dict[str, Any]] = []
    for permission_filter in permission_filters:
        combined = dict(filters or {})
        combined.update(permission_filter.get("filter", {}))
        groups.append(combined)
    return groups or None


def matches_filters(
    instance: GeneralManager,
    filters: dict[str, Any],
    *,
    empty_is_match: bool = True,
) -> bool:
    """
    Return ``True`` if *instance* satisfies every condition in *filters*.

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
    permission_plan: Any | None = None,
) -> bool:
    """
    Return ``True`` if the current user may read *instance*.

    Checks all per-manager read-permission filter sets.  Returns ``True`` if
    no permission filters are configured, or if at least one filter passes and
    its corresponding exclude does not match.

    Parameters:
        instance: The manager instance to evaluate.
        info: GraphQL resolver info containing the request context / user.
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
        graphql_type_registry: Registry of manager name → Graphene ObjectType.

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
        ``took_ms``, and ``raw`` fields.
    """
    return type(
        "SearchResult",
        (graphene.ObjectType,),
        {
            "results": graphene.List(union_type),
            "total": graphene.Int(),
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
    map_field_to_graphene_read: Callable[[type, str, Mapping[str, Any] | None], Any],
    attr_info: Mapping[str, Any] | None = None,
) -> Generator[
    tuple[str, type[graphene.ObjectType] | MeasurementScalar | graphene.List | None],
    None,
    None,
]:
    """
    Yield ``(field_name, graphene_type)`` pairs for every filter variant of
    *attribute_name* / *attribute_type*.

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

    if issubclass(attribute_type, GeneralManager):
        yield attribute_name, None
    elif issubclass(attribute_type, Measurement):
        yield attribute_name, MeasurementScalar()
        for option in number_options:
            yield f"{attribute_name}__{option}", MeasurementScalar()
    else:
        yield (
            attribute_name,
            map_field_to_graphene_read(attribute_type, attribute_name, attr_info),
        )
        if issubclass(attribute_type, (int, float, Decimal, date, datetime)):
            for option in number_options:
                yield (
                    f"{attribute_name}__{option}",
                    map_field_to_graphene_read(
                        attribute_type, attribute_name, attr_info
                    ),
                )
        elif issubclass(attribute_type, str):
            base_type = map_field_to_graphene_base_type(
                attribute_type,
                attr_info.get("graphql_scalar") if attr_info else None,
            )
            for option in string_options:
                if option == "in":
                    yield f"{attribute_name}__in", graphene.List(base_type)
                else:
                    yield (
                        f"{attribute_name}__{option}",
                        map_field_to_graphene_read(
                            attribute_type, attribute_name, attr_info
                        ),
                    )


def create_filter_options(
    field_type: Type[GeneralManager],
    graphql_filter_type_registry: dict[str, type[graphene.InputObjectType]],
    map_field_to_graphene_read: Callable[[type, str, Mapping[str, Any] | None], Any],
) -> type[graphene.InputObjectType] | None:
    """
    Create (or retrieve from cache) a Graphene InputObjectType exposing all
    filter fields for *field_type*.

    Parameters:
        field_type: Manager class whose Interface and GraphQLProperties
            determine the available filter fields.
        graphql_filter_type_registry: Shared registry dict for caching
            generated filter types (mutated in-place).
        map_field_to_graphene_read: Callable mapping Python type + field name
            to a Graphene read type (passed to avoid circular imports).

    Returns:
        A Graphene ``InputObjectType`` for *field_type*, or ``None`` if no
        filterable fields exist.
    """
    graphene_filter_type_name = f"{field_type.__name__}FilterType"
    if graphene_filter_type_name in graphql_filter_type_registry:
        return graphql_filter_type_registry[graphene_filter_type_name]

    filter_fields: dict[str, Any] = {}
    for attr_name, attr_info in field_type.Interface.get_attribute_types().items():
        attr_type = attr_info["type"]
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


# ---------------------------------------------------------------------------
# Top-level search query registration
# ---------------------------------------------------------------------------


def register_search_query(
    query_fields: dict[str, Any],
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

    Parameters:
        query_fields: Mutable dict of registered query fields; will be updated.
        manager_registry: Mapping of manager name → manager class.
        graphql_type_registry: Registry of manager name → Graphene ObjectType.
        search_union: Current cached search union (or ``None``).
        search_result_type: Current cached result type (or ``None``).

    Returns:
        ``(updated_search_union, updated_search_result_type)``; either may be
        ``None`` if no searchable managers were found.
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
        filters: dict[str, Any] | str | list[dict[str, Any]] | None = None,
        sort_by: str | None = None,
        sort_desc: bool = False,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """Execute a cross-manager full-text search with permission filtering."""
        index_name = index or "global"
        limit = page_size or 10
        current_page = page or 1
        offset = max(current_page - 1, 0) * limit
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

        hits: list[tuple[float | None, Any, GeneralManager]] = []
        total = 0
        took_ms: int | None = None
        raw: list[Any] = []
        requested_count = offset + limit
        fetch_limit = max(requested_count, limit)
        # Tracks how many authorized hits have been appended globally so far.
        # This enforces a single cross-manager cap on collected results while
        # still letting the loop continue counting for an accurate ``total``.
        global_appended_hits = 0

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
            authorized_hits: list[tuple[float | None, Any, GeneralManager]] = []
            total_hits_for_manager = 0
            candidate_hits_for_manager = 0
            appended_hits_for_manager = 0
            offset_cursor = 0
            while True:
                result = backend.search(
                    index_name,
                    query,
                    filters=filter_groups,
                    limit=fetch_limit,
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
                    # Apply a single global cap across all managers so that
                    # ``authorized_hits`` never grows beyond ``requested_count``
                    # items, regardless of how many managers are searched.
                    if global_appended_hits < requested_count:
                        authorized_hits.append((hit.score, hit, instance))
                        appended_hits_for_manager += 1
                        global_appended_hits += 1
                if len(result.hits) < fetch_limit:
                    break
            total += total_hits_for_manager
            hits.extend(authorized_hits)
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

        if sort_by:

            def _normalize_sort_value(value: Any) -> Any:
                """Normalise a sort value to a comparable type."""
                if value is None:
                    return None
                if isinstance(value, (int, float, Decimal)):
                    return float(value)
                if isinstance(value, datetime):
                    if timezone.is_naive(value):
                        return timezone.make_aware(value)
                    return value
                if isinstance(value, date):
                    return timezone.make_aware(
                        datetime.combine(value, datetime.min.time())
                    )
                if isinstance(value, str):
                    try:
                        parsed = datetime.fromisoformat(value)
                    except ValueError:
                        return value
                    if timezone.is_naive(parsed):
                        parsed = timezone.make_aware(parsed)
                    return parsed
                return str(value)

            def _sort_key(
                item: tuple[float | None, Any, GeneralManager],
            ) -> tuple[bool, Any]:
                """Sort key placing None-valued items last."""
                value = item[1].data.get(sort_by) if item[1].data else None
                normalized = _normalize_sort_value(value)
                return (normalized is None, normalized)

            hits.sort(key=_sort_key, reverse=sort_desc)
        else:
            hits.sort(key=lambda item: (item[0] or 0), reverse=True)

        items: list[GeneralManager] = []
        for _, _hit, instance in hits[offset : offset + limit]:
            items.append(instance)

        return {
            "results": items,
            "total": total,
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
        page=graphene.Int(),
        page_size=graphene.Int(),
        resolver=resolver,
    )

    return union_type, result_type
