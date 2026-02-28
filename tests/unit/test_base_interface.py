# type: ignore
from django.test import SimpleTestCase, override_settings
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.capabilities.configuration import (
    InterfaceCapabilityConfig,
)
from general_manager.manager.general_manager import GeneralManager
from typing import Callable, ClassVar, Type


# Dummy InputField implementation for testing
class DummyInput:
    def __init__(
        self,
        type_,
        depends_on=None,
        possible_values=None,
        *,
        required=True,
        min_value=None,
        max_value=None,
        validator=None,
    ):
        """
        Create a DummyInput representing a single input field with type information, dependency relationships, allowed values, and validation constraints.
        
        Parameters:
            type_ (type): Expected Python type for the input value.
            depends_on (list[str] | None): Names of other input fields this field depends on; used when resolving callable `possible_values` or `validator`.
            possible_values (Iterable | callable | None): Explicit allowed values or a callable that returns allowed values based on dependency values and optional identification.
            required (bool): Whether a value is required (value must not be None) when True.
            min_value (numeric | None): Minimum allowed value (inclusive) for scalar inputs, if applicable.
            max_value (numeric | None): Maximum allowed value (inclusive) for scalar inputs, if applicable.
            validator (callable | None): Optional callable used to perform custom validation; receives the value and dependency context and should indicate validity (truthy result or `None` are treated as passing).
        """
        self.type = type_
        self.depends_on = depends_on or []
        self.possible_values = possible_values
        self.required = required
        self.min_value = min_value
        self.max_value = max_value
        self.validator = validator

    def cast(self, value, _identification=None):
        """
        Return the input value unchanged.
        
        Returns:
            The same value provided as input.
        """
        return value

    def resolve_possible_values(self, identification=None):
        """
        Return the resolved set of allowed values for this input, evaluating a callable `possible_values` with dependent input values when necessary.
        
        Parameters:
            identification (dict | None): Mapping of input names to their current values used to supply arguments to a callable `possible_values`. If a dependency name is absent in `identification`, `None` is passed for that parameter.
        
        Returns:
            The resolved possible values (the direct `possible_values` attribute when not callable, or the result of calling it with dependency values).
        """
        if callable(self.possible_values):
            identification = identification or {}
            dependency_values = {
                dependency_name: identification.get(dependency_name)
                for dependency_name in self.depends_on
            }
            return self.possible_values(**dependency_values)
        return self.possible_values

    def validate_bounds(self, value):
        """
        Check whether a value satisfies this input's required, minimum, and maximum constraints.
        
        Parameters:
            value: The value to validate; may be None.
        
        Returns:
            `True` if the value is permitted by the input's `required`, `min_value`, and `max_value` settings, `False` otherwise.
        """
        if value is None:
            return not self.required
        if self.min_value is not None and value < self.min_value:
            return False
        if self.max_value is not None and value > self.max_value:
            return False
        return True

    def validate_with_callable(self, value, identification=None):
        """
        Validate a value using the configured validator callable and dependency values.
        
        If no validator is configured or the provided value is None, the value is considered valid.
        When a validator is present, it is called as validator(value, **dependency_values), where
        dependency_values is built by extracting names in self.depends_on from the optional
        identification mapping. If the validator returns None that is treated as valid; otherwise
        the truthiness of the validator's return determines validity.
        
        Parameters:
            value: The value to validate.
            identification (dict, optional): Mapping of field names to values used to supply
                dependency arguments to the validator. Defaults to an empty mapping.
        
        Returns:
            bool: `True` if the value is valid (or no validator/value is None), `False` otherwise.
        """
        if self.validator is None or value is None:
            return True
        identification = identification or {}
        dependency_values = {
            dependency_name: identification.get(dependency_name)
            for dependency_name in self.depends_on
        }
        result = self.validator(value, **dependency_values)
        if result is None:
            return True
        return bool(result)


