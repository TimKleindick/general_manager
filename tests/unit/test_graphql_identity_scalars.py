from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from uuid import UUID, uuid4

import graphene
import pytest
from django.db import models
from django.test.utils import isolate_apps
from graphql import GraphQLInputObjectType, Undefined, get_named_type

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_identifiers import build_identification_arguments
from general_manager.api.graphql_mutations import create_write_fields
from general_manager.api.graphql_search import get_filter_options
from general_manager.interface.capabilities.orm_utils.field_descriptors import (
    build_field_descriptors,
)
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.api.mutation import _build_manager_argument_field


@pytest.mark.parametrize(
    ("name", "primary_key", "python_type"),
    [
        ("id", models.AutoField(primary_key=True), int),
        ("id", models.BigAutoField(primary_key=True), int),
        ("id", models.BigIntegerField(primary_key=True), int),
        ("id", models.UUIDField(primary_key=True, default=uuid4), UUID),
        ("code", models.CharField(max_length=32, primary_key=True), str),
    ],
)
def test_django_primary_key_metadata_maps_reads_and_equality_filters_to_id(
    name: str, primary_key: models.Field, python_type: type
) -> None:
    with isolate_apps():
        model = type(
            "IdentityScalarMetadataRecord",
            (models.Model,),
            {
                "__module__": "general_manager.models",
                name: primary_key,
                "Meta": type("Meta", (), {"app_label": "general_manager"}),
            },
        )
        metadata = build_field_descriptors(SimpleNamespace(_model=model))[name].metadata

    assert metadata["type"] is python_type
    assert isinstance(
        GraphQL._map_field_to_graphene_read(python_type, name, metadata), graphene.ID
    )
    options = dict(
        get_filter_options(
            python_type, name, GraphQL._map_field_to_graphene_filter_input, metadata
        )
    )
    assert isinstance(options[name], graphene.ID)
    assert isinstance(options[f"{name}__exact"], graphene.ID)
    assert options[f"{name}__in"].of_type is graphene.ID


def test_ordinary_django_integer_with_id_like_name_retains_int_metadata() -> None:
    with isolate_apps():

        class NativeScalarMetadataRecord(models.Model):
            external_id = models.IntegerField()
            amount = models.BigIntegerField()

            class Meta:
                app_label = "general_manager"

        descriptors = build_field_descriptors(
            SimpleNamespace(_model=NativeScalarMetadataRecord)
        )
    metadata = descriptors["external_id"].metadata
    assert isinstance(
        GraphQL._map_field_to_graphene_read(int, "external_id", metadata), graphene.Int
    )
    options = dict(
        get_filter_options(
            int, "external_id", GraphQL._map_field_to_graphene_filter_input, metadata
        )
    )
    assert isinstance(options["external_id__exact"], graphene.Int)
    assert descriptors["amount"].metadata["graphql_scalar"] == "bigint"


def test_string_primary_key_preserves_native_pattern_filter_operators() -> None:
    with isolate_apps():

        class StringPatternMetadataRecord(models.Model):
            code = models.CharField(primary_key=True, max_length=32)

            class Meta:
                app_label = "general_manager"

        metadata = build_field_descriptors(
            SimpleNamespace(_model=StringPatternMetadataRecord)
        )["code"].metadata

    options = dict(
        get_filter_options(
            str, "code", GraphQL._map_field_to_graphene_filter_input, metadata
        )
    )
    for operator in ("contains", "icontains", "startswith", "endswith"):
        assert f"code__{operator}" in options
        assert isinstance(options[f"code__{operator}"], graphene.String)


@pytest.mark.parametrize("default", [models.NOT_PROVIDED, uuid4])
def test_write_fields_do_not_expose_sentinel_or_callable_as_graphql_defaults(
    default: object,
) -> None:
    class DefaultInterface:
        @staticmethod
        def get_attribute_types():
            return {
                "value": {
                    "type": str,
                    "is_required": default is models.NOT_PROVIDED,
                    "is_editable": True,
                    "is_derived": False,
                    "default": default,
                }
            }

    field = create_write_fields(DefaultInterface)["value"]
    assert field.kwargs["default_value"] is Undefined


