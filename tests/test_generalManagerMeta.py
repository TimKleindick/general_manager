from django.test import SimpleTestCase, override_settings

from general_manager.manager.meta import GeneralManagerMeta
from general_manager.interface.baseInterface import InterfaceBase


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


# -----------------------------------------------------------------------------
# 1. Minimal stub classes for Input and Bucket (if referenced by InterfaceBase)
# -----------------------------------------------------------------------------
# These stubs ensure that InterfaceBase logic relying on Input and Bucket
# does not fail during tests.


class Input:
    def __init__(self, type_, depends_on=(), possible_values=None):
        self.type = type_
        self.depends_on = depends_on
        self.possible_values = possible_values


class Bucket(list):
    pass


# -----------------------------------------------------------------------------
# 2. DummyInterface: minimal implementation of InterfaceBase
# -----------------------------------------------------------------------------
class DummyInterface(InterfaceBase):
    """
    Minimal subclass of InterfaceBase that:
    - Defines input_fields as an empty dict so parseInputFieldsToIdentification won’t fail.
    - Stubs all abstract methods.
    - Implements handleInterface() to return custom pre/post creation hooks.
    """

    input_fields: dict[str, Input] = {}  # no required fields # type: ignore

    @classmethod
    def create(cls, *args, **kwargs):
        raise NotImplementedError

    def update(self, *args, **kwargs):
        raise NotImplementedError

    def deactivate(self, *args, **kwargs):
        raise NotImplementedError

    def getData(self, search_date=None):
        raise NotImplementedError

    @classmethod
    def getAttributeTypes(cls) -> dict[str, dict]:  # type: ignore
        return {}

    @classmethod
    def getAttributes(cls) -> dict[str, dict]:
        return {}

    @classmethod
    def filter(cls, **kwargs):  # type: ignore
        return Bucket()

    @classmethod
    def exclude(cls, **kwargs):  # type: ignore
        return Bucket()

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        return str

    @classmethod
    def handleInterface(cls):
        """
        Returns two functions:
        - preCreation: modifies attrs before the class is created (adds 'marker').
        - postCreation: sets a flag on the newly created class.
        """

        def preCreation(name, attrs, interface):
            attrs["marker"] = "initialized_by_dummy"
            return attrs, cls, None

        def postCreation(new_cls, interface_cls, model):
            new_cls.post_mark = True

        return preCreation, postCreation


