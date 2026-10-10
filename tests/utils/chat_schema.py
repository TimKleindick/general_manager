"""Executable schema fixtures for read-contract tests (no production fallback)."""

from __future__ import annotations

import graphene
from general_manager.api.graphql import GraphQL


def registered_schema():
    class PageInfo(graphene.ObjectType):
        total_count = graphene.Int(required=True)

    fields = {}
    for manager, output in GraphQL.graphql_type_registry.items():
        page = type(
            manager + "TestPage",
            (graphene.ObjectType,),
            {"items": graphene.List(output), "page_info": graphene.Field(PageInfo)},
        )
        arguments = {"page_size": graphene.Int(), "page": graphene.Int()}
        filter_type = GraphQL.graphql_filter_type_registry.get(manager)
        if filter_type:
            arguments["filter"] = graphene.Argument(filter_type)
        fields[manager.lower() + "List"] = graphene.Field(page, **arguments)
    return graphene.Schema(query=type("Query", (graphene.ObjectType,), fields))


def install_registered_schema():
    GraphQL._schema = registered_schema()
