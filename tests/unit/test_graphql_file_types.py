from __future__ import annotations

from typing import ClassVar

import graphene
import pytest
from django.db.models import NOT_PROVIDED
from graphql import Undefined
from graphql.language import IntValueNode, StringValueNode

from general_manager.api.graphql import GraphQL
from general_manager.interface.base_interface import AttributeTypedDict
from general_manager.manager.general_manager import GeneralManager
from general_manager.uploads.graphql_types import (
    StoredFile,
    StoredFileStatusEnum,
    StoredImage,
    UploadToken,
)
from general_manager.uploads.types import StoredFileStatus


class _FileInterface:
    attribute_types: ClassVar[dict[str, AttributeTypedDict]] = {
        "document": {
            "type": str,
            "orm_field_kind": "file",
            "file_clearable": False,
            "is_required": True,
            "is_derived": False,
            "default": NOT_PROVIDED,
            "is_editable": True,
        },
        "photo": {
            "type": str,
            "orm_field_kind": "image",
            "file_clearable": True,
            "is_required": False,
            "is_derived": False,
            "default": None,
            "is_editable": True,
        },
        "label": {
            "type": str,
            "is_required": False,
            "is_derived": False,
            "default": None,
            "is_editable": True,
        },
    }

    @classmethod
    def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
        return cls.attribute_types

    @staticmethod
    def get_graph_ql_properties() -> dict[str, object]:
        return {}


def _introspect_type(type_: type[graphene.ObjectType]) -> dict[str, object]:
    class Query(graphene.ObjectType):
        value = graphene.Field(type_)

    schema = graphene.Schema(query=Query)
    result = schema.execute(
        """
        query TypeShape($name: String!) {
          __type(name: $name) {
            fields {
              name
              type {
                kind
                name
                ofType { kind name }
              }
            }
          }
        }
        """,
        variable_values={"name": type_.__name__},
    )

    assert result.errors is None
    assert result.data is not None
    type_data = result.data["__type"]
    assert type_data is not None
    return {field["name"]: field["type"] for field in type_data["fields"]}


def test_upload_token_accepts_only_non_empty_strings_without_path_semantics() -> None:
    assert UploadToken.parse_value("upload-token") == "upload-token"
    assert UploadToken.parse_value("../not-interpreted-as-a-path") == (
        "../not-interpreted-as-a-path"
    )

    for invalid_value in ("", 7, None):
        with pytest.raises((TypeError, ValueError)):
            UploadToken.parse_value(invalid_value)


def test_upload_token_rejects_empty_and_non_string_literals() -> None:
    assert UploadToken.parse_literal(StringValueNode(value="token")) == "token"

    with pytest.raises(ValueError):
        UploadToken.parse_literal(StringValueNode(value=""))

    assert UploadToken.parse_literal(IntValueNode(value="7")) is Undefined


def test_upload_token_serializes_non_empty_strings() -> None:
    assert UploadToken.serialize("token") == "token"


def test_upload_token_rejects_non_string_output() -> None:
    with pytest.raises(TypeError, match="UploadToken must be a string"):
        UploadToken.serialize(7)


def test_stored_file_status_enum_is_derived_from_domain_status() -> None:
    class Query(graphene.ObjectType):
        value = graphene.Field(StoredFile)

    schema = graphene.Schema(query=Query)
    result = schema.execute(
        '{ __type(name: "StoredFileStatus") { enumValues { name } } }'
    )

    assert result.errors is None
    assert result.data is not None
    assert [value["name"] for value in result.data["__type"]["enumValues"]] == [
        member.name for member in StoredFileStatus
    ]
    assert issubclass(StoredFileStatusEnum, graphene.Enum)


def test_stored_file_output_schema_has_stable_nullable_shape() -> None:
    fields = _introspect_type(StoredFile)

    assert fields == {
        "name": {
            "kind": "NON_NULL",
            "name": None,
            "ofType": {"kind": "SCALAR", "name": "String"},
        },
        "size": {"kind": "SCALAR", "name": "Int", "ofType": None},
        "contentType": {"kind": "SCALAR", "name": "String", "ofType": None},
        "downloadUrl": {"kind": "SCALAR", "name": "String", "ofType": None},
        "expiresAt": {"kind": "SCALAR", "name": "DateTime", "ofType": None},
        "status": {
            "kind": "NON_NULL",
            "name": None,
            "ofType": {"kind": "ENUM", "name": "StoredFileStatus"},
        },
    }


def test_stored_image_output_schema_adds_nullable_dimensions() -> None:
    file_fields = _introspect_type(StoredFile)
    image_fields = _introspect_type(StoredImage)

    assert image_fields == {
        **file_fields,
        "width": {"kind": "SCALAR", "name": "Int", "ofType": None},
        "height": {"kind": "SCALAR", "name": "Int", "ofType": None},
    }


