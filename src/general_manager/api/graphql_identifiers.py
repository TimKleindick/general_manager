"""Share declared constructor arguments between generated GraphQL operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

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
from general_manager.manager.general_manager import GeneralManager


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
        manager_type = resolve_general_manager_type(
            input_field.type, get_graphql_manager_registry()
        )
        if manager_type is not None:
            # Reuse custom mutation support for structured/composite manager
            # references. Import lazily after GraphQL startup to avoid a cycle.
            from general_manager.api.mutation import (
                _build_manager_argument_field,
                _uses_single_id_input,
            )

            arguments[f"{name}_id"] = (
                graphene.Argument(graphene.ID, required=required)
                if _uses_single_id_input(manager_type)
                else _build_manager_argument_field(manager_type, required=required)
            )
        else:
            scalar = map_identification_scalar(manager_class, name, input_field.type)
            arguments[name] = graphene.Argument(scalar, required=required)
    return arguments


def pop_identification(
    arguments: dict[str, object], payload: dict[str, object]
) -> dict[str, object]:
    """Extract only declared constructor values, preserving omission for defaults."""
    return {name: payload.pop(name) for name in arguments if name in payload}
