# type: ignore
from django.test import SimpleTestCase, override_settings
from general_manager.interface.baseInterface import InterfaceBase
from general_manager.manager.generalManager import GeneralManager
from typing import ClassVar


# Dummy InputField implementation for testing
class DummyInput:
    def __init__(self, type_, depends_on=None, possible_values=None):
        """
        Initializes a DummyInput instance with a type, dependencies, and possible values.

        Args:
            type_: The expected type for the input value.
            depends_on: Optional list of field names this input depends on.
            possible_values: Optional list or callable specifying allowed values for the input.
        """
        self.type = type_
        self.depends_on = depends_on or []
        self.possible_values = possible_values

    def cast(self, value):
        """
        Returns the input value unchanged.

        Args:
            value: The value to be returned.

        Returns:
            The same value that was provided as input.
        """
        return value


# Dummy GeneralManager subclass for testing formatIdentification
class DummyGM(GeneralManager):  # type: ignore[misc]
    def __init__(self, identification):
        """
        Initializes the DummyGM instance with the given identification value.
        """
        self._identification = identification

    @property
    def identification(self):
        """
        Returns the identification value associated with this instance.
        """
        return self._identification


# Concrete test implementation of InterfaceBase
test_input_fields = {
    "a": DummyInput(int),
    "b": DummyInput(str, depends_on=["a"]),
    "gm": DummyInput(DummyGM),
    "vals": DummyInput(int, possible_values=[1, 2, 3]),
    "c": DummyInput(int, depends_on=["a"], possible_values=lambda a: [a, a + 1]),
}


class DummyInterface(InterfaceBase):
    input_fields = test_input_fields

    def getData(self, _search_date=None):
        """
        Returns the identification value for this interface instance.

        Args:
            search_date: Optional parameter for compatibility; ignored.

        Returns:
            The identification value associated with the interface.
        """
        return self.identification

    @classmethod
    def getAttributeTypes(cls):
        """
        Returns an empty dictionary representing attribute types for the class.
        """
        return {}

    @classmethod
    def getAttributes(cls):
        """
        Returns an empty dictionary of attributes for the class.

        Intended as a stub for subclasses to override with actual attribute definitions.
        """
        return {}

    @classmethod
    def filter(cls, **_kwargs):
        """
        Filters items based on provided keyword arguments.

        Returns:
            None. This is a stub implementation for testing purposes.
        """
        return None

    @classmethod
    def exclude(cls, **_kwargs):
        """
        Stub method for excluding items based on provided criteria.

        Returns:
            None
        """
        return None

    @classmethod
    def handleInterface(cls):
        """
        Returns stub handler functions for interface processing.

        The first returned function accepts any arguments and returns a tuple of the arguments, an empty dictionary, and None. The second returned function accepts any arguments and returns None.
        """
        return (lambda *args: (args, {}, None), lambda *_,: None)

    @classmethod
    def getFieldType(cls, field_name):
        """
        Returns the expected type for a given input field name.

        Args:
            field_name: Name of the input field to look up.

        Returns:
            The type object associated with the specified input field.
        """
        return cls.input_fields[field_name].type


