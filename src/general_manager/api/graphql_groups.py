"""Generated GraphQL types and resolvers for explicit grouped results."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import UnionType
from typing import TYPE_CHECKING, Union, cast, get_args, get_origin

import graphene
from graphene.utils.str_converters import to_snake_case
from graphql import GraphQLError

from general_manager.api.graphql_errors import MeasurementType, PageInfo
from general_manager.api.graphql_resolvers import (
    check_read_permission,
    create_list_resolver,
    measurement_to_graphql_payload,
)
from general_manager.api.graphql_ordering import sortable_field_paths
from general_manager.api.graphql_relations import resolve_general_manager_type
from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.group_manager import GroupManager, group_sum_value_type
from general_manager.measurement import Measurement

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo


GraphQLFieldMap = dict[str, object]
GraphQLFieldMapper = Callable[[object, str, Mapping[str, object] | None], object]
GraphQLListGetter = Callable[[object, bool], "Bucket[GeneralManager] | None"]


class GroupQueryError(GraphQLError):
    """Raised when an explicit grouped query would disclose unavailable data."""

    @classmethod
    def missing_keys(cls) -> "GroupQueryError":
        return cls("groupBy must select at least one grouping key.")

    @classmethod
    def denied_key(cls, field_name: str) -> "GroupQueryError":
        return cls(f"Permission denied to read grouping key '{field_name}'.")

    @classmethod
    def invalid_ordering(cls) -> "GroupQueryError":
        return cls("Grouped orderBy fields must be selected grouping keys.")

    @classmethod
    def denied_sum(cls, field_name: str) -> "GroupQueryError":
        return cls(f"Permission denied to read sum field '{field_name}'.")

    @classmethod
    def invalid_key(cls, field_name: str) -> "GroupQueryError":
        return cls(f"{field_name!r} is not an eligible grouping key.")


@dataclass(frozen=True, slots=True)
class GroupGraphQLTypes:
    """Generated result types for one manager's explicit group API."""

    page_type: type[graphene.ObjectType]
    group_type: type[graphene.ObjectType]


def create_group_types(
    manager_class: type[GeneralManager],
    *,
    member_page_type: type[graphene.ObjectType],
    map_field: GraphQLFieldMapper,
    member_resolver: Callable[..., object],
    member_arguments: Mapping[str, object],
) -> GroupGraphQLTypes:
    """Create keys, sums, group, and page output types for one manager."""
    attributes = eligible_group_key_fields(manager_class)
    key_fields: GraphQLFieldMap = {}
    sum_fields: GraphQLFieldMap = {}
    sum_resolvers: GraphQLFieldMap = {}

    for field_name, field_info in attributes.items():
        field_type = field_info["type"]
        key_fields[field_name] = map_field(field_type, field_name, field_info)
        sum_type = group_sum_value_type(field_type)
        if _is_sum_type(field_type):
            if sum_type is not None and issubclass(sum_type, Measurement):
                sum_fields[field_name] = graphene.Field(
                    MeasurementType,
                    target_unit=graphene.String(),
                )
            else:
                sum_fields[field_name] = map_field(sum_type, field_name, field_info)
            sum_resolvers[f"resolve_{field_name}"] = _sum_resolver(field_name)

    key_type = type(
        f"{manager_class.__name__}GroupKeys",
        (graphene.ObjectType,),
        key_fields,
    )
    group_fields: GraphQLFieldMap = {
        "keys": graphene.Field(key_type, required=True),
        "members": graphene.Field(member_page_type, **dict(member_arguments)),
        "count": graphene.Int(required=True),
        "resolve_keys": lambda group, _info: group.keys,
        "resolve_members": member_resolver,
        "resolve_count": lambda group, _info: group.count,
    }
    if sum_fields:
        sums_type = type(
            f"{manager_class.__name__}GroupSums",
            (graphene.ObjectType,),
            {**sum_fields, **sum_resolvers},
        )
        group_fields["sums"] = graphene.Field(sums_type, required=True)
        group_fields["resolve_sums"] = lambda group, _info: group
    group_type = type(
        f"{manager_class.__name__}Group",
        (graphene.ObjectType,),
        group_fields,
    )
    page_type = type(
        f"{manager_class.__name__}GroupPage",
        (graphene.ObjectType,),
        {
            "groups": graphene.List(group_type, required=True),
            "pageInfo": graphene.Field(PageInfo, required=True),
        },
    )
    return GroupGraphQLTypes(page_type=page_type, group_type=group_type)


def create_group_resolver(
    base_getter: GraphQLListGetter,
    manager_class: type[GeneralManager],
    *,
    filter_normalizer: Callable[
        [type[GeneralManager], dict[str, object]], dict[str, dict[str, object]]
    ]
    | None = None,
) -> Callable[..., object]:
    """Build a grouped page resolver on top of the normal authorized list path."""
    list_resolver = create_list_resolver(
        base_getter,
        manager_class,
        filter_normalizer,
        grouping_validator=_validate_group_request,
    )

    def resolver(
        root: object,
        info: GraphQLResolveInfo,
        group_by: list[str],
        **kwargs: object,
    ) -> object:
        python_group_by = _python_group_fields(manager_class, group_by)
        payload = cast(
            Mapping[str, object],
            list_resolver(root, info, group_by=python_group_by, **kwargs),
        )
        return {
            "groups": list(cast(Iterable[object], payload["items"])),
            "pageInfo": payload["pageInfo"],
        }

    return resolver