def test_callable_defaults_are_omitted_without_evaluation_during_schema_build() -> None:
    calls: list[str] = []

    def default_label() -> str:
        calls.append("evaluated")
        return "generated"

    class DefaultInterface:
        @staticmethod
        def get_attribute_types():
            return {
                "label": {
                    "type": str,
                    "is_required": False,
                    "is_editable": True,
                    "is_derived": False,
                    "default": default_label,
                },
                "quantity": {
                    "type": int,
                    "is_required": False,
                    "is_editable": True,
                    "is_derived": False,
                    "default": 7,
                },
            }

    class Query(graphene.ObjectType):
        value = graphene.String()

    class Mutation(graphene.ObjectType):
        create = graphene.Field(
            graphene.String, **create_write_fields(DefaultInterface)
        )

    schema = graphene.Schema(query=Query, mutation=Mutation)
    arguments = schema.graphql_schema.mutation_type.fields["create"].args
    assert calls == []
    assert arguments["label"].default_value is Undefined
    assert arguments["quantity"].default_value == 7
    assert "quantity: Int = 7" in str(schema)
    assert "__schema" in schema.introspect()
    assert calls == []


def test_write_fields_keep_composite_manager_references_as_structured_inputs(
    monkeypatch,
) -> None:
    for registry in (
        "all_classes",
        "pending_graphql_interfaces",
        "pending_attribute_initialization",
    ):
        monkeypatch.setattr(
            GeneralManagerMeta, registry, list(getattr(GeneralManagerMeta, registry))
        )

    class CompositeWriteReference(GeneralManager):
        class Interface(CalculationInterface):
            id = Input(int)
            quantity = Input(int)
            period = Input(str)

    with isolate_apps():

        class OrdinaryWriteReference(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=32)

                class Meta:
                    app_label = "general_manager"

        class ReferenceWriteInterface:
            @staticmethod
            def get_attribute_types():
                attributes = {}
                for name, target, relation_kind in (
                    ("reference", CompositeWriteReference, "direct"),
                    ("reference_list", CompositeWriteReference, "collection"),
                    ("owner", OrdinaryWriteReference, "direct"),
                    ("owner_list", OrdinaryWriteReference, "collection"),
                ):
                    attributes[name] = {
                        "type": target,
                        "relation_kind": relation_kind,
                        "is_required": False,
                        "is_editable": True,
                        "is_derived": False,
                        "default": models.NOT_PROVIDED,
                    }
                return attributes

        class Query(graphene.ObjectType):
            value = graphene.String()

        class Mutation(graphene.ObjectType):
            create = graphene.Field(
                graphene.String, **create_write_fields(ReferenceWriteInterface)
            )

        schema = graphene.Schema(query=Query, mutation=Mutation)
        arguments = schema.graphql_schema.mutation_type.fields["create"].args
        for name in ("reference", "referenceList"):
            structured = get_named_type(arguments[name].type)
            assert isinstance(structured, GraphQLInputObjectType)
            assert str(structured.fields["id"].type) == "Int!"
            assert str(structured.fields["quantity"].type) == "Int!"
            assert str(structured.fields["period"].type) == "String!"
        assert str(arguments["referenceList"].type).startswith("[")
        assert str(arguments["owner"].type) == "ID"
        assert str(arguments["ownerList"].type) == "[ID]"