# Dummy GeneralManager subclass for testing format_identification
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

    def get_data(self, _search_date=None):
        """
        Returns the identification value for this interface instance.

        Args:
            search_date: Optional parameter for compatibility; ignored.

        Returns:
            The identification value associated with the interface.
        """
        return self.identification

    @classmethod
    def get_attribute_types(cls):
        """
        Returns an empty dictionary representing attribute types for the class.
        """
        return {}

    @classmethod
    def get_attributes(cls):
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
    def handle_interface(cls):
        """
        Returns stub handler functions for interface processing.

        The first returned function accepts any arguments and returns a tuple of the arguments, an empty dictionary, and None. The second returned function accepts any arguments and returns None.
        """
        return (lambda *args: (args, {}, None), lambda *_: None)

    @classmethod
    def get_field_type(cls, field_name):
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

            def get_data(self, _search_date=None):
                return {}

            @classmethod
            def get_attribute_types(cls):
                return {}

            @classmethod
            def get_attributes(cls):
                return {}

            @classmethod
            def filter(cls, **_kwargs):
                return None

            @classmethod
            def exclude(cls, **_kwargs):
                return None

            @classmethod
            def handle_interface(cls):
                return (lambda *args: (args, {}, None), lambda *_: None)

            @classmethod
            def get_field_type(cls, _field_name):
                return int

        with self.assertRaises(ValueError):
            Circ(a=1, b=2)

    def test_format_identification_list_and_gm(self):
        # format_identification converts nested GeneralManager and lists correctly
        """
        Tests that format_identification recursively converts GeneralManager instances and lists containing them into dictionaries within the identification attribute.
        """
        gm = DummyGM({"id": 11})
        inst = DummyInterface(a=1, b="foo", gm=gm, vals=2, c=1)
        # inject a mixed list
        inst.identification["mixed"] = [DummyGM({"id": 12}), 42]
        formatted = InterfaceBase.format_identification(inst.identification)
        self.assertEqual(formatted["mixed"], [{"id": 12}, 42])

    def test_get_field_type_returns_expected_types(self):
        self.assertIs(DummyInterface.get_field_type("a"), int)
        self.assertIs(DummyInterface.get_field_type("gm"), DummyGM)

    def test_handle_interface_returns_valid_callables(self):
        process, finalize = DummyInterface.handle_interface()
        args = ("alpha", 123, None)
        out_args, meta, err = process(*args)
        self.assertEqual(out_args, args)
        self.assertEqual(meta, {})
        self.assertIsNone(err)
        self.assertIsNone(finalize("beta"))

    def test_gm_id_only_is_accepted(self):
        inst = DummyInterface(a=1, b="x", vals=1, c=1, gm_id=DummyGM({"id": 200}))
        self.assertEqual(inst.identification["gm"], {"id": 200})

    def test_wrong_number_of_positional_args_raises(self):
        gm = DummyGM({"id": 1})
        # Missing last required positional ('c')
        with self.assertRaises(TypeError):
            DummyInterface(1, "foo", gm, 2)
        # Too many positional args
        with self.assertRaises(TypeError):
            DummyInterface(1, "foo", gm, 2, 1, "extra")

    def test_wrong_type_for_b_raises(self):
        with self.assertRaises(TypeError):
            DummyInterface(a=1, b=123, gm=DummyGM({"id": 1}), vals=1, c=1)

    def test_wrong_type_for_gm_raises(self):
        with self.assertRaises(TypeError):
            DummyInterface(a=1, b="ok", gm="not_gm", vals=1, c=1)

    def test_callable_possible_values_accepts_a_plus_one(self):
        gm = DummyGM({"id": 2})
        inst = DummyInterface(a=10, b="ok", gm=gm, vals=1, c=11)
        self.assertEqual(inst.identification["c"], 11)

    def test_missing_dependent_input_raises(self):
        # 'b' depends on 'a' and is required in this interface layout
        with self.assertRaises(TypeError):
            DummyInterface(a=1, gm=DummyGM({"id": 3}), vals=1, c=1)

    def test_optional_input_defaults_to_none(self):
        class OptionalInterface(DummyInterface):
            input_fields: ClassVar[dict] = {
                **test_input_fields,
                "maybe": DummyInput(int, required=False),
            }

        inst = OptionalInterface(a=1, b="foo", gm=DummyGM({"id": 4}), vals=1, c=1)
        self.assertIsNone(inst.identification["maybe"])

    def test_optional_input_accepts_explicit_none(self):
        class OptionalInterface(DummyInterface):
            input_fields: ClassVar[dict] = {
                **test_input_fields,
                "maybe": DummyInput(int, required=False),
            }

        inst = OptionalInterface(
            a=1,
            b="foo",
            gm=DummyGM({"id": 5}),
            vals=1,
            c=1,
            maybe=None,
        )
        self.assertIsNone(inst.identification["maybe"])

    def test_scalar_bounds_are_enforced(self):
        """
        Verifies that a numeric input with defined min_value and max_value rejects values outside the inclusive range.
        
        Creates an interface with a 'score' field bounded between 1 and 3 and asserts that constructing the interface with score=0 raises a ValueError.
        """
        class RangedInterface(DummyInterface):
            input_fields: ClassVar[dict] = {
                **test_input_fields,
                "score": DummyInput(int, min_value=1, max_value=3),
            }

        with self.assertRaises(ValueError):
            RangedInterface(
                a=1,
                b="foo",
                gm=DummyGM({"id": 6}),
                vals=1,
                c=1,
                score=0,
            )

    def test_validator_is_enforced(self):
        """
        Verify that a custom validator on an input field raises ValueError when the provided value fails validation.
        
        This test defines a ValidatedInterface with a `score` field whose validator requires an even integer, then constructs the interface with an odd `score` (3) and asserts that construction raises a `ValueError`.
        """
        class ValidatedInterface(DummyInterface):
            input_fields: ClassVar[dict] = {
                **test_input_fields,
                "score": DummyInput(int, validator=lambda value: value % 2 == 0),
            }

        with self.assertRaises(ValueError):
            ValidatedInterface(
                a=1,
                b="foo",
                gm=DummyGM({"id": 7}),
                vals=1,
                c=1,
                score=3,
            )

    @override_settings(DEBUG=False, GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_possible_values_enforced_outside_debug_when_enabled(self):
        with self.assertRaises(ValueError):
            DummyInterface(a=1, b="foo", gm=DummyGM({"id": 8}), vals=99, c=1)

    @override_settings(DEBUG=False, GENERAL_MANAGER={"VALIDATE_INPUT_VALUES": "yes"})
    def test_possible_values_toggle_accepts_truthy_string(self):
        with self.assertRaises(ValueError):
            DummyInterface(a=1, b="foo", gm=DummyGM({"id": 8}), vals=99, c=1)

    @override_settings(DEBUG=True, GENERAL_MANAGER={"VALIDATE_INPUT_VALUES": "off"})
    def test_possible_values_toggle_accepts_falsey_string(self):
        inst = DummyInterface(a=1, b="foo", gm=DummyGM({"id": 8}), vals=99, c=1)
        self.assertEqual(inst.identification["vals"], 99)

    def test_dummy_input_validator_none_return_is_allowed(self):
        input_field = DummyInput(int, validator=lambda _value: None)
        self.assertTrue(input_field.validate_with_callable(1))

    def test_format_identification_deep_nested_collections(self):
        gm13 = DummyGM({"id": 13})
        gm14 = DummyGM({"id": 14})
        inst = DummyInterface(a=1, b="foo", gm=DummyGM({"id": 11}), vals=2, c=1)
        # deep/nested structure containing GeneralManager instances
        inst.identification["deep"] = {"list": [gm13, {"inner": [gm14, 7]}]}
        formatted = InterfaceBase.format_identification(inst.identification)
        self.assertEqual(
            formatted["deep"],
            {"list": [{"id": 13}, {"inner": [{"id": 14}, 7]}]},
        )

    def test_filter_and_exclude_return_none(self):
        self.assertIsNone(DummyInterface.filter(name="x"))
        self.assertIsNone(DummyInterface.exclude(name="x"))

    def test_get_data_returns_identification(self):
        gm = DummyGM({"id": 9})
        inst = DummyInterface(a=1, b="foo", gm=gm, vals=2, c=1)
        self.assertEqual(inst.get_data(), inst.identification)

    def test_vals_allowed_lower_and_upper_bounds(self):
        # Lower bound
        inst1 = DummyInterface(a=1, b="v", gm=DummyGM({"id": 21}), vals=1, c=1)
        self.assertEqual(inst1.identification["vals"], 1)
        # Upper bound
        inst2 = DummyInterface(a=1, b="v", gm=DummyGM({"id": 22}), vals=3, c=1)
        self.assertEqual(inst2.identification["vals"], 3)


# ------------------------------------------------------------
# Tests for startup hook dependency resolver registration
# ------------------------------------------------------------
class StartupHookDependencyResolverTests(SimpleTestCase):
    """Tests for dependency resolver registration in InterfaceBase."""

    def setUp(self) -> None:
        """Clear startup hooks before each test."""
        from general_manager.interface.infrastructure.startup_hooks import (
            clear_startup_hooks,
        )

        clear_startup_hooks()

    def tearDown(self) -> None:
        """Clear startup hooks after each test."""
        from general_manager.interface.infrastructure.startup_hooks import (
            clear_startup_hooks,
        )

        clear_startup_hooks()

    def test_registers_dependency_resolver_from_get_method(self) -> None:
        """Verify dependency resolver from get_startup_hook_dependency_resolver is registered."""
        from general_manager.interface.infrastructure.startup_hooks import (
            registered_startup_hook_entries,
        )

        class TestCapability(BaseCapability):
            name: ClassVar[str] = "test_resolver"

            def get_startup_hooks(
                self, interface_cls: Type[InterfaceBase]
            ) -> tuple[Callable[[], None], ...]:
                return (lambda: None,)

            def get_startup_hook_dependency_resolver(
                self, interface_cls: Type[InterfaceBase]
            ) -> Callable[[Type[object]], set[Type[object]]]:
                def resolver(iface: Type[object]) -> set[Type[object]]:
                    return set()

                return resolver

        class TestInterface(InterfaceBase):
            _interface_type = "test"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[tuple[InterfaceCapabilityConfig, ...]] = (
                InterfaceCapabilityConfig(TestCapability),
            )

        TestInterface.get_capabilities()
        entries = registered_startup_hook_entries()

        self.assertIn(TestInterface, entries)
        self.assertEqual(len(entries[TestInterface]), 1)
        self.assertIsNotNone(entries[TestInterface][0].dependency_resolver)

    def test_registers_dependency_resolver_from_attribute(self) -> None:
        """Verify dependency resolver from attribute is registered."""
        from general_manager.interface.infrastructure.startup_hooks import (
            registered_startup_hook_entries,
        )

        def resolver_func(iface: object) -> set[object]:
            return set()

        class TestCapability(BaseCapability):
            name: ClassVar[str] = "test_attr_resolver"
            startup_hook_dependency_resolver = resolver_func

            def get_startup_hooks(
                self, interface_cls: Type[InterfaceBase]
            ) -> tuple[Callable[[], None], ...]:
                return (lambda: None,)

        class TestInterface(InterfaceBase):
            _interface_type = "test"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[tuple[InterfaceCapabilityConfig, ...]] = (
                InterfaceCapabilityConfig(TestCapability),
            )

        TestInterface.get_capabilities()
        entries = registered_startup_hook_entries()

        self.assertIn(TestInterface, entries)
        resolver = entries[TestInterface][0].dependency_resolver
        resolved_func = getattr(resolver, "__func__", resolver)
        self.assertIs(resolved_func, resolver_func)

    def test_registers_none_resolver_when_not_provided(self) -> None:
        """Verify None resolver when capability provides no resolver."""
        from general_manager.interface.infrastructure.startup_hooks import (
            registered_startup_hook_entries,
        )

        class TestCapability(BaseCapability):
            name: ClassVar[str] = "test_no_resolver"

            def get_startup_hooks(
                self, interface_cls: Type[InterfaceBase]
            ) -> tuple[Callable[[], None], ...]:
                return (lambda: None,)

        class TestInterface(InterfaceBase):
            _interface_type = "test"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[tuple[InterfaceCapabilityConfig, ...]] = (
                InterfaceCapabilityConfig(TestCapability),
            )

        TestInterface.get_capabilities()
        entries = registered_startup_hook_entries()

        self.assertIn(TestInterface, entries)
        self.assertIsNone(entries[TestInterface][0].dependency_resolver)