class InterfaceBaseTests(SimpleTestCase):
    def test_valid_input_kwargs(self):
        # Normal case: all inputs provided as kwargs
        """
        Tests that DummyInterface initializes correctly when all required inputs are provided as keyword arguments.

        Verifies that the identification attribute matches the expected values, including nested GeneralManager instances.
        """
        gm = DummyGM({"id": 1})
        inst = DummyInterface(a=1, b="foo", gm=gm, vals=2, c=1)
        self.assertEqual(
            inst.identification,
            {"a": 1, "b": "foo", "gm": {"id": 1}, "vals": 2, "c": 1},
        )

    def test_valid_input_args(self):
        # Positional args instead of kwargs
        """
        Tests that DummyInterface correctly assigns input fields when initialized with valid positional arguments.
        """
        gm = DummyGM({"id": 2})

        inst = DummyInterface(2, "bar", gm, 3, 2)
        self.assertEqual(inst.identification["a"], 2)

    def test_missing_required_input(self):
        # Missing 'a' should raise TypeError
        """
        Tests that omitting a required input field when initializing DummyInterface raises a TypeError.
        """
        with self.assertRaises(TypeError):
            DummyInterface(b="foo", gm=DummyGM({"id": 3}), vals=1, c=1)

    def test_extra_input(self):
        # Unexpected argument 'extra' raises TypeError
        """
        Tests that providing an unexpected input argument to DummyInterface raises a TypeError.
        """
        with self.assertRaises(TypeError):
            DummyInterface(a=1, b="foo", gm=DummyGM({"id": 4}), vals=1, c=1, extra=5)

    def test_extra_input_id_suffix(self):
        # Argument 'gm_id' is remapped to 'gm'
        """
        Tests that input arguments with an '_id' suffix override the corresponding input field, ensuring the remapped value is used in identification.
        """
        inst = DummyInterface(
            a=1, b="baz", gm=DummyGM({"id": 5}), vals=1, c=1, gm_id=DummyGM({"id": 6})
        )
        self.assertEqual(inst.identification["gm"], {"id": 6})

    def test_type_mismatch(self):
        # Passing wrong type for 'a' should raise TypeError
        """
        Tests that providing a value of the wrong type for an input field raises a TypeError.

        This test ensures that initializing DummyInterface with a non-integer value for the 'a' field results in a TypeError.
        """
        with self.assertRaises(TypeError):
            DummyInterface(a="not_int", b="foo", gm=DummyGM({"id": 7}), vals=1, c=1)

    @override_settings(DEBUG=True)
    def test_invalid_value_list(self):
        # 'vals' not in allowed [1,2,3] raises ValueError
        """
        Tests that providing a value for the 'vals' input field that is not in its allowed list raises a ValueError.
        """
        with self.assertRaises(ValueError):
            DummyInterface(a=1, b="foo", gm=DummyGM({"id": 8}), vals=99, c=1)

    @override_settings(DEBUG=True)
    def test_invalid_value_callable(self):
        # 'c' not in allowed from lambda [a, a+1] raises ValueError
        """
        Tests that providing an invalid value for input 'c', where allowed values are determined by a callable, raises a ValueError.
        """
        with self.assertRaises(ValueError):
            DummyInterface(a=5, b="foo", gm=DummyGM({"id": 9}), vals=1, c=3)

    @override_settings(DEBUG=True)
    def test_invalid_kwargs(self):
        """
        Tests that initializing DummyInterface with an invalid kwargs raises a TypeError.
        """
        with self.assertRaises(TypeError):
            DummyInterface(a=1, b="foo", gm=DummyGM({"id": 10}), vals=1, c=1, x=2)

    def test_circular_dependency(self):
        # Two inputs depending on each other -> ValueError
        """
        Verifies that initializing an interface with circular input field dependencies raises a ValueError.
        """

        class Circ(InterfaceBase):
            input_fields: ClassVar[dict] = {
                "a": DummyInput(int, depends_on=["b"]),
                "b": DummyInput(int, depends_on=["a"]),
            }

            def getData(self, _search_date=None):
                return {}

            @classmethod
            def getAttributeTypes(cls):
                return {}

            @classmethod
            def getAttributes(cls):
                return {}

            @classmethod
            def filter(cls, **_kwargs):
                return None

            @classmethod
            def exclude(cls, **_kwargs):
                return None

            @classmethod
            def handleInterface(cls):
                return (lambda *args: (args, {}, None), lambda *_,: None)

            @classmethod
            def getFieldType(cls, _field_name):
                return int

        with self.assertRaises(ValueError):
            Circ(a=1, b=2)

    def test_format_identification_list_and_gm(self):
        # formatIdentification converts nested GeneralManager and lists correctly
        """
        Tests that formatIdentification recursively converts GeneralManager instances and lists containing them into dictionaries within the identification attribute.
        """
        gm = DummyGM({"id": 11})

        inst = DummyInterface(a=1, b="foo", gm=gm, vals=2, c=1)
        # inject a mixed list

        inst.identification["mixed"] = [DummyGM({"id": 12}), 42]
        formatted = inst.formatIdentification()
        self.assertEqual(formatted["mixed"], [{"id": 12}, 42])