def test_plain_django_relation_write_fields_use_id_and_list_of_id() -> None:
    with isolate_apps():

        class PlainWriteRelationTarget(models.Model):
            code = models.CharField(primary_key=True, max_length=32)

            class Meta:
                app_label = "general_manager"

        class PlainWriteRelationSource(models.Model):
            owner = models.ForeignKey(
                PlainWriteRelationTarget, on_delete=models.CASCADE
            )
            labels = models.ManyToManyField(PlainWriteRelationTarget)

            class Meta:
                app_label = "general_manager"

        descriptors = build_field_descriptors(
            SimpleNamespace(_model=PlainWriteRelationSource)
        )
        assert descriptors["owner"].metadata["type"] is PlainWriteRelationTarget
        assert not hasattr(PlainWriteRelationTarget, "_general_manager_class")
        for name in ("owner", "labels_list"):
            assert isinstance(
                GraphQL._map_field_to_graphene_read(
                    PlainWriteRelationTarget, name, descriptors[name].metadata
                ),
                graphene.String,
            )

        class PlainRelationInterface:
            @staticmethod
            def get_attribute_types():
                return {
                    name: descriptor.metadata
                    for name, descriptor in descriptors.items()
                }

        class Query(graphene.ObjectType):
            value = graphene.String()

        class Mutation(graphene.ObjectType):
            create = graphene.Field(
                graphene.String, **create_write_fields(PlainRelationInterface)
            )

        schema = graphene.Schema(query=Query, mutation=Mutation)
        args = schema.graphql_schema.mutation_type.fields["create"].args
        assert str(args["owner"].type) == "ID!"
        assert str(args["labelsList"].type) == "[ID]"


@pytest.mark.parametrize("boundary", ["detail", "write", "write_list", "custom"])
def test_composite_orm_reference_preserves_id_scalar_and_accepts_large_id_variables(
    boundary: str, monkeypatch
) -> None:
    for registry in (
        "all_classes",
        "pending_graphql_interfaces",
        "pending_attribute_initialization",
    ):
        monkeypatch.setattr(
            GeneralManagerMeta, registry, list(getattr(GeneralManagerMeta, registry))
        )

    with isolate_apps():

        class CompositeOrmIdentityReference(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {
                    "id": Input(int),
                    "context": Input(str),
                }
                id = models.BigAutoField(primary_key=True)
                name = models.CharField(max_length=32)

                class Meta:
                    app_label = "general_manager"

        class CompositeOrmIdentitySelection(GeneralManager):
            class Interface(CalculationInterface):
                selection = Input(CompositeOrmIdentityReference)

        class CompositeOrmIdentityWriteSource(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                selection = models.ForeignKey(
                    CompositeOrmIdentityReference.Interface._model,
                    on_delete=models.CASCADE,
                )
                selections = models.ManyToManyField(
                    CompositeOrmIdentityReference.Interface._model
                )

                class Meta:
                    app_label = "general_manager"

        if boundary == "detail":
            arguments = build_identification_arguments(CompositeOrmIdentitySelection)
            argument_name = "selectionId"
            python_name = "selection_id"
        elif boundary == "custom":
            arguments = {
                "selection": _build_manager_argument_field(
                    CompositeOrmIdentityReference, required=True
                )
            }
            argument_name = python_name = "selection"
        else:
            fields = create_write_fields(CompositeOrmIdentityWriteSource.Interface)
            python_name = "selections_list" if boundary == "write_list" else "selection"
            arguments = {python_name: fields[python_name]}
            argument_name = (
                "selectionsList" if boundary == "write_list" else "selection"
            )

        captured: list[dict[str, object]] = []

        def capture(_root, _info, **kwargs):
            captured.append(kwargs)
            return "accepted"

        query_type = type(
            "CompositeOrmIdentityQuery",
            (graphene.ObjectType,),
            {
                "direct": graphene.String(
                    **build_identification_arguments(CompositeOrmIdentityReference)
                ),
                "reference": graphene.String(**arguments),
                "resolve_reference": capture,
            },
        )
        schema = graphene.Schema(query=query_type)
        query_fields = schema.graphql_schema.query_type.fields
        assert str(query_fields["direct"].args["id"].type) == "ID!"
        nested = get_named_type(query_fields["reference"].args[argument_name].type)
        assert isinstance(nested, GraphQLInputObjectType)
        assert str(nested.fields["id"].type) == "ID!"
        assert str(nested.fields["context"].type) == "String!"
        value = '{id: $id, context: "tenant"}'
        if boundary == "write_list":
            value = f"[{value}]"
        result = schema.execute(
            f"query($id: ID!) {{ reference({argument_name}: {value}) }}",
            variable_values={"id": "2147483648"},
        )
        assert result.errors is None
        assert result.data == {"reference": "accepted"}
        expected: object = {"id": "2147483648", "context": "tenant"}
        if boundary == "write_list":
            expected = [expected]
        assert captured == [{python_name: expected}]
