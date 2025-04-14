from generalManager.src.interface.baseInterface import InterfaceBase, Bucket
from typing import (
    Any,
)
from django.test import TestCase
from datetime import datetime


class TestInterfaceBase(InterfaceBase):

    def __init__(self, *args: Any, **kwargs: Any):
        pass

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def update(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def deactivate(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def getData(self, search_date: datetime | None = None) -> Any:
        raise NotImplementedError

    @classmethod
    def getAttributeTypes(cls) -> dict[str, type]:
        raise NotImplementedError

    @classmethod
    def getAttributes(cls) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def filter(cls, **kwargs: Any) -> Bucket[Any]:
        raise NotImplementedError

    @classmethod
    def exclude(cls, **kwargs: Any) -> Bucket[Any]:
        raise NotImplementedError

    @classmethod
    def handleInterface(
        cls,
    ):
        raise NotImplementedError

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        """
        Returns the type of the field with the given name.
        """
        raise NotImplementedError


class InterfaceBaseTests(TestCase):
    def setUp(self):
        self.interface = TestInterfaceBase()

    def test_parseInputFieldsToIdentification(self):
        self.interface.input_fields = {
            "field1": "value1",
            "field2": "value2",
        }
        expected_result = {
            "field1": "value1",
            "field2": "value2",
        }
        result = self.interface.parseInputFieldsToIdentification()
        self.assertEqual(result, expected_result)
        self.assertIsInstance(result, dict)
        self.assertEqual(len(result), len(input_fields))
        for key in input_fields:
            self.assertIn(key, result)
            self.assertEqual(result[key], input_fields[key])
            self.assertIsInstance(result[key], str)
