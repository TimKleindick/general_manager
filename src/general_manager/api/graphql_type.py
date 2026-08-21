"""Frozen value declarations for GraphQL output objects."""

from __future__ import annotations

from dataclasses import Field, dataclass, field
from typing import ClassVar, cast, dataclass_transform


@dataclass_transform(field_specifiers=(Field, field), frozen_default=True)
class GraphQLTypeMeta(type):
    """Create and register frozen dataclass-based GraphQL output declarations."""

    _registered_types: ClassVar[list[type[GraphQLType]]] = []

    def __new__(
        mcls: type[GraphQLTypeMeta],
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, object],
    ) -> type[GraphQLType]:
        created = cast(
            "type[GraphQLType]",
            type.__new__(mcls, name, bases, namespace),
        )
        if bases:
            created = dataclass(frozen=True)(created)
            mcls._registered_types.append(created)
        return created


class GraphQLType(metaclass=GraphQLTypeMeta):
    """Frozen annotated value exposed only as a GraphQL output object."""


def get_registered_graphql_types() -> tuple[type[GraphQLType], ...]:
    """Return the declared GraphQL output types in creation order."""
    return tuple(GraphQLTypeMeta._registered_types)


def _restore_registered_graphql_types(
    types: tuple[type[GraphQLType], ...],
) -> None:
    """Restore a declaration registry snapshot for test/integration cleanup.

    This is test infrastructure, not a public registry-reset API.
    """
    GraphQLTypeMeta._registered_types[:] = types
