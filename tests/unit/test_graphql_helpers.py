from __future__ import annotations

import pytest
from django.test import SimpleTestCase
from graphql.language.ast import StringValueNode

from general_manager.api.graphql import (
    InvalidGeneralManagerClassError,
    InvalidMeasurementValueError,
    MeasurementScalar,
    MissingChannelLayerError,
    MissingManagerIdentifierError,
    UnsupportedGraphQLFieldTypeError,
    get_read_permission_filter,
)
from typing import ClassVar

from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.permission.base_permission import BasePermission
from tests.utils.simple_manager_interface import BaseTestInterface, SimpleBucket


class _DummyInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
    data_store: ClassVar[dict[int, dict[str, str]]] = {1: {"name": "Alpha"}}

    def get_data(self, search_date=None):
        return self.data_store[self.identification["id"]]

    @classmethod
    def get_attribute_types(cls):
        return {"name": {"type": str}}

    @classmethod
    def get_attributes(cls):
        return {"name": lambda interface: interface.get_data()["name"]}

    @classmethod
    def filter(cls, **kwargs):
        ids = kwargs.get("id__in") or list(cls.data_store.keys())
        return SimpleBucket(
            cls._parent_class, [cls._parent_class(id=val) for val in ids]
        )


class _DummyPermission(BasePermission):
    def check_permission(self, *args, **kwargs) -> None:
        return None

    def get_permission_filter(self):
        return [{"filter": {"status": "public"}, "exclude": {}}]


class _DummyManager(GeneralManager):
    Interface = _DummyInterface
    Permission = _DummyPermission


class _Info:
    def __init__(self) -> None:
        self.context = type("Context", (), {"user": object()})()


class GraphQLHelperTests(SimpleTestCase):
    def setUp(self) -> None:
        GeneralmanagerConfig.initialize_general_manager_classes(
            [_DummyManager],
            [_DummyManager],
        )

    def test_measurement_scalar_invalid(self) -> None:
        with pytest.raises(InvalidMeasurementValueError):
            MeasurementScalar.serialize("not-a-measurement")  # type: ignore[arg-type]

    def test_measurement_scalar_parse_literal(self) -> None:
        node = StringValueNode(value="10 m")
        assert MeasurementScalar.parse_literal(node) is not None
        assert MeasurementScalar.parse_literal(object()) is None

    def test_permission_filter_helper(self) -> None:
        info = _Info()
        filters = get_read_permission_filter(_DummyManager, info)
        assert filters == [({"status": "public"}, {})]

    def test_graphql_error_types(self) -> None:
        assert str(InvalidGeneralManagerClassError(GeneralManager)).endswith(
            "GeneralManager to create a GraphQL interface."
        )
        assert str(UnsupportedGraphQLFieldTypeError(dict)).startswith(
            "GraphQL does not support dict fields"
        )
        assert str(MissingManagerIdentifierError()) == "id is required."
        assert str(MissingChannelLayerError()).startswith("No channel layer configured")