# ---------------------------------------------------------------------------
# Additional tests for InterfaceBase: focus on dependency handling, possible_values,
# ID-suffix remapping, and identification formatting.
# Test framework: Django's unittest-based SimpleTestCase (runs under pytest-django or Django's test runner).
# ---------------------------------------------------------------------------


class InterfaceBaseMoreTests(SimpleTestCase):
    def test_callable_allowed_lower_bound_value(self):
        """c is allowed to equal a (lower bound from lambda [a, a+1])."""
        gm = DummyGM({"id": 20})
        inst = DummyInterface(a=10, b="ok", gm=gm, vals=1, c=10)
        self.assertEqual(inst.identification["c"], 10)

    def test_callable_allowed_upper_bound_value(self):
        """c is allowed to equal a+1 (upper bound from lambda [a, a+1])."""
        gm = DummyGM({"id": 21})
        inst = DummyInterface(a=3, b="ok", gm=gm, vals=1, c=4)
        self.assertEqual(inst.identification["c"], 4)

    def test_vals_allowed_bounds(self):
        """Explicitly verify inclusive bounds for fixed possible_values [1,2,3]."""
        gm = DummyGM({"id": 15})
        inst1 = DummyInterface(a=1, b="x", gm=gm, vals=1, c=1)
        inst3 = DummyInterface(a=1, b="x", gm=gm, vals=3, c=1)
        self.assertEqual(inst1.identification["vals"], 1)
        self.assertEqual(inst3.identification["vals"], 3)

    def test_gm_id_only_remap_sets_gm(self):
        """Providing only gm_id should populate/override gm in identification."""
        inst = DummyInterface(a=1, b="baz", gm_id=DummyGM({"id": 42}), vals=1, c=1)
        self.assertEqual(inst.identification["gm"], {"id": 42})

    def test_b_without_a_raises_type_error(self):
        """Field 'b' depends on 'a'; supplying 'b' without 'a' should fail."""
        with self.assertRaises(TypeError):
            DummyInterface(b="only_b", gm=DummyGM({"id": 13}), vals=1, c=1)

    def test_invalid_type_for_b_raises_type_error(self):
        """Type enforcement: 'b' expects str; non-str should fail."""
        with self.assertRaises(TypeError):
            DummyInterface(a=1, b=123, gm=DummyGM({"id": 1}), vals=1, c=1)

    def test_invalid_type_for_vals_raises_type_error(self):
        """Type enforcement: 'vals' expects int; non-int should fail."""
        with self.assertRaises(TypeError):
            DummyInterface(a=1, b="foo", gm=DummyGM({"id": 1}), vals="1", c=1)

    def test_missing_gm_raises_type_error(self):
        """All required inputs must be provided; missing 'gm' should fail."""
        with self.assertRaises(TypeError):
            DummyInterface(a=1, b="foo", vals=1, c=1)

    def test_handleInterface_contract(self):
        """handleInterface should return a tuple of callables with the documented behavior."""
        handler, post = DummyInterface.handleInterface()
        args, ctx, err = handler(1, "x", True)
        self.assertEqual(args, (1, "x", True))
        self.assertIsInstance(ctx, dict)
        self.assertIsNone(err)
        self.assertIsNone(post("anything"))

    def test_getFieldType_returns_expected_type(self):
        """getFieldType should return the exact declared type for fields."""
        self.assertIs(DummyInterface.getFieldType("a"), int)
        self.assertIs(DummyInterface.getFieldType("gm"), DummyGM)

    def test_filter_and_exclude_return_none(self):
        """Stubbed classmethods should return None per contract."""
        self.assertIsNone(DummyInterface.filter(foo=1))
        self.assertIsNone(DummyInterface.exclude(bar=2))

    def test_formatIdentification_list_of_gms(self):
        """formatIdentification should convert lists of GeneralManager instances into list of dicts."""
        inst = DummyInterface(a=1, b="foo", gm=DummyGM({"id": 2}), vals=2, c=1)
        inst.identification["team"] = [DummyGM({"id": 7}), DummyGM({"id": 8})]
        formatted = inst.formatIdentification()
        self.assertEqual(formatted["team"], [{"id": 7}, {"id": 8}])