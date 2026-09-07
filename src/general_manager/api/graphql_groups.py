"""Shared validation and resolver plumbing for explicit GraphQL group pages."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from types import UnionType
from typing import TYPE_CHECKING, Annotated, Union, get_args, get_origin

from graphene.utils.str_converters import to_snake_case
from graphql import GraphQLError

from general_manager.api.graphql_ordering import order_by_to_sort_terms
from general_manager.api.graphql_ordering import sortable_field_paths
from general_manager.api.graphql_relations import resolve_general_manager_type
from general_manager.api.graphql_resolvers import (
    check_read_permission,
    apply_grouped_projection_sorting,
    create_list_resolver,
    relation_id_alias_field,
)
from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement import Measurement

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo


GraphQLListGetter = Callable[[object, bool], "Bucket[GeneralManager] | None"]


class GroupQueryError(GraphQLError):
    """Raised before a grouped query can read a protected source value."""

    @classmethod
    def missing_keys(cls) -> "GroupQueryError":
        return cls("groupBy must select at least one grouping key.")

    @classmethod
    def denied_key(cls, field_name: str) -> "GroupQueryError":
        return cls(f"Permission denied to read grouping key '{field_name}'.")

    @classmethod
    def invalid_key(cls, field_name: str) -> "GroupQueryError":
        return cls(f"{field_name!r} is not an eligible grouping key.")


def create_group_resolver(
    base_getter: GraphQLListGetter,
    manager_class: type[GeneralManager],
    *,
    filter_normalizer: Callable[
        [type[GeneralManager], dict[str, object]], dict[str, dict[str, object]]
    ]
    | None = None,
) -> Callable[..., object]:
    """Build the resolver behind a dedicated ``…Groups`` page field."""
    list_resolver = create_list_resolver(
        base_getter,
        manager_class,
        filter_normalizer,
        grouping_validator=_validate_group_request,
        group_sorter=apply_grouped_projection_sorting,
    )

    def resolver(
        root: object,
        info: GraphQLResolveInfo,
        group_by: list[str],
        **kwargs: object,
    ) -> object:
        return list_resolver(
            root,
            info,
            group_by=_python_group_fields(manager_class, group_by),
            **kwargs,
        )

    return resolver


def eligible_group_key_fields(
    manager_class: type[GeneralManager],
) -> dict[str, Mapping[str, object]]:
    """Return non-collection interface attributes accepted by ``groupBy``."""
    interface = getattr(manager_class, "Interface", None)
    get_attribute_types = getattr(interface, "get_attribute_types", None)
    if not callable(get_attribute_types):
        return {}
    result: dict[str, Mapping[str, object]] = {}
    for field_name, field_info in get_attribute_types().items():
        field_type = field_info["type"]
        if field_info.get("relation_kind") == "collection" or _is_collection_type(
            field_type
        ):
            continue
        result[field_name] = field_info
    return result


def group_sortable_field_paths(
    manager_class: type[GeneralManager],
    manager_registry: Mapping[str, type[GeneralManager]],
) -> dict[str, str]:
    """Expose only values that a flat grouped projection can safely aggregate.

    Direct relations keep their identity ordering path.  Traversing a related
    object would read data that is absent from the grouped output and cannot be
    authorised as one aggregate scalar.
    """
    eligible = eligible_group_key_fields(manager_class)
    paths = sortable_field_paths(manager_class, manager_registry)
    properties = manager_class.Interface.get_graph_ql_properties()
    result: dict[str, str] = {}
    for graphql_name, python_path in paths.items():
        root = python_path.split("__", 1)[0]
        field_info = eligible.get(root)
        if field_info is None:
            property_value = properties.get(root)
            if (
                property_value is not None
                and "__" not in python_path
                and not _is_collection_type(property_value.graphql_type_hint)
                and _is_grouped_scalar_annotation(property_value.graphql_type_hint)
                and resolve_general_manager_type(
                    property_value.graphql_type_hint, manager_registry
                )
                is None
            ):
                result[graphql_name] = python_path
            continue
        related = resolve_general_manager_type(field_info["type"], manager_registry)
        if (
            related is None
            and "__" not in python_path
            and _is_grouped_scalar_annotation(field_info["type"])
            and field_info.get("orm_field_kind") not in {"file", "image"}
        ):
            result[graphql_name] = python_path
    return result


def _python_group_fields(
    manager_class: type[GeneralManager], group_by: list[str]
) -> list[str]:
    eligible = eligible_group_key_fields(manager_class)
    result: list[str] = []
    for field_name in group_by:
        snake_name = to_snake_case(field_name)
        python_name = snake_name if snake_name in eligible else field_name
        if python_name not in eligible:
            raise GroupQueryError.invalid_key(field_name)
        result.append(python_name)
    return result


def _validate_group_request(
    queryset: "Bucket[GeneralManager]",
    group_by: list[str] | None,
    order_by: object,
    info: GraphQLResolveInfo,
) -> None:
    if not group_by:
        raise GroupQueryError.missing_keys()

    # The bucket and GroupManager read both grouping and ordering attributes.
    # Check those source values before either operation begins. Relation-id
    # aliases additionally require the canonical relation permission.
    protected_fields = dict.fromkeys(group_by)
    try:
        protected_fields.update(
            (term.field.split("__", 1)[0], None)
            for term in order_by_to_sort_terms(order_by)
        )
    except ValueError as exc:
        raise GraphQLError(str(exc)) from exc
    permission_fields = {}
    for field_name in protected_fields:
        relation_field = relation_id_alias_field(queryset._manager_class, field_name)
        permission_fields[field_name] = (
            (field_name, relation_field)
            if relation_field is not None
            else (field_name,)
        )
    denied_fields: set[str] = set()
    for member in queryset:
        for field_name, checked_fields in permission_fields.items():
            if any(
                not check_read_permission(member, info, permission_field)
                for permission_field in checked_fields
            ):
                denied_fields.add(field_name)
    for field_name in protected_fields:
        if field_name in denied_fields:
            raise GroupQueryError.denied_key(field_name)


def _is_collection_type(field_type: object) -> bool:
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


def _is_grouped_scalar_annotation(annotation: object) -> bool:
    """Return whether GroupManager can project the annotation as one scalar."""
    while get_origin(annotation) in {Annotated, Union, UnionType}:
        if get_origin(annotation) is Annotated:
            annotation = get_args(annotation)[0]
            continue
        members = [
            member for member in get_args(annotation) if member is not type(None)
        ]
        if len(members) != 1:
            return False
        annotation = members[0]
    return isinstance(annotation, type) and issubclass(
        annotation,
        (bool, int, float, Decimal, Measurement, str, datetime, date, time),
    )
