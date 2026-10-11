from dataclasses import FrozenInstanceError, field, fields
from typing import Annotated, Any, get_type_hints
from uuid import UUID

import graphene
import pytest

from general_manager import GraphQL, GraphQLType
from general_manager.api.graphql_type import (
    _restore_registered_graphql_types,
    get_registered_graphql_types,
)
from general_manager.measurement.measurement import Measurement


BIG = 9007199254740993
KEY = UUID("00112233-4455-6677-8899-aabbccddeeff")


@pytest.fixture(autouse=True)
def restore_output_declarations(monkeypatch):
    declarations = get_registered_graphql_types()
    monkeypatch.setattr(GraphQL, "graphql_output_type_registry", {})
    yield
    _restore_registered_graphql_types(declarations)


def output_declaration(annotation, metadata=None):
    return type(
        "IdentityProjection530",
        (GraphQLType,),
        {
            "__module__": __name__,
            "__annotations__": {"value": annotation, "business_id": int},
            "value": field(
                metadata={"is_identifier": True} if metadata is None else metadata
            ),
        },
    )


def output_schema(declaration):
    generated = GraphQL.create_graphql_output_type(declaration)

    class Query(graphene.ObjectType):
        projection = graphene.Field(generated)

        @staticmethod
        def resolve_projection(root, _info):
            return root

    return graphene.Schema(query=Query)


@pytest.mark.parametrize(
    ("annotation", "native", "serialized", "graphql_type"),
    [
        (int, 7, "7", "ID!"),
        (int, 2147483648, "2147483648", "ID!"),
        (int, BIG, str(BIG), "ID!"),
        (int, 2**100 + 1, str(2**100 + 1), "ID!"),
        (str, "007", "007", "ID!"),
        (UUID, KEY, str(KEY), "ID!"),
        (int | None, BIG, str(BIG), "ID"),
        (str | None, "007", "007", "ID"),
        (UUID | None, KEY, str(KEY), "ID"),
        (int | None, None, None, "ID"),
        (UUID | None, None, None, "ID"),
        (list[int], [BIG], [str(BIG)], "[ID!]!"),
        (list[str], ["007"], ["007"], "[ID!]!"),
        (list[UUID], [KEY], [str(KEY)], "[ID!]!"),
        (set[int], {BIG}, [str(BIG)], "[ID!]!"),
        (set[UUID], {KEY}, [str(KEY)], "[ID!]!"),
        (tuple[int, ...], (BIG,), [str(BIG)], "[ID!]!"),
        (tuple[str, ...], ("007",), ["007"], "[ID!]!"),
        (list[int | None], [BIG, None], [str(BIG), None], "[ID]!"),
        (list[UUID | None], [KEY, None], [str(KEY), None], "[ID]!"),
        (list[int] | None, [BIG], [str(BIG)], "[ID!]"),
        (list[int] | None, None, None, "[ID!]"),
        (list[int | None] | None, [BIG, None], [str(BIG), None], "[ID]"),
        (list[int | None] | None, None, None, "[ID]"),
        (list[int], [], [], "[ID!]!"),
        (set[str], set(), [], "[ID!]!"),
        (tuple[UUID, ...], (), [], "[ID!]!"),
    ],
)
def test_declared_output_identity_preserves_native_value_and_nullability(
    annotation, native, serialized, graphql_type
):
    declaration = output_declaration(annotation)
    value = declaration(value=native, business_id=17)
    schema = output_schema(declaration)
    output = schema.graphql_schema.get_type("IdentityProjection530Type")
    assert str(output.fields["value"].type) == graphql_type
    assert str(output.fields["businessId"].type) == "Int!"
    result = schema.execute("{ projection { value businessId } }", root_value=value)
    assert result.errors is None
    assert result.data == {"projection": {"value": serialized, "businessId": 17}}
    assert value.value is native
    assert get_type_hints(declaration)["value"] == annotation
    assert fields(declaration)[0].metadata == {"is_identifier": True}
    with pytest.raises(FrozenInstanceError):
        value.value = "changed"


