from django.test import SimpleTestCase
from general_manager.manager.meta import GeneralManagerMeta


class dummyInterface:
    @staticmethod
    def getAttributes():
        return {
            "test_int": 42,
            "test_field": "value",
            "dummy_manager1": DummyManager1,
            "dummy_manager2": DummyManager2,
        }


class DummyGeneralManager:
    _attributes: dict
    _interface = dummyInterface

    class Interface:
        @staticmethod
        def getFieldType(field_name: str) -> type:
            if field_name == "test_int":
                return int
            elif field_name == "dummy_manager2":
                return DummyManager2
            elif field_name == "dummy_manager1":
                return DummyManager1
            return str


class DummyManager1(DummyGeneralManager):
    pass


class DummyManager2(DummyGeneralManager):
    pass


class TestPropertyInitialization(SimpleTestCase):
    def setUp(self):
        self.dummy_manager1 = DummyManager1()
        self.dummy_manager2 = DummyManager2()

    def tearDown(self):
        del self.dummy_manager1
        del self.dummy_manager2

        for manager_cls in (DummyManager1, DummyManager2):
            for attr in set(vars(manager_cls).keys()):
                if not attr.startswith("_"):
                    delattr(manager_cls, attr)

    def test_properties_initialization(self):
        self.dummy_manager1._attributes = {
            "test_field": "value",
        }

        GeneralManagerMeta.createAtPropertiesForAttributes(
            ["test_field"], DummyManager1  # type: ignore
        )

        self.assertTrue(hasattr(DummyManager1, "test_field"))  # type: ignore
        self.assertEqual(DummyManager1.test_field, str)  # type: ignore

        self.assertTrue(hasattr(self.dummy_manager1, "test_field"))  # type: ignore
        self.assertEqual(self.dummy_manager1.test_field, "value")  # type: ignore
        self.assertIsInstance(self.dummy_manager1.test_field, str)  # type: ignore

    def test_nested_manager_property(self):
        self.dummy_manager1._attributes = {
            "dummy_manager2": self.dummy_manager2,
        }

        GeneralManagerMeta.createAtPropertiesForAttributes(
            ["dummy_manager2"], DummyManager1  # type: ignore
        )

        self.assertTrue(hasattr(DummyManager1, "dummy_manager2"))  # type: ignore
        self.assertEqual(DummyManager1.dummy_manager2, DummyManager2)  # type: ignore

        self.assertTrue(hasattr(self.dummy_manager1, "dummy_manager2"))  # type: ignore
        self.assertIsInstance(self.dummy_manager1.dummy_manager2, DummyManager2)  # type: ignore
        self.assertEqual(self.dummy_manager1.dummy_manager2, self.dummy_manager2)  # type: ignore

    def test_circular_nested_manager_property(self):
        self.dummy_manager1._attributes = {
            "dummy_manager2": self.dummy_manager2,
        }
        self.dummy_manager2._attributes = {
            "dummy_manager1": self.dummy_manager1,
        }

        GeneralManagerMeta.createAtPropertiesForAttributes(
            ["dummy_manager2"], DummyManager1  # type: ignore
        )

        GeneralManagerMeta.createAtPropertiesForAttributes(
            ["dummy_manager1"], DummyManager2  # type: ignore
        )

        self.assertTrue(hasattr(DummyManager1, "dummy_manager2"))
        self.assertEqual(DummyManager1.dummy_manager2, DummyManager2)  # type: ignore
        self.assertTrue(hasattr(DummyManager2, "dummy_manager1"))  # type: ignore
        self.assertEqual(DummyManager2.dummy_manager1, DummyManager1)  # type: ignore

        self.assertTrue(hasattr(self.dummy_manager1, "dummy_manager2"))  # type: ignore
        self.assertIsInstance(self.dummy_manager1.dummy_manager2, DummyManager2)  # type: ignore
        self.assertEqual(self.dummy_manager1.dummy_manager2, self.dummy_manager2)  # type: ignore

        self.assertTrue(hasattr(self.dummy_manager1.dummy_manager2, "dummy_manager1"))  # type: ignore
        self.assertIsInstance(self.dummy_manager1.dummy_manager2.dummy_manager1, DummyManager1)  # type: ignore
        self.assertEqual(self.dummy_manager1.dummy_manager2.dummy_manager1, self.dummy_manager1)  # type: ignore

    def test_multiple_properties_initialization(self):
        self.dummy_manager1._attributes = {
            "test_int": 42,
            "test_field": "value",
        }

        GeneralManagerMeta.createAtPropertiesForAttributes(
            ["test_int", "test_field"], DummyManager1  # type: ignore
        )

        self.assertTrue(hasattr(DummyManager1, "test_int"))
        self.assertEqual(DummyManager1.test_int, int)  # type: ignore
        self.assertTrue(hasattr(DummyManager1, "test_field"))  # type: ignore
        self.assertEqual(DummyManager1.test_field, str)  # type: ignore

        self.assertTrue(hasattr(self.dummy_manager1, "test_int"))
        self.assertEqual(self.dummy_manager1.test_int, 42)  # type: ignore
        self.assertIsInstance(self.dummy_manager1.test_int, int)  # type: ignore
        self.assertTrue(hasattr(self.dummy_manager1, "test_field"))  # type: ignore
        self.assertEqual(self.dummy_manager1.test_field, "value")  # type: ignore
        self.assertIsInstance(self.dummy_manager1.test_field, str)  # type: ignore

    def test_property_with_callable(self):
        def test_callable(interface):
            return "callable_value"

        self.dummy_manager1._attributes = {
            "test_field": test_callable,
        }

        GeneralManagerMeta.createAtPropertiesForAttributes(
            ["test_field"], DummyManager1  # type: ignore
        )

        self.assertTrue(hasattr(DummyManager1, "test_field"))
        self.assertEqual(DummyManager1.test_field, str)  # type: ignore
        self.assertTrue(hasattr(self.dummy_manager1, "test_field"))
        self.assertEqual(self.dummy_manager1.test_field, "callable_value")  # type: ignore
        self.assertIsInstance(self.dummy_manager1.test_field, str)  # type: ignore

    def test_property_with_complex_callable(self):
        def test_complex_callable1(interface):
            return interface.getAttributes().get("test_field")

        def test_complex_callable2(interface):
            return interface.getAttributes().get("test_int")

        self.dummy_manager1._attributes = {
            "test_field": test_complex_callable1,
            "test_int": test_complex_callable2,
        }

        GeneralManagerMeta.createAtPropertiesForAttributes(
            ["test_field", "test_int"], DummyManager1  # type: ignore
        )

        self.assertTrue(hasattr(DummyManager1, "test_field"))
        self.assertEqual(DummyManager1.test_field, str)  # type: ignore
        self.assertTrue(hasattr(DummyManager1, "test_int"))
        self.assertEqual(DummyManager1.test_int, int)  # type: ignore

        self.assertTrue(hasattr(self.dummy_manager1, "test_field"))
        self.assertEqual(self.dummy_manager1.test_field, "value")  # type: ignore
        self.assertIsInstance(self.dummy_manager1.test_field, str)  # type: ignore
        self.assertTrue(hasattr(self.dummy_manager1, "test_int"))
        self.assertEqual(self.dummy_manager1.test_int, 42)  # type: ignore
        self.assertIsInstance(self.dummy_manager1.test_int, int)  # type: ignore

    def test_property_with_non_existent_attribute(self):
        self.dummy_manager1._attributes = {}

        GeneralManagerMeta.createAtPropertiesForAttributes(
            ["non_existent_field"], DummyManager1  # type: ignore
        )

        with self.assertRaises(AttributeError):
            getattr(self.dummy_manager1, "non_existent_field")

    def test_property_with_callable_error(self):

        def test_callable_error(interface):
            raise ValueError("This is a test error")

        self.dummy_manager1._attributes = {
            "test_field": test_callable_error,
        }

        GeneralManagerMeta.createAtPropertiesForAttributes(
            ["test_field"], DummyManager1  # type: ignore
        )

        with self.assertRaises(AttributeError) as context:
            getattr(self.dummy_manager1, "test_field")
        self.assertIn("Error calling attribute test_field", str(context.exception))
