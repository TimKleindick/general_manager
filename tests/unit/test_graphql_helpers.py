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
        """
        Retrieve the stored data record for this instance's id.
        
        Parameters:
            search_date (optional): Ignored; accepted for API compatibility.
        
        Returns:
            dict: The data record associated with this instance's id.
        """
        return self.data_store[self.identification["id"]]

    @classmethod
    def get_attribute_types(cls):
        """
        Provide the mapping of attribute names to their GraphQL-exposed types.
        
        Returns:
            dict: A mapping where each key is an attribute name and each value is a dict containing type information, e.g. {"name": {"type": str}}.
        """
        return {"name": {"type": str}}

    @classmethod
    def get_attributes(cls):
        """
        Provide attribute extractors for the interface.
        
        Returns:
            dict: Mapping of attribute names to callables that accept an interface instance and return the attribute's value (e.g., `"name"` -> callable that returns the interface's `"name"`).
        """
        return {"name": lambda interface: interface.get_data()["name"]}

    @classmethod
    def filter(cls, **kwargs):
        """
        Produce a SimpleBucket of parent-manager instances matching the provided ids or all stored ids.
        
        Parameters:
            id__in (iterable, optional): Iterable of ids to include; if omitted, all keys from cls.data_store are used.
        
        Returns:
            SimpleBucket: A bucket containing instances of the parent manager class constructed for each selected id.
        """
        ids = kwargs.get("id__in") or list(cls.data_store.keys())
        return SimpleBucket(
            cls._parent_class, [cls._parent_class(id=val) for val in ids]
        )


class _DummyPermission(BasePermission):
    def check_permission(self, *args, **kwargs) -> None:
        """
        Allow access unconditionally by performing no permission checks.
        
        This implementation never raises and is intended to grant permission for all calls.
        """
        return None

    def get_permission_filter(self):
        """
        Provide the permission filter used for read operations.
        
        Returns:
            list[dict]: A list containing a single filter specification mapping `"filter"` to `{"status": "public"}` and `"exclude"` to an empty dict.
        """
        return [{"filter": {"status": "public"}, "exclude": {}}]


class _DummyManager(GeneralManager):
    Interface = _DummyInterface
    Permission = _DummyPermission


class _Info:
    def __init__(self) -> None:
        """
        Create a minimal info container with a context object exposing a `user` attribute.
        
        Sets self.context to a lightweight object with a single attribute `user` initialized to a generic object instance.
        """
        self.context = type("Context", (), {"user": object()})()


class GraphQLHelperTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Initialize GeneralmanagerConfig with the test dummy manager classes used by the test case.
        
        This configures both interface and permission manager registries to use _DummyManager so each test runs with the same minimal manager implementations.
        """
        GeneralmanagerConfig.initialize_general_manager_classes(
            [_DummyManager],
            [_DummyManager],
        )

    def test_measurement_scalar_invalid(self) -> None:
        """
        Verify that serializing a non-measurement string with MeasurementScalar raises an InvalidMeasurementValueError.
        
        This test calls MeasurementScalar.serialize with an invalid value and expects an InvalidMeasurementValueError to be raised.
        """
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
        """
        Verify GraphQL-related error classes produce the expected human-readable messages.
        
        Asserts that:
        - `InvalidGeneralManagerClassError(GeneralManager)` message ends with "GeneralManager to create a GraphQL interface."
        - `UnsupportedGraphQLFieldTypeError(dict)` message starts with "GraphQL does not support dict fields"
        - `MissingManagerIdentifierError()` message equals "id is required."
        - `MissingChannelLayerError()` message starts with "No channel layer configured"
        """
        assert str(InvalidGeneralManagerClassError(GeneralManager)).endswith(
            "GeneralManager to create a GraphQL interface."
        )
        assert str(UnsupportedGraphQLFieldTypeError(dict)).startswith(
            "GraphQL does not support dict fields"
        )
        assert str(MissingManagerIdentifierError()) == "id is required."
        assert str(MissingChannelLayerError()).startswith("No channel layer configured")