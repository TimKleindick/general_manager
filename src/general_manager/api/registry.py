"""
GraphQLRegistry — a typed container for all mutable state managed by the
``GraphQL`` class.

Extracting the ``ClassVar`` registries into a single dataclass makes the
state explicit, documentable, and resettable:

    from general_manager.api.graphql import GraphQL

    # Reset all registry state between tests:
    GraphQL.reset_registry()

    # Inspect current state:
    snap = GraphQL.get_registry_snapshot()
    assert "MyManager" in snap.graphql_type_registry

The ``GraphQL`` class exposes its current registries through the standard
ClassVar attributes it has always had.  ``GraphQLRegistry`` is the canonical
data model / snapshot type; the two are kept in sync via ``reset_registry()``
and ``get_registry_snapshot()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import graphene

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager


@dataclass
class GraphQLRegistry:
    """
    Snapshot of the ``GraphQL`` class's mutable registry state.

    All fields mirror the ClassVars on the ``GraphQL`` class.  An instance can
    be obtained at any time via ``GraphQL.get_registry_snapshot()``, or a fresh
    empty instance can be used to reset state via ``GraphQL.reset_registry()``.
    Snapshot dictionaries are shallow copies: mutating the dictionary on a
    snapshot does not change the ``GraphQL`` class registry, but the generated
    Graphene classes and field/resolver objects inside those dictionaries are
    shared.
    """

    # Schema assembly output
    query_class: type[graphene.ObjectType] | None = None
    mutation_class: type[graphene.ObjectType] | None = None
    subscription_class: type[graphene.ObjectType] | None = None
    schema: graphene.Schema | None = None

    # Per-manager registries
    mutations: dict[str, object] = field(default_factory=dict)
    query_fields: dict[str, object] = field(default_factory=dict)
    subscription_fields: dict[str, object] = field(default_factory=dict)
    page_type_registry: dict[str, type[graphene.ObjectType]] = field(
        default_factory=dict
    )
    subscription_payload_registry: dict[str, type[graphene.ObjectType]] = field(
        default_factory=dict
    )
    graphql_type_registry: dict[str, type[graphene.ObjectType]] = field(
        default_factory=dict
    )
    graphql_filter_type_registry: dict[str, type[graphene.InputObjectType]] = field(
        default_factory=dict
    )
    graphql_capability_type_registry: dict[str, type[graphene.ObjectType]] = field(
        default_factory=dict
    )
    manager_registry: dict[str, type[GeneralManager]] = field(default_factory=dict)

    # Search
    search_union: type[graphene.Union] | None = None
    search_result_type: type[graphene.ObjectType] | None = None
