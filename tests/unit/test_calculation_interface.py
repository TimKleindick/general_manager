from typing import ClassVar
from unittest.mock import patch

from django.test import TestCase
from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.interface import CalculationInterface
from general_manager.interface.capabilities.calculation import (
    CalculationLifecycleCapability,
    CalculationQueryCapability,
)
from general_manager.interface.capabilities.configuration import (
    InterfaceCapabilityConfig,
)
from general_manager.manager.input import Input


class DummyCalculationInterface(CalculationInterface):
    input_fields: ClassVar[dict[str, Input]] = {
        "field1": Input(type=str),
        "field2": Input(type=int, required=False),
    }


class DummyGeneralManager:
    Interface = DummyCalculationInterface


DummyCalculationInterface._parent_class = DummyGeneralManager


class CustomQueryCapability(CalculationQueryCapability):
    def __init__(self, label: str):
        """
        Initialize the capability and store a human-readable label on the instance.

        Parameters:
            label (str): Human-readable label assigned to the capability.
        """
        super().__init__()
        self.label = label


class CustomCalculationInterface(DummyCalculationInterface):
    configured_capabilities: ClassVar[tuple] = (
        *DummyCalculationInterface.configured_capabilities,
        InterfaceCapabilityConfig(CustomQueryCapability, {"label": "custom"}),
    )


CustomCalculationInterface._parent_class = DummyGeneralManager


class TestCalculationInterface(TestCase):
    def setUp(self):
        """
        Initializes a DummyCalculationInterface instance for use in test methods.
        """
        self.interface = DummyCalculationInterface("test", 1)

    def test_get_data(self):
        """
        Tests that get_data() raises a NotImplementedError when called on the interface instance.
        """
        with self.assertRaises(NotImplementedError):
            self.interface.get_data()

    def test_get_attribute_types(self):
        """
        Tests that get_attribute_types() returns a dictionary with expected attribute metadata keys.
        """
        attribute_types = DummyCalculationInterface.get_attribute_types()
        self.assertIsInstance(attribute_types, dict)
        for _name, attr in attribute_types.items():
            self.assertIn("type", attr)
            self.assertIn("default", attr)
            self.assertIn("is_editable", attr)
            self.assertIn("is_required", attr)
        self.assertTrue(attribute_types["field1"]["is_required"])
        self.assertFalse(attribute_types["field2"]["is_required"])

    def test_get_attributes(self):
        """
        Tests that get_attributes() returns a dictionary mapping attribute names to callables that produce the correct values for the interface instance.
        """
        attributes = DummyCalculationInterface.get_attributes()
        self.assertIsInstance(attributes, dict)
        for _name, attr in attributes.items():
            self.assertTrue(callable(attr))
            self.assertIn(attr(self.interface), ("test", 1))

    def test_get_attributes_passes_identification_to_dependent_inputs(self):
        class DependentCalculationInterface(CalculationInterface):
            input_fields: ClassVar[dict[str, Input]] = {
                "field1": Input(type=str),
                "field2": Input(
                    type=str,
                    possible_values=lambda field1: [field1.upper()],
                    depends_on=["field1"],
                    normalizer=lambda value: value.upper(),
                ),
            }

        class DependentManager:
            Interface = DependentCalculationInterface

        DependentCalculationInterface._parent_class = DependentManager
        interface = DependentCalculationInterface("alpha", "alpha")

        attributes = DependentCalculationInterface.get_attributes()

        self.assertEqual(attributes["field1"](interface), "alpha")
        self.assertEqual(attributes["field2"](interface), "ALPHA")

    def test_filter(self):
        """
        Tests that the filter method returns a CalculationBucket linked to DummyGeneralManager.
        """
        bucket = DummyCalculationInterface.filter(field1="test")
        self.assertIsInstance(bucket, CalculationBucket)
        self.assertEqual(bucket._manager_class, DummyGeneralManager)

    def test_exclude(self):
        """
        Tests that the exclude method returns a CalculationBucket linked to DummyGeneralManager.
        """
        bucket = DummyCalculationInterface.exclude(field1="test")
        self.assertIsInstance(bucket, CalculationBucket)
        self.assertEqual(bucket._manager_class, DummyGeneralManager)

    def test_all(self):
        """
        Tests that the all() method returns a CalculationBucket linked to DummyGeneralManager.
        """
        bucket = DummyCalculationInterface.all()
        self.assertIsInstance(bucket, CalculationBucket)
        self.assertEqual(bucket._manager_class, DummyGeneralManager)

    def test_pre_create(self):
        """
        Tests that the _pre_create class method initializes attributes and interface metadata correctly.

        Verifies that the returned attributes dictionary contains the provided field values, the correct interface type, and a reference to the interface class. Also checks that the initialized interface is a subclass of DummyCalculationInterface.
        """
        pre, _post = DummyCalculationInterface.handle_interface()
        attr, initialized_interface, _ = pre(
            "test",
            {"field1": "value1", "field2": 42},
            DummyCalculationInterface,
        )
        self.assertTrue(issubclass(initialized_interface, DummyCalculationInterface))
        self.assertEqual(attr.get("field1"), "value1")
        self.assertEqual(attr.get("field2"), 42)
        self.assertEqual(attr["_interface_type"], "calculation")
        self.assertIsNotNone(attr.get("Interface"))
        self.assertTrue(issubclass(attr["Interface"], DummyCalculationInterface))

    def test_interface_type(self):
        """
        Tests that the `_interface_type` attribute is set to "calculation" on both the class and instance.
        """
        self.assertEqual(DummyCalculationInterface._interface_type, "calculation")
        self.assertEqual(self.interface._interface_type, "calculation")

    def test_parent_class(self):
        """
        Tests that the `_parent_class` attribute of `DummyCalculationInterface` and its instance is set to `DummyGeneralManager`.
        """
        self.assertEqual(DummyCalculationInterface._parent_class, DummyGeneralManager)
        self.assertEqual(self.interface._parent_class, DummyGeneralManager)

    def test_get_field_type(self):
        """
        Tests that get_field_type returns the correct type for defined fields and raises KeyError for unknown fields.
        """
        field_type = DummyCalculationInterface.get_field_type("field1")
        self.assertEqual(field_type, str)

        field_type = DummyCalculationInterface.get_field_type("field2")
        self.assertEqual(field_type, int)

        with self.assertRaises(KeyError):
            DummyCalculationInterface.get_field_type("non_existent_field")

    def test_configured_capability_override(self):
        """
        Tests that configured capabilities declared on the interface replace default handlers.
        """
        handler = CustomCalculationInterface.get_capability_handler("query")
        self.assertIsNotNone(handler)
        self.assertIsInstance(handler, CustomQueryCapability)
        self.assertEqual(handler.label, "custom")


