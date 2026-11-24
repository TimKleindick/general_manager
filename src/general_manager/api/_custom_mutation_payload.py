"""Collision checks for custom mutation payload types."""

from __future__ import annotations

from collections.abc import Iterable
from inspect import isfunction
from typing import Any

import graphene
from graphene.types.schema import TypeMap
from graphql import GraphQLSchema


class DuplicateCustomMutationPayloadTypeError(TypeError):
    """Raised before Graphene can silently reuse a conflicting type by name."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"Custom mutation payload type {name!r} conflicts with another "
            "GraphQL type. Rename the conflicting declaration."
        )


class _PayloadCheckedTypeMap(TypeMap):  # type: ignore[misc]
    def __init__(self, names: frozenset[str], **kwargs: Any) -> None:
        self._payload_names = names
        self._payload_bindings: dict[str, object] = {}
        super().__init__(**kwargs)

    def add_type(self, graphene_type: Any) -> Any:
        # Resolve a lazy declaration only once, as Graphene's TypeMap does.
        if isfunction(graphene_type):
            graphene_type = graphene_type()
        name = getattr(getattr(graphene_type, "_meta", None), "name", None)
        if name in self._payload_names:
            prior = self._payload_bindings.setdefault(name, graphene_type)
            if prior is not graphene_type:
                raise DuplicateCustomMutationPayloadTypeError(name)
        return super().add_type(graphene_type)


class _PayloadCheckedSchema(graphene.Schema):  # type: ignore[misc]
    """Keep Graphene's usual schema inputs, adding only payload-name checks."""

    def __init__(
        self,
        names: frozenset[str],
        *,
        query: Any = None,
        mutation: Any = None,
        subscription: Any = None,
        types: Any = None,
        directives: Any = None,
    ) -> None:
        self.query = query
        self.mutation = mutation
        self.subscription = subscription
        type_map = _PayloadCheckedTypeMap(
            names,
            query=query,
            mutation=mutation,
            subscription=subscription,
            types=types,
        )
        self.graphql_schema = GraphQLSchema(
            type_map.query,
            type_map.mutation,
            type_map.subscription,
            type_map.types,
            directives,
        )


def build_schema_with_custom_payload_names(
    mutations: Iterable[type[graphene.Mutation]], **kwargs: Any
) -> graphene.Schema:
    """Check renamed payload names without affecting other Graphene types."""
    names = frozenset(
        mutation._meta.name
        for mutation in mutations
        if vars(mutation).get("_gm_renamed_custom_payload") is True
    )
    if names:
        return _PayloadCheckedSchema(names, **kwargs)
    return graphene.Schema(**kwargs)
