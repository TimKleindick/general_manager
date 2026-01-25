from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from django.test import SimpleTestCase

from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.search.utils import (
    build_document_id,
    extract_value,
    normalize_identification,
)
from tests.utils.simple_manager_interface import BaseTestInterface, SimpleBucket


class _DummyInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
    data_store: ClassVar[dict[int, dict[str, str]]] = {1: {"name": "Alpha"}}

    def get_data(self, search_date=None):
        """
        Retrieve the stored data for this interface instance.
        
        Parameters:
            search_date (datetime.date | datetime.datetime | None): Optional date parameter (not used by this implementation; present for API compatibility).
        
        Returns:
            dict: The data dictionary from the class-level data_store corresponding to this instance's identification["id"].
        """
        return self.data_store[self.identification["id"]]

    @classmethod
    def get_attribute_types(cls):
        """
        Return a mapping of interface attribute names to their type descriptors.
        
        Returns:
            dict: Mapping from attribute name to a descriptor dict with a 'type' key holding the Python type (e.g., {"name": {"type": str}}).
        """
        return {"name": {"type": str}}

    @classmethod
    def get_attributes(cls):
        """
        Provide attribute resolvers for the interface.
        
        Returns:
            dict: Mapping from attribute name to a callable(interface) that returns the attribute value; here `"name"` maps to a function that returns `interface.get_data()["name"]`.
        """
        return {"name": lambda interface: interface.get_data()["name"]}

    @classmethod
    def filter(cls, **kwargs):
        """
        Produce a bucket of manager instances for the requested IDs.
        
        Parameters:
            id__in (iterable[int], optional): Iterable of IDs to include. If omitted, all IDs present in the class-level `data_store` are used.
        
        Returns:
            SimpleBucket: A bucket containing instances of the manager class (constructed with each selected `id`).
        """
        ids = kwargs.get("id__in") or list(cls.data_store.keys())
        return SimpleBucket(
            cls._parent_class, [cls._parent_class(id=val) for val in ids]
        )


class _DummyManager(GeneralManager):
    Interface = _DummyInterface


@dataclass
class _Nested:
    value: str


class SearchUtilsTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Register the dummy manager classes with GeneralmanagerConfig for tests.
        
        Registers `_DummyManager` in both the allowed and interface class lists used by the general manager configuration.
        """
        GeneralmanagerConfig.initialize_general_manager_classes(
            [_DummyManager],
            [_DummyManager],
        )

    def test_normalize_identification_is_deterministic(self) -> None:
        first = normalize_identification({"b": 2, "a": 1})
        second = normalize_identification({"a": 1, "b": 2})
        assert first == second

    def test_build_document_id_includes_type(self) -> None:
        doc_id = build_document_id("Project", {"id": 1})
        assert doc_id.startswith("Project:")

    def test_extract_value_from_mapping(self) -> None:
        data = {"nested": {"value": "alpha"}}
        assert extract_value(data, "nested__value") == "alpha"

    def test_extract_value_from_attribute(self) -> None:
        data = _Nested(value="beta")
        assert extract_value(data, "value") == "beta"

    def test_extract_value_from_list(self) -> None:
        data = [{"value": "alpha"}, {"value": "beta"}]
        assert extract_value(data, "value") == ["alpha", "beta"]

    def test_extract_value_from_bucket(self) -> None:
        items = [_DummyManager(id=1), _DummyManager(id=2)]
        bucket = SimpleBucket(_DummyManager, items)
        assert extract_value(bucket, "identification__id") == [1, 2]

    def test_extract_value_missing_returns_none(self) -> None:
        data = {"value": "alpha"}
        assert extract_value(data, "missing") is None