@pytest.mark.parametrize("metadata", [{}, {"is_identifier": False}, {"other": True}])
def test_unmarked_business_fields_keep_existing_scalars(metadata):
    declaration = output_declaration(int, metadata)
    value = declaration(value=7, business_id=17)
    schema = output_schema(declaration)
    output = schema.graphql_schema.get_type("IdentityProjection530Type")
    assert str(output.fields["value"].type) == "Int!"
    assert str(output.fields["businessId"].type) == "Int!"
    result = schema.execute("{ projection { value businessId } }", root_value=value)
    assert result.errors is None
    assert result.data == {"projection": {"value": 7, "businessId": 17}}


@pytest.mark.parametrize("invalid_flag", [None, 0, 1, "true", [], {"enabled": True}])
def test_identity_metadata_requires_boolean_before_registry_mutation(invalid_flag):
    declaration = output_declaration(int, {"is_identifier": invalid_flag})
    with pytest.raises(TypeError, match=r"IdentityProjection530\.value") as error:
        GraphQL.create_graphql_output_type(declaration)
    assert "bool" in str(error.value)
    assert GraphQL.graphql_output_type_registry == {}


@pytest.mark.parametrize(
    "annotation",
    [
        bool,
        float,
        Measurement,
        Any,
        object,
        list,
        list[bool],
        list[float],
        list[int, str],
        list[list[int]],
        tuple[int, int],
        tuple[int, str],
        int | str,
        Annotated[int, "metadata"],
        list[Annotated[int, "metadata"]],
        graphene.ID,
    ],
)
def test_invalid_identity_annotations_fail_with_field_name(annotation):
    declaration = output_declaration(annotation)
    with pytest.raises(TypeError, match=r"IdentityProjection530\.value"):
        GraphQL.create_graphql_output_type(declaration)
    assert GraphQL.graphql_output_type_registry == {}


def test_nested_output_object_cannot_be_marked_as_scalar_identity():
    class NestedValue530(GraphQLType):
        quantity: int

    GraphQL.create_graphql_output_type(NestedValue530)
    declaration = output_declaration(NestedValue530)
    with pytest.raises(TypeError, match=r"IdentityProjection530\.value"):
        GraphQL.create_graphql_output_type(declaration)
    assert "IdentityProjection530" not in GraphQL.graphql_output_type_registry


@pytest.mark.parametrize("metadata", [{}, {"is_identifier": False}])
def test_unmarked_uuid_keeps_existing_strict_rejection(metadata):
    declaration = output_declaration(UUID, metadata)
    with pytest.raises(TypeError, match=r"IdentityProjection530\.value"):
        GraphQL.create_graphql_output_type(declaration)


def test_inherited_identity_defaults_survive_registry_rebuild():
    class IdentityBase530(GraphQLType):
        record_key: int = field(metadata={"is_identifier": True})
        business_id: int = 17

    class IdentityChild530(IdentityBase530):
        record_keys: list[int] = field(
            default_factory=list, metadata={"is_identifier": True}
        )

    value = IdentityChild530(BIG)
    first = GraphQL.create_graphql_output_type(IdentityChild530)
    assert GraphQL.create_graphql_output_type(IdentityChild530) is first
    schema = output_schema(IdentityChild530)
    result = schema.execute(
        "{ projection { recordKey recordKeys businessId } }", root_value=value
    )
    assert result.errors is None
    assert result.data == {
        "projection": {
            "recordKey": str(BIG),
            "recordKeys": [],
            "businessId": 17,
        }
    }
    GraphQL.graphql_output_type_registry.clear()
    rebuilt = GraphQL.create_graphql_output_type(IdentityChild530)
    assert rebuilt is not first
    assert str(rebuilt._meta.fields["record_key"].type) == "ID!"
    assert str(rebuilt._meta.fields["record_keys"].type) == "[ID!]!"
    assert value.record_key == BIG and type(value.record_key) is int
    assert value.record_keys == []
    assert IdentityChild530(BIG).record_keys is not value.record_keys