def create_member_resolver(
    manager_class: type[GeneralManager],
    *,
    filter_normalizer: Callable[
        [type[GeneralManager], dict[str, object]], dict[str, dict[str, object]]
    ]
    | None = None,
) -> Callable[..., object]:
    """Return a normal paginated resolver over one group's original members."""
    resolver = create_list_resolver(
        lambda group, _include_inactive: cast(
            GroupManager[GeneralManager], group
        ).members,
        manager_class,
        filter_normalizer,
    )

    def member_resolver(
        group: GroupManager[GeneralManager],
        info: GraphQLResolveInfo,
        **kwargs: object,
    ) -> object:
        return resolver(group, info, **kwargs)

    return member_resolver


def _is_sum_type(field_type: object) -> bool:
    concrete_type = group_sum_value_type(field_type)
    return (
        concrete_type is not None
        and not issubclass(concrete_type, bool)
        and issubclass(concrete_type, (int, float, Decimal, Measurement))
    )


def _python_group_fields(
    manager_class: type[GeneralManager],
    group_by: list[str],
) -> list[str]:
    """Translate GraphQL-cased group keys to their interface attribute names."""
    attributes = eligible_group_key_fields(manager_class)
    result: list[str] = []
    for field_name in group_by:
        snake_name = to_snake_case(field_name)
        python_name = snake_name if snake_name in attributes else field_name
        if python_name not in attributes:
            raise GroupQueryError.invalid_key(field_name)
        result.append(python_name)
    return result


def eligible_group_key_fields(
    manager_class: type[GeneralManager],
) -> dict[str, Mapping[str, object]]:
    """Return fields valid as keys for generated GraphQL grouping APIs only."""
    result: dict[str, Mapping[str, object]] = {}
    for field_name, field_info in manager_class.Interface.get_attribute_types().items():
        field_type = field_info["type"]
        if field_info.get("relation_kind") == "collection":
            continue
        if _is_collection_type(field_type):
            continue
        result[field_name] = field_info
    return result


def _is_collection_type(field_type: object) -> bool:
    """Return whether a runtime annotation represents a collection value."""
    origin = get_origin(field_type)
    if origin in {list, tuple, set, dict}:
        return True
    if origin in {Union, UnionType}:
        return any(
            _is_collection_type(member)
            for member in get_args(field_type)
            if member is not type(None)
        )
    target = origin or field_type
    return isinstance(target, type) and issubclass(
        target, (list, tuple, set, dict, Bucket)
    )


def group_sortable_field_paths(
    manager_class: type[GeneralManager],
    manager_registry: Mapping[str, type[GeneralManager]],
) -> dict[str, str]:
    """Restrict typed group ordering to the fields that can define a group."""
    eligible = eligible_group_key_fields(manager_class)
    paths = sortable_field_paths(manager_class, manager_registry)
    result: dict[str, str] = {}
    for graphql_name, python_path in paths.items():
        root_field = python_path.split("__", 1)[0]
        if root_field not in eligible:
            continue
        field_type = eligible[root_field]["type"]
        if python_path == root_field or (
            python_path == f"{root_field}__id"
            and resolve_general_manager_type(field_type, manager_registry) is not None
        ):
            result[graphql_name] = python_path
    return result


def _sum_resolver(field_name: str) -> Callable[..., object]:
    def resolver(
        group: GroupManager[GeneralManager],
        info: GraphQLResolveInfo,
        target_unit: str | None = None,
    ) -> object:
        _validate_group_field_permission(group, info, field_name)
        value = group.sum(field_name)
        if isinstance(value, Measurement):
            return measurement_to_graphql_payload(value, target_unit)
        return value

    return resolver


def _validate_group_request(
    queryset: "Bucket[GeneralManager]",
    group_by: list[str] | None,
    order_by: object,
    info: GraphQLResolveInfo,
) -> None:
    if not group_by:
        raise GroupQueryError.missing_keys()
    requested_keys = set(group_by)
    for field_name in requested_keys:
        for member in queryset:
            if not check_read_permission(member, info, field_name):
                raise GroupQueryError.denied_key(field_name)

    from general_manager.api.graphql_ordering import order_by_to_sort_terms

    try:
        ordering_fields = {term.field for term in order_by_to_sort_terms(order_by)}
    except ValueError as exc:
        raise GraphQLError(str(exc)) from exc
    if any(
        field not in requested_keys
        and not any(field == f"{key}__id" for key in requested_keys)
        for field in ordering_fields
    ):
        raise GroupQueryError.invalid_ordering()


def _validate_group_field_permission(
    group: GroupManager[GeneralManager],
    info: GraphQLResolveInfo,
    field_name: str,
) -> None:
    for member in group.members:
        if not check_read_permission(member, info, field_name):
            raise GroupQueryError.denied_sum(field_name)
