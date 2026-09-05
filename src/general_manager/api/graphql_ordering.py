"""Typed GraphQL ordering inputs and conversion to signed bucket ordering."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha1

import graphene
from graphene.utils.str_converters import to_camel_case

from general_manager.api.graphql_relations import resolve_general_manager_type
from general_manager.bucket._ordering import (
    InvalidOrderingError,
    SortTerm,
    normalize_ordering,
)
from general_manager.manager.general_manager import GeneralManager


class OrderDirection(graphene.Enum):  # type: ignore[misc]
    """One direction for one typed GraphQL ordering term."""

    ASC = "ASC"
    DESC = "DESC"


class GraphQLOrderingTypeError(ValueError):
    """Raised when generated GraphQL ordering names are ambiguous."""


class GraphQLOrderingInputError(ValueError):
    """Raised when an ordering request cannot be converted safely."""

    @classmethod
    def non_list(cls) -> "GraphQLOrderingInputError":
        return cls("orderBy must be a list of order inputs.")

    @classmethod
    def unavailable_field(cls, field: object) -> "GraphQLOrderingInputError":
        return cls(f"Ordering field {field!r} is not available in this scope.")

    @classmethod
    def invalid_direction(cls, direction: object) -> "GraphQLOrderingInputError":
        return cls(f"Ordering direction {direction!r} must be ASC or DESC.")


class OrderingTypes:
    """The enum, input type, and Python field map for one GraphQL scope."""

    def __init__(
        self,
        field_enum: type[graphene.Enum],
        input_type: type[graphene.InputObjectType],
        field_map: Mapping[str, str],
    ) -> None:
        self.field_enum = field_enum
        self.input_type = input_type
        self.field_map = dict(field_map)


_type_cache: dict[tuple[str, str, tuple[tuple[str, str], ...]], OrderingTypes] = {}
_generated_type_keys: dict[str, tuple[str, str, tuple[tuple[str, str], ...]]] = {}


def clear_ordering_type_cache() -> None:
    """Clear generated ordering type state when the GraphQL registry resets."""
    _type_cache.clear()
    _generated_type_keys.clear()


def sortable_field_paths(
    manager_class: type[GeneralManager],
    manager_registry: Mapping[str, type[GeneralManager]],
) -> dict[str, str]:
    """Return GraphQL-spelled sortable fields mapped to Python paths."""
    paths: dict[str, str] = {}
    interface = manager_class.Interface
    for field_name, field_info in interface.get_attribute_types().items():
        field_type = field_info["type"]
        related_manager = resolve_general_manager_type(field_type, manager_registry)
        if related_manager is None:
            _add_field_path(paths, field_name)
            continue
        if field_info.get("relation_kind") == "collection":
            continue

        _add_field_mapping(
            paths,
            graphql_name_for_path(field_name),
            f"{field_name}__id",
        )
        for (
            related_name,
            related_info,
        ) in related_manager.Interface.get_attribute_types().items():
            if related_info.get("relation_kind") == "collection":
                continue
            if (
                resolve_general_manager_type(related_info["type"], manager_registry)
                is not None
            ):
                continue
            _add_field_path(paths, f"{field_name}__{related_name}")

    for (
        property_name,
        property_definition,
    ) in interface.get_graph_ql_properties().items():
        if property_definition.sortable:
            _add_field_path(paths, property_name)
    return paths


def graphql_name_for_path(python_path: str) -> str:
    """Return the generated GraphQL enum spelling for one Python path."""
    return "__".join(to_camel_case(segment) for segment in python_path.split("__"))


def create_ordering_types(
    manager_class: type[GeneralManager] | None,
    *,
    scope: str,
    field_paths: Mapping[str, str],
) -> OrderingTypes | None:
    """Create cached scope-specific order enum and input types.

    ``field_paths`` maps GraphQL enum member names to the Python paths passed to
    ``Bucket.sort``. A scope is part of the cache identity even when two scopes
    happen to expose the same fields, keeping future relation/group allowlists
    isolated.
    """
    if not field_paths:
        return None
    manager_identity = _manager_identity(manager_class)
    normalized_fields = tuple(sorted(field_paths.items()))
    cache_key = (manager_identity, scope, normalized_fields)
    cached = _type_cache.get(cache_key)
    if cached is not None:
        return cached

    type_prefix = _type_prefix(manager_class, scope, cache_key)
    enum_name = f"{type_prefix}OrderField"
    input_name = f"{type_prefix}OrderBy"
    enum = type(enum_name, (graphene.Enum,), dict(field_paths))
    input_type = type(
        input_name,
        (graphene.InputObjectType,),
        {
            "field": graphene.InputField(enum, required=True),
            "direction": graphene.InputField(
                OrderDirection,
                required=True,
                default_value=OrderDirection.ASC,
            ),
        },
    )
    result = OrderingTypes(enum, input_type, field_paths)
    _type_cache[cache_key] = result
    return result


def order_by_to_sort_terms(
    order_by: object,
    *,
    allowed_fields: Iterable[str] | None = None,
) -> tuple[SortTerm, ...]:
    """Convert typed GraphQL ordering objects into independently signed terms."""
    if order_by is None:
        return ()
    if not isinstance(order_by, (list, tuple)):
        raise GraphQLOrderingInputError.non_list()
    allowed = set(allowed_fields) if allowed_fields is not None else None
    signed_fields: list[str] = []
    for item in order_by:
        field = _input_value(item, "field")
        python_field = getattr(field, "value", field)
        if not isinstance(python_field, str) or (
            allowed is not None and python_field not in allowed
        ):
            raise GraphQLOrderingInputError.unavailable_field(python_field)
        direction = getattr(
            _input_value(item, "direction", OrderDirection.ASC),
            "value",
            _input_value(item, "direction", OrderDirection.ASC),
        )
        if direction not in {"ASC", "DESC"}:
            raise GraphQLOrderingInputError.invalid_direction(direction)
        signed_fields.append(
            f"-{python_field}" if direction == "DESC" else python_field
        )
    try:
        return normalize_ordering(signed_fields)
    except InvalidOrderingError as exc:
        raise GraphQLOrderingInputError(str(exc)) from exc


def _input_value(item: object, name: str, default: object | None = None) -> object:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _add_field_path(paths: dict[str, str], python_path: str) -> None:
    _add_field_mapping(paths, graphql_name_for_path(python_path), python_path)


def _add_field_mapping(
    paths: dict[str, str], graphql_name: str, python_path: str
) -> None:
    existing = paths.get(graphql_name)
    if existing is not None and existing != python_path:
        raise _enum_alias_collision(graphql_name, existing, python_path)
    paths[graphql_name] = python_path


def _manager_identity(manager_class: type[GeneralManager] | None) -> str:
    if manager_class is None:
        return "search"
    return f"{manager_class.__module__}.{manager_class.__qualname__}"


def _enum_alias_collision(
    graphql_name: str, existing: str, python_path: str
) -> GraphQLOrderingTypeError:
    """Build the explicit error raised for colliding generated enum members."""
    return GraphQLOrderingTypeError(
        f"GraphQL ordering enum member {graphql_name!r} maps to both "
        f"{existing!r} and {python_path!r}."
    )


def _type_prefix(
    manager_class: type[GeneralManager] | None,
    scope: str,
    cache_key: tuple[str, str, tuple[tuple[str, str], ...]],
) -> str:
    base = "Search" if manager_class is None else manager_class.__name__
    if scope:
        base = f"{base}{scope}"
    for type_name in (f"{base}OrderField", f"{base}OrderBy"):
        existing = _generated_type_keys.get(type_name)
        if existing is not None and existing != cache_key:
            digest = sha1(repr(cache_key).encode(), usedforsecurity=False).hexdigest()[
                :10
            ]
            return f"{base}{digest}"
    _generated_type_keys[f"{base}OrderField"] = cache_key
    _generated_type_keys[f"{base}OrderBy"] = cache_key
    return base
