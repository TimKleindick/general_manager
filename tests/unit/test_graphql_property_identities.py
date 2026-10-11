from types import SimpleNamespace
from typing import Annotated, Any, ClassVar
from unittest.mock import patch
from uuid import UUID

import graphene
import pytest

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_type import GraphQLType
from general_manager.api.property import graph_ql_property
from general_manager.bucket.base_bucket import Bucket
from general_manager.manager import GeneralManager
from general_manager.measurement import Measurement


def property_with_annotation(annotation, *, is_identifier=True, **options):
    def getter(_instance):
        return 7

    getter.__annotations__["return"] = annotation
    return graph_ql_property(is_identifier=is_identifier, **options)(getter)


def generate_property_type(prop, field_name="key"):
    class IdentityAnnotationInterface:
        input_fields: ClassVar[dict] = {}

        @staticmethod
        def get_attribute_types():
            return {}

        @staticmethod
        def get_graph_ql_properties():
            return {field_name: prop}

    manager = SimpleNamespace(
        __name__="IdentityAnnotationManager", Interface=IdentityAnnotationInterface
    )
    with (
        patch.dict(GraphQL.graphql_type_registry),
        patch.dict(GraphQL.manager_registry),
        patch.object(GraphQL, "_add_queries_to_schema"),
        patch.object(GraphQL, "_add_subscription_field"),
        patch("general_manager.api.graphql.get_graphql_capabilities", return_value=()),
    ):
        GraphQL.create_graphql_interface(manager)
        return GraphQL.graphql_type_registry[manager.__name__]


@pytest.mark.parametrize(
    "annotation", [int, str, UUID, int | None, Annotated[int, "key"]]
)
def test_explicit_identity_scalar_preserves_native_annotation(annotation):
    prop = property_with_annotation(annotation)
    assert prop.is_identifier is True
    assert generate_property_type(prop)._meta.fields["key"].type is graphene.ID


@pytest.mark.parametrize(
    "annotation",
    [
        list[int],
        list[UUID],
        set[str],
        tuple[int, ...],
        tuple[int, int],
        list[int | None] | None,
        Annotated[list[str], "keys"],
    ],
)
def test_explicit_identity_collections_map_to_nullable_id_list(annotation):
    prop = property_with_annotation(annotation)
    field = generate_property_type(prop)._meta.fields["key"]
    assert isinstance(field.type, graphene.List)
    assert field.type.of_type is graphene.ID


@pytest.mark.parametrize(
    "annotation",
    [
        bool,
        float,
        Any,
        object,
        list,
        list[int, int],
        set[str, str],
        list[list[int]],
        tuple[int, str],
        int | str,
        list[int | str],
        dict[str, int],
        Measurement,
        GeneralManager,
        GraphQLType,
        "UnknownIdentityType",
        type(None),
    ],
)
def test_unsupported_identity_declaration_fails_schema_with_field_context(annotation):
    prop = property_with_annotation(annotation)
    with pytest.raises(TypeError, match=r"IdentityAnnotationManager\.key"):
        generate_property_type(prop)


def test_bucket_identity_is_rejected_before_relation_mapping():
    prop = property_with_annotation(Bucket[GeneralManager])
    with pytest.raises(TypeError, match=r"IdentityAnnotationManager\.key_list"):
        generate_property_type(prop, "key_list")


@pytest.mark.parametrize("name", ["id", "external_id", "customer_id", "quantity"])
def test_business_property_identity_flag_defaults_to_false(name):
    def getter(_instance) -> int:
        return 7

    getter.__name__ = name
    prop = graph_ql_property(getter)
    assert prop.is_identifier is False
    assert prop.graphql_type_hint is int
    assert generate_property_type(prop, name)._meta.fields[name].type is graphene.Int