# -----------------------------------------------------------------------------
# 3. Test cases for GeneralManagerMeta
# -----------------------------------------------------------------------------
class GeneralManagerMetaTests(SimpleTestCase):
    def setUp(self):
        # Reset the metaclass’s global lists before each test
        GeneralManagerMeta.all_classes.clear()
        GeneralManagerMeta.pending_graphql_interfaces.clear()
        GeneralManagerMeta.pending_attribute_initialization.clear()

    def test_register_with_valid_interface(self):
        """
        1. Define a class that sets Interface = DummyInterface.
        2. After definition, it should appear in all_classes and pending_attribute_initialization.
        3. preCreation hook adds 'marker'; postCreation hook sets 'post_mark'.
        """

        class MyManager(metaclass=GeneralManagerMeta):
            Interface = DummyInterface

        # a) MyManager should be in all_classes
        self.assertIn(
            MyManager,
            GeneralManagerMeta.all_classes,
            msg="MyManager should be registered in all_classes.",
        )

        # b) MyManager should be in pending_attribute_initialization
        self.assertIn(
            MyManager,
            GeneralManagerMeta.pending_attribute_initialization,
            msg="MyManager should be registered in pending_attribute_initialization.",
        )

        # c) preCreation hook added the 'marker' attribute
        self.assertTrue(
            hasattr(MyManager, "marker") and MyManager.marker == "initialized_by_dummy",  # type: ignore
            msg="MyManager.marker should be set by the DummyInterface preCreation hook.",
        )

        # d) postCreation hook set the 'post_mark' attribute to True
        self.assertTrue(
            hasattr(MyManager, "post_mark") and MyManager.post_mark is True,  # type: ignore
            msg="MyManager.post_mark should be True set by the DummyInterface postCreation hook.",
        )

    def test_invalid_interface_raises_type_error(self):
        """
        Verifies that defining a class with an Interface not subclassing InterfaceBase raises a TypeError.

        Asserts that the exception message indicates the requirement for InterfaceBase and that no classes are registered after the failure.
        """
        with self.assertRaises(TypeError) as cm:

            class BadManager(metaclass=GeneralManagerMeta):
                Interface = (
                    object  # not a valid subclass of InterfaceBase # type: ignore
                )

        self.assertIn(
            "object must be a subclass of InterfaceBase",
            str(cm.exception),
            msg="Exception message should indicate that InterfaceBase is required.",
        )

        # The lists should remain empty after the failure
        self.assertEqual(
            len(GeneralManagerMeta.all_classes),
            0,
            msg="all_classes should remain empty after the failed definition.",
        )
        self.assertEqual(
            len(GeneralManagerMeta.pending_attribute_initialization),
            0,
            msg="pending_attribute_initialization should remain empty.",
        )

    def test_plain_manager_without_interface_does_nothing(self):
        """
        A class without an Interface attribute:
        - should not be added to all_classes
        - should not be added to pending_attribute_initialization
        - (when AUTOCREATE_GRAPHQL=False) should not be added to pending_graphql_interfaces
        """
        before_all = list(GeneralManagerMeta.all_classes)
        before_pending_init = list(GeneralManagerMeta.pending_attribute_initialization)
        before_pending_graphql = list(GeneralManagerMeta.pending_graphql_interfaces)

        class PlainManager(metaclass=GeneralManagerMeta):
            pass

        # a) all_classes should remain unchanged
        self.assertEqual(
            GeneralManagerMeta.all_classes,
            before_all,
            msg="all_classes should remain unchanged.",
        )

        # b) pending_attribute_initialization should remain unchanged
        self.assertEqual(
            GeneralManagerMeta.pending_attribute_initialization,
            before_pending_init,
            msg="pending_attribute_initialization should remain unchanged.",
        )

        # c) pending_graphql_interfaces should remain unchanged (AUTOCREATE_GRAPHQL=False by default)
        self.assertEqual(
            GeneralManagerMeta.pending_graphql_interfaces,
            before_pending_graphql,
            msg="pending_graphql_interfaces should remain unchanged.",
        )

    @override_settings(AUTOCREATE_GRAPHQL=True)
    def test_autocreate_graphql_flag_adds_to_pending(self):
        """
        When AUTOCREATE_GRAPHQL=True, every created class
        (with or without Interface) should be added to pending_graphql_interfaces.
        """
        GeneralManagerMeta.pending_graphql_interfaces.clear()

        # 1) Class without Interface
        class GQLManagerPlain(metaclass=GeneralManagerMeta):
            pass

        self.assertIn(
            GQLManagerPlain,
            GeneralManagerMeta.pending_graphql_interfaces,
            msg="GQLManagerPlain should be in pending_graphql_interfaces when AUTOCREATE_GRAPHQL=True.",
        )

        # Clear the list again
        GeneralManagerMeta.pending_graphql_interfaces.clear()

        # 2) Class WITH Interface
        class GQLManagerWithInterface(metaclass=GeneralManagerMeta):
            Interface = DummyInterface

        self.assertIn(
            GQLManagerWithInterface,
            GeneralManagerMeta.pending_graphql_interfaces,
            msg="GQLManagerWithInterface should be in pending_graphql_interfaces when AUTOCREATE_GRAPHQL=True.",
        )

    def test_multiple_classes_register_in_order(self):
        """
        Define two different DummyInterface variants, each with its own hooks.
        Verify that all_classes and pending_attribute_initialization
        appear in the correct order: [ManagerA, ManagerB],
        and that each manager has the attributes set by its hooks.
        """

        # DummyInterfaceA: adds 'fromA' and 'a_post'
        class DummyInterfaceA(InterfaceBase):
            input_fields: dict[str, Input] = {}  # type: ignore

            @classmethod
            def create(cls, *args, **kwargs):
                raise NotImplementedError

            def update(self, *args, **kwargs):
                raise NotImplementedError

            def deactivate(self, *args, **kwargs):
                raise NotImplementedError

            def getData(self, search_date=None):
                raise NotImplementedError

            @classmethod
            def getAttributeTypes(cls) -> dict[str, dict]:  # type: ignore
                return {}

            @classmethod
            def getAttributes(cls) -> dict[str, dict]:
                return {}

            @classmethod
            def filter(cls, **kwargs):  # type: ignore
                return Bucket()

            @classmethod
            def exclude(cls, **kwargs):  # type: ignore
                return Bucket()

            @classmethod
            def getFieldType(cls, field_name: str) -> type:
                return str

            @classmethod
            def handleInterface(cls):
                def preCreation(name, attrs, interface):
                    attrs["fromA"] = True
                    return attrs, cls, None

                def postCreation(new_cls, interface_cls, model):
                    new_cls.a_post = True

                return preCreation, postCreation

        # DummyInterfaceB: adds 'fromB' and 'b_post'
        class DummyInterfaceB(InterfaceBase):
            input_fields: dict[str, Input] = {}  # type: ignore

            @classmethod
            def create(cls, *args, **kwargs):
                raise NotImplementedError

            def update(self, *args, **kwargs):
                raise NotImplementedError

            def deactivate(self, *args, **kwargs):
                raise NotImplementedError

            def getData(self, search_date=None):
                raise NotImplementedError

            @classmethod
            def getAttributeTypes(cls) -> dict[str, dict]:  # type: ignore
                return {}

            @classmethod
            def getAttributes(cls) -> dict[str, dict]:
                return {}

            @classmethod
            def filter(cls, **kwargs):  # type: ignore
                return Bucket()

            @classmethod
            def exclude(cls, **kwargs):  # type: ignore
                return Bucket()

            @classmethod
            def getFieldType(cls, field_name: str) -> type:
                return str

            @classmethod
            def handleInterface(cls):
                def preCreation(name, attrs, interface):
                    attrs["fromB"] = True
                    return attrs, cls, None

                def postCreation(new_cls, interface_cls, model):
                    new_cls.b_post = True

                return preCreation, postCreation

        # 1. Define ManagerA with DummyInterfaceA
        class ManagerA(metaclass=GeneralManagerMeta):
            Interface = DummyInterfaceA

        # 2. Define ManagerB with DummyInterfaceB
        class ManagerB(metaclass=GeneralManagerMeta):
            Interface = DummyInterfaceB

        # a) all_classes must be [ManagerA, ManagerB] in that order
        self.assertEqual(
            GeneralManagerMeta.all_classes,
            [ManagerA, ManagerB],
            msg="all_classes should be [ManagerA, ManagerB] in this exact order.",
        )

        # b) pending_attribute_initialization must be [ManagerA, ManagerB] as well
        self.assertEqual(
            GeneralManagerMeta.pending_attribute_initialization,
            [ManagerA, ManagerB],
            msg="pending_attribute_initialization should be [ManagerA, ManagerB].",
        )

        # c) ManagerA should have attributes 'fromA' and 'a_post'
        self.assertTrue(
            hasattr(ManagerA, "fromA") and ManagerA.fromA is True,  # type: ignore
            msg="ManagerA should have the attribute 'fromA'.",
        )
        self.assertTrue(
            hasattr(ManagerA, "a_post") and ManagerA.a_post is True,  # type: ignore
            msg="ManagerA should have the attribute 'a_post'.",
        )

        # d) ManagerB should have attributes 'fromB' and 'b_post'
        self.assertTrue(
            hasattr(ManagerB, "fromB") and ManagerB.fromB is True,  # type: ignore
            msg="ManagerB should have the attribute 'fromB'.",
        )
        self.assertTrue(
            hasattr(ManagerB, "b_post") and ManagerB.b_post is True,  # type: ignore
            msg="ManagerB should have the attribute 'b_post'.",
        )
