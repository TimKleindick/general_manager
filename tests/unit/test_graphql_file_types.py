from __future__ import annotations

from typing import ClassVar

import graphene
import pytest
from django.db.models import NOT_PROVIDED
from graphql import Undefined
from graphql.language import IntValueNode, StringValueNode

from general_manager.api.graphql import GraphQL
from general_manager.interface.base_interface import AttributeTypedDict
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
    fields = _introspect_type(StoredImage)

    assert fields["width"] == {"kind": "SCALAR", "name": "Int", "ofType": None}
    assert fields["height"] == {"kind": "SCALAR", "name": "Int", "ofType": None}
    assert fields["status"] == {
        "kind": "NON_NULL",
        "name": None,
        "ofType": {"kind": "ENUM", "name": "StoredFileStatus"},
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
