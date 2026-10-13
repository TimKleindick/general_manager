"""Share declared constructor arguments between generated GraphQL operations."""

from __future__ import annotations

from collections.abc import Mapping
from types import UnionType
from typing import Annotated, Union, cast, get_args, get_origin
from uuid import UUID

import graphene
from django.db import models

from general_manager.api.graphql_errors import (
    GrapheneBaseTypeClass,
    map_field_to_graphene_base_type,
)
from general_manager.api.graphql_relations import (
    get_graphql_manager_registry,
    resolve_general_manager_type,
)
from general_manager.api.graphql_output import GraphQLOutputAnnotationError
from general_manager.manager.general_manager import GeneralManager


def property_identifier_type(
    annotation: object, *, owner_name: str, field_name: str
) -> tuple[type, bool]:
    """Validate an explicit native identity hint and return its type and shape.

    This shared interpretation lets output and filter builders consume the
    descriptor's public ``is_identifier`` flag without changing its annotation
    or the native resolver value. Optional wrappers do not add non-null output
    constraints to legacy manager properties.
    """
    original_annotation = annotation

    def unwrap(hint: object) -> object:
        while get_origin(hint) is Annotated:
            hint = get_args(hint)[0]
        if get_origin(hint) in (Union, UnionType):
            members = get_args(hint)
            if len(members) == 2 and type(None) in members:
                return unwrap(next(t for t in members if t is not type(None)))
        return hint

    annotation = unwrap(annotation)
    origin = get_origin(annotation)
    is_collection = origin in (list, tuple, set)
    if is_collection:
        elements = get_args(annotation)
        if origin is tuple and len(elements) == 2 and elements[1] is Ellipsis:
            elements = elements[:1]
        if elements and (origin is tuple or len(elements) == 1):
            native_type = unwrap(elements[0])
            if native_type in (int, str, UUID) and all(
                unwrap(element) is native_type for element in elements
            ):
                return cast(type, native_type), True
    elif annotation in (int, str, UUID):
        return cast(type, annotation), False
    raise GraphQLOutputAnnotationError(owner_name, field_name, original_annotation)


def is_orm_identifier_input(manager_class: type[GeneralManager], name: str) -> bool:
    """Identify the ORM constructor alias addressing the actual model PK."""
    model = getattr(manager_class.Interface, "_model", None)
    return name == "id" and isinstance(model, type) and issubclass(model, models.Model)


def map_identification_scalar(
    manager_class: type[GeneralManager], name: str, input_type: type
) -> GrapheneBaseTypeClass:
    """Map actual scalar identities consistently, including nested references."""
    metadata: Mapping[str, object] = manager_class.Interface.get_attribute_types().get(
        name, {}
    )
    if is_orm_identifier_input(manager_class, name) or metadata.get("is_identifier"):
        return cast(GrapheneBaseTypeClass, graphene.ID)
    return map_field_to_graphene_base_type(
        input_type, cast(str | None, metadata.get("graphql_scalar"))
    )


def build_identification_arguments(
    manager_class: type[GeneralManager], *, for_mutation: bool = False
) -> dict[str, object]:
    """Keep named business inputs and use ID only for scalar identities/references."""
    interface = manager_class.Interface
    arguments: dict[str, object] = {}
    for name, input_field in interface.input_fields.items():
        # The ORM constructor's id input addresses model._meta.pk. Its public
        # name is independent of the actual primary-key column name.
        is_orm_id = is_orm_identifier_input(manager_class, name)
        required = input_field.required or (for_mutation and is_orm_id)
        is_collection = getattr(input_field, "is_collection", False) is True
        element_type = (
            input_field.collection_element_type if is_collection else input_field.type
        )
        assert element_type is not None
        manager_type = resolve_general_manager_type(
            element_type, get_graphql_manager_registry()
        )
        if manager_type is not None:
            # Reuse custom mutation support for structured/composite manager
            # references. Import lazily after GraphQL startup to avoid a cycle.
            from general_manager.api.mutation import (
                _build_manager_argument_field,
                _uses_single_id_input,
                _get_or_create_manager_input_type,
            )

            if is_collection:
                reference = (
                    graphene.ID
                    if _uses_single_id_input(manager_type)
                    else _get_or_create_manager_input_type(manager_type)
                )
                arguments[f"{name}_id"] = graphene.Argument(
                    graphene.List(graphene.NonNull(reference)), required=required
                )
                continue
            arguments[f"{name}_id"] = (
                graphene.Argument(graphene.ID, required=required)
                if _uses_single_id_input(manager_type)
                else _build_manager_argument_field(manager_type, required=required)
            )
        else:
            scalar = map_identification_scalar(manager_class, name, element_type)
            arguments[name] = graphene.Argument(
                graphene.List(graphene.NonNull(scalar)) if is_collection else scalar,
                required=required,
            )
    return arguments


def pop_identification(
    arguments: dict[str, object], payload: dict[str, object]
) -> dict[str, object]:
    """Extract only declared constructor values, preserving omission for defaults."""
    return {name: payload.pop(name) for name in arguments if name in payload}