def test_graphql_read_fields_use_typed_file_objects() -> None:
    class Query(graphene.ObjectType):
        document = GraphQL._map_field_to_graphene_read(
            str,
            "document",
            _FileInterface.attribute_types["document"],
        )
        photo = GraphQL._map_field_to_graphene_read(
            str,
            "photo",
            _FileInterface.attribute_types["photo"],
        )
        label = GraphQL._map_field_to_graphene_read(
            str,
            "label",
            _FileInterface.attribute_types["label"],
        )

    schema = graphene.Schema(query=Query)
    result = schema.execute(
        '{ __type(name: "Query") { fields { name type { name } } } }'
    )

    assert result.errors is None
    assert result.data is not None
    fields = {
        field["name"]: field["type"]["name"]
        for field in result.data["__type"]["fields"]
    }
    assert fields == {
        "document": "StoredFile",
        "photo": "StoredImage",
        "label": "String",
    }


def test_file_manager_schema_keeps_filter_inputs_as_strings() -> None:
    class FileManager(GeneralManager):
        pass

    FileManager.Interface = _FileInterface  # type: ignore[assignment]
    GraphQL.graphql_filter_type_registry.clear()
    filter_type = GraphQL._create_filter_options(FileManager)
    assert filter_type is not None

    class FileManagerType(graphene.ObjectType):
        document = GraphQL._map_field_to_graphene_read(
            str,
            "document",
            _FileInterface.attribute_types["document"],
        )
        photo = GraphQL._map_field_to_graphene_read(
            str,
            "photo",
            _FileInterface.attribute_types["photo"],
        )

    class Query(graphene.ObjectType):
        files = graphene.List(
            FileManagerType,
            filter=graphene.Argument(filter_type),
            exclude=graphene.Argument(filter_type),
        )

    schema = graphene.Schema(query=Query, auto_camelcase=False)
    result = schema.execute(
        """
        {
          query: __type(name: "Query") {
            fields { name args { name type { kind name } } }
          }
          filters: __type(name: "FileManagerFilterTypeDepth1") {
            inputFields { name type { kind name ofType { kind name } } }
          }
        }
        """
    )

    assert result.errors is None
    assert result.data is not None
    query_fields = {field["name"]: field for field in result.data["query"]["fields"]}
    assert {
        argument["name"]: argument["type"] for argument in query_fields["files"]["args"]
    } == {
        "filter": {
            "kind": "INPUT_OBJECT",
            "name": "FileManagerFilterTypeDepth1",
        },
        "exclude": {
            "kind": "INPUT_OBJECT",
            "name": "FileManagerFilterTypeDepth1",
        },
    }

    input_fields = {
        field["name"]: field["type"] for field in result.data["filters"]["inputFields"]
    }
    scalar_string = {"kind": "SCALAR", "name": "String", "ofType": None}
    list_of_strings = {
        "kind": "LIST",
        "name": None,
        "ofType": {"kind": "SCALAR", "name": "String"},
    }
    for field_name in ("document", "photo"):
        assert input_fields[field_name] == scalar_string
        for lookup in ("exact", "icontains", "contains", "startswith", "endswith"):
            assert input_fields[f"{field_name}__{lookup}"] == scalar_string
        assert input_fields[f"{field_name}__in"] == list_of_strings


def test_graphql_write_fields_use_upload_tokens_and_preserve_update_optionality() -> (
    None
):
    create_fields = GraphQL.create_write_fields(_FileInterface)
    update_fields = GraphQL.create_write_fields(_FileInterface, require_fields=False)

    assert isinstance(create_fields["document"], UploadToken)
    assert isinstance(create_fields["photo"], UploadToken)
    assert isinstance(create_fields["label"], graphene.String)
    assert isinstance(update_fields["document"], UploadToken)

    create_input = type(
        "CreateFileInput",
        (graphene.InputObjectType,),
        dict(create_fields),
    )
    update_input = type(
        "UpdateFileInput",
        (graphene.InputObjectType,),
        dict(update_fields),
    )

    class Query(graphene.ObjectType):
        value = graphene.String(
            create=graphene.Argument(create_input),
            update=graphene.Argument(update_input),
        )

    schema = graphene.Schema(query=Query)
    result = schema.execute(
        """
        {
          create: __type(name: "CreateFileInput") {
            inputFields { name type { kind name ofType { kind name } } }
          }
          update: __type(name: "UpdateFileInput") {
            inputFields { name type { kind name ofType { kind name } } }
          }
        }
        """
    )

    assert result.errors is None
    assert result.data is not None
    create_types = {
        field["name"]: field["type"] for field in result.data["create"]["inputFields"]
    }
    update_types = {
        field["name"]: field["type"] for field in result.data["update"]["inputFields"]
    }
    assert create_types["document"] == {
        "kind": "NON_NULL",
        "name": None,
        "ofType": {"kind": "SCALAR", "name": "UploadToken"},
    }
    assert create_types["photo"] == {
        "kind": "SCALAR",
        "name": "UploadToken",
        "ofType": None,
    }
    assert create_types["label"] == {
        "kind": "SCALAR",
        "name": "String",
        "ofType": None,
    }
    assert update_types["document"] == {
        "kind": "SCALAR",
        "name": "UploadToken",
        "ofType": None,
    }