class LifecycleInterface(CalculationInterface):
    foo = Input(type=str)
    bar = Input(type=int)


class TestCalculationLifecycleCapability(TestCase):
    def setUp(self):
        self.capability = CalculationLifecycleCapability()

    def test_pre_create_collects_input_fields(self):
        attrs = {"__module__": __name__}

        with patch(
            "general_manager.interface.capabilities.calculation.lifecycle.call_with_observability",
            side_effect=lambda *_args, **kwargs: kwargs["func"](),
        ):
            updated_attrs, interface_cls, _ = self.capability.pre_create(
                name="GeneratedManager",
                attrs=attrs,
                interface=LifecycleInterface,
            )

        self.assertIn("Interface", updated_attrs)
        self.assertEqual(updated_attrs["_interface_type"], "calculation")
        generated_interface = updated_attrs["Interface"]
        self.assertTrue(issubclass(generated_interface, LifecycleInterface))
        self.assertEqual(
            set(generated_interface.input_fields.keys()),
            {"foo", "bar"},
        )
        self.assertEqual(interface_cls, generated_interface)

    def test_post_create_sets_parent_class(self):
        temp_interface = type(
            "TempInterface",
            (LifecycleInterface,),
            {},
        )
        manager_cls = type("TempManager", (), {})

        with patch(
            "general_manager.interface.capabilities.calculation.lifecycle.call_with_observability",
            side_effect=lambda *_args, **kwargs: kwargs["func"](),
        ):
            self.capability.post_create(
                new_class=manager_cls,
                interface_class=temp_interface,
                model=None,
            )

        self.assertIs(temp_interface._parent_class, manager_cls)  # type: ignore[attr-defined]
