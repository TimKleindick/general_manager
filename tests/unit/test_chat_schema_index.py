from __future__ import annotations

from unittest.mock import patch

import graphene
import pytest

from general_manager.api.graphql import GraphQL
from general_manager.chat.schema_index import (
    build_schema_index,
    clear_schema_index_cache,
)


@pytest.fixture(autouse=True)
def _reset_schema_index_state():
    GraphQL.reset_registry()
    try:
        yield
    finally:
        clear_schema_index_cache()
        GraphQL.reset_registry()


def test_reset_registry_surfaces_schema_index_cache_clear_errors() -> None:
    with patch(
        "general_manager.chat.schema_index.clear_schema_index_cache",
        side_effect=RuntimeError("cache clear failed"),
    ):
        with pytest.raises(RuntimeError, match="cache clear failed"):
            GraphQL.reset_registry()


def test_build_schema_index_cache_key_changes_when_registry_contents_change() -> None:
    GraphQL.reset_registry()

    class PartManager:
        chat_exposed = True

    class PartType(graphene.ObjectType):
        """Inventory part."""

        name = graphene.String()

    GraphQL.manager_registry = {"PartManager": PartManager}  # type: ignore[assignment]
    GraphQL.graphql_type_registry = {"PartManager": PartType}

    first = build_schema_index()

    class ProjectManager:
        chat_exposed = True

    class ProjectType(graphene.ObjectType):
        """Tracked project."""

        title = graphene.String()

    GraphQL.manager_registry["ProjectManager"] = ProjectManager  # type: ignore[assignment]
    GraphQL.graphql_type_registry["ProjectManager"] = ProjectType

    second = build_schema_index()

    assert "ProjectManager" not in first
    assert "ProjectManager" in second


def test_build_schema_index_cache_key_changes_when_graphql_metadata_changes() -> None:
    class MaterialManager:
        chat_exposed = True

    class PartManager:
        chat_exposed = True

    class MaterialType(graphene.ObjectType):
        """Inventory material."""

        name = graphene.String()

    class PartType(graphene.ObjectType):
        """Inventory part."""

        name = graphene.String()

    class PartFilter(graphene.InputObjectType):
        name = graphene.String()

    GraphQL.manager_registry = {  # type: ignore[assignment]
        "MaterialManager": MaterialManager,
        "PartManager": PartManager,
    }
    GraphQL.graphql_type_registry = {
        "MaterialManager": MaterialType,
        "PartManager": PartType,
    }
    GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}

    first = build_schema_index()["PartManager"]

    PartType.__doc__ = "Updated part summary."
    PartType._meta.fields["sku"] = graphene.Field(graphene.String)
    PartType._meta.fields["material"] = graphene.Field(MaterialType)
    PartFilter._meta.fields["sku"] = graphene.InputField(graphene.String)

    second = build_schema_index()["PartManager"]

    assert first["description"] == "Inventory part."
    assert second["description"] == "Updated part summary."
    assert second["fields"] == ["name", "sku"]
    assert second["relations"] == [{"name": "material", "target": "MaterialManager"}]
    assert second["filters"] == ["name", "sku"]
