from django.test import TestCase
from general_manager.interface.calculationInterface import CalculationInterface
from general_manager.bucket.calculationBucket import CalculationBucket
from general_manager.manager.input import Input


class DummyCalculationInterface(CalculationInterface):
    input_fields = {
        "field1": Input(type=str),
        "field2": Input(type=int),
    }


class DummyGeneralManager:
    Interface = DummyCalculationInterface


DummyCalculationInterface._parent_class = DummyGeneralManager


class TestCalculationInterface(TestCase):
    def setUp(self):
        """
        Initializes a DummyCalculationInterface instance for use in test methods.
        """
        self.interface = DummyCalculationInterface("test", 1)

    def test_getData(self):
        """
        Tests that getData() raises a NotImplementedError when called on the interface instance.
        """
        with self.assertRaises(NotImplementedError):
            self.interface.getData()

    def test_getAttributeTypes(self):
        """
        Tests that getAttributeTypes() returns a dictionary with expected attribute metadata keys.
        """
        attribute_types = DummyCalculationInterface.getAttributeTypes()
        self.assertIsInstance(attribute_types, dict)
        for _name, attr in attribute_types.items():
            self.assertIn("type", attr)
            self.assertIn("default", attr)
            self.assertIn("is_editable", attr)
            self.assertIn("is_required", attr)

    def test_getAttributes(self):
        """
        Tests that getAttributes() returns a dictionary mapping attribute names to callables that produce the correct values for the interface instance.
        """
        attributes = DummyCalculationInterface.getAttributes()
        self.assertIsInstance(attributes, dict)
        for _name, attr in attributes.items():
            self.assertTrue(callable(attr))
            self.assertIn(attr(self.interface), ("test", 1))

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

    def test_preCreate(self):
        """
        Tests that the _preCreate class method initializes attributes and interface metadata correctly.
        
        Verifies that the returned attributes dictionary contains the provided field values, the correct interface type, and a reference to the interface class. Also checks that the initialized interface is a subclass of DummyCalculationInterface.
        """
        attr, initialized_interface, _ = DummyCalculationInterface._preCreate(
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

    def test_getFieldType(self):
        """
        Tests that getFieldType returns the correct type for defined fields and raises KeyError for unknown fields.
        """
        field_type = DummyCalculationInterface.getFieldType("field1")
        self.assertEqual(field_type, str)

        field_type = DummyCalculationInterface.getFieldType("field2")
        self.assertEqual(field_type, int)

        with self.assertRaises(KeyError):
            DummyCalculationInterface.getFieldType("non_existent_field")
