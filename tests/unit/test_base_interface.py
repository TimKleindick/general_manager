# type: ignore
from django.test import SimpleTestCase, override_settings
from general_manager.interface import base_interface as base_interface_module
from general_manager.interface.base_interface import (
    InterfaceBase,
    InvalidInputTypeError,
    MissingInputArgumentsError,
    UnexpectedInputArgumentsError,
)
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.capabilities.configuration import (
    InterfaceCapabilityConfig,
)
from general_manager.cache.run_context import CalculationRunContext
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from typing import Callable, ClassVar, Type
from unittest.mock import patch


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
        Initializes a DummyInput instance with a type, dependencies, and possible values.

        Args:
            type_: The expected type for the input value.
            depends_on: Optional list of field names this input depends on.
            possible_values: Optional list or callable specifying allowed values for the input.
        """
        self.type = type_
        self.depends_on = depends_on or []
        self.possible_values = possible_values
        self.required = required
        self.min_value = min_value
        self.max_value = max_value
        self.validator = validator

    def cast(self, value, _identification=None, *, cache_context=None):
        """
        Returns the input value unchanged.

        Args:
            value: The value to be returned.

        Returns:
            The same value that was provided as input.
        """
        del cache_context
        return value

    def resolve_possible_values(self, identification=None, *, cache_context=None):
        del cache_context
        if callable(self.possible_values):
            identification = identification or {}
            dependency_values = {
                dependency_name: identification.get(dependency_name)
                for dependency_name in self.depends_on
            }
            return self.possible_values(**dependency_values)
        return self.possible_values

    def validate_bounds(self, value):
        if value is None:
            return not self.required
        if self.min_value is not None and value < self.min_value:
            return False
        if self.max_value is not None and value > self.max_value:
            return False
        return True

    def validate_with_callable(self, value, identification=None):
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

    def test_possible_values_setting_helper_does_not_import_config_per_call(self):
        import builtins

        with patch("builtins.__import__", wraps=builtins.__import__) as importer:
            base_interface_module._should_validate_possible_values()
            base_interface_module._should_validate_possible_values()

        config_imports = [
            call
            for call in importer.call_args_list
            if call.args and call.args[0] == "general_manager.conf"
        ]
        self.assertEqual(config_imports, [])

    @override_settings(DEBUG=False, GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_possible_values_setting_helper_reuses_cached_setting_value(self):
        with patch.object(
            base_interface_module,
            "get_setting",
            wraps=base_interface_module.get_setting,
        ) as get_setting:
            self.assertTrue(base_interface_module._should_validate_possible_values())
            self.assertTrue(base_interface_module._should_validate_possible_values())

        get_setting.assert_called_once_with("VALIDATE_INPUT_VALUES")

    def test_possible_values_setting_helper_recomputes_after_settings_change(self):
        with override_settings(DEBUG=False, GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True):
            self.assertTrue(base_interface_module._should_validate_possible_values())

        with override_settings(
            DEBUG=True, GENERAL_MANAGER={"VALIDATE_INPUT_VALUES": "off"}
        ):
            self.assertFalse(base_interface_module._should_validate_possible_values())

    def test_input_without_possible_values_skips_validation_setting_lookup(self):
        class NoPossibleValuesInterface(InterfaceBase):
            input_fields: ClassVar = {"id": DummyInput(int, possible_values=None)}

        with patch(
            "general_manager.interface.base_interface._should_validate_possible_values",
            side_effect=AssertionError("setting lookup is unnecessary"),
        ):
            instance = NoPossibleValuesInterface(7)

        self.assertEqual(instance.identification, {"id": 7})

    def test_unconstrained_input_skips_bound_and_validator_helpers(self):
        class UnconstrainedInputInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {"id": Input(int)}

        input_field = UnconstrainedInputInterface.input_fields["id"]

        with (
            patch.object(
                input_field,
                "validate_bounds",
                side_effect=AssertionError("bounds helper should be skipped"),
            ),
            patch.object(
                input_field,
                "validate_with_callable",
                side_effect=AssertionError("validator helper should be skipped"),
            ),
        ):
            instance = UnconstrainedInputInterface(7)

        self.assertEqual(instance.identification, {"id": 7})

    def test_input_possible_values_cache_context_is_reused_during_cast_and_validation(
        self,
    ):
        calls = 0

        def possible_values():
            nonlocal calls
            calls += 1
            return ["ABC"]

        def normalize_code(value, domain):
            del domain
            return value.upper()

        class ParentManager:
            pass

        class CachedInputInterface(InterfaceBase):
            _parent_class = ParentManager
            input_fields: ClassVar[dict] = {
                "code": Input(
                    str,
                    possible_values=possible_values,
                    normalizer=normalize_code,
                )
            }

            def get_data(self, _search_date=None):
                return self.identification

        with override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True):
            with CalculationRunContext():
                interface = CachedInputInterface(code="abc")

        self.assertEqual(interface.identification, {"code": "ABC"})
        self.assertEqual(calls, 1)

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

    def test_execute_with_observability_resolves_success_hooks_once(self):
        class CountingObserver:
            before_lookups = 0
            after_lookups = 0

            @property
            def before_operation(self):
                self.before_lookups += 1
                return lambda **_kwargs: None

            @property
            def after_operation(self):
                self.after_lookups += 1
                return lambda **_kwargs: None

        observer = CountingObserver()

        result = InterfaceBase._execute_with_observability(
            target=DummyInterface,
            operation="read",
            payload={},
            func=lambda: "ok",
            observer=observer,
        )

        self.assertEqual(result, "ok")
        self.assertEqual(observer.before_lookups, 1)
        self.assertEqual(observer.after_lookups, 1)

    def test_execute_with_observability_none_hook_still_raises(self):
        class NoneObserver:
            before_operation = None

        with self.assertRaises(TypeError):
            InterfaceBase._execute_with_observability(
                target=DummyInterface,
                operation="read",
                payload={},
                func=lambda: "ok",
                observer=NoneObserver(),
            )

    def test_vals_allowed_lower_and_upper_bounds(self):
        # Lower bound
        inst1 = DummyInterface(a=1, b="v", gm=DummyGM({"id": 21}), vals=1, c=1)
        self.assertEqual(inst1.identification["vals"], 1)
        # Upper bound
        inst2 = DummyInterface(a=1, b="v", gm=DummyGM({"id": 22}), vals=3, c=1)
        self.assertEqual(inst2.identification["vals"], 3)

    def test_input_parsing_plan_reused_for_repeated_instances(self):
        class CountingFields(dict):
            keys_calls = 0
            items_calls = 0

            def keys(self):
                self.keys_calls += 1
                return super().keys()

            def items(self):
                self.items_calls += 1
                return super().items()

        fields = CountingFields(
            {
                "a": DummyInput(int),
                "b": DummyInput(str, depends_on=["a"]),
                "c": DummyInput(int, required=False),
            }
        )

        class PlannedInterface(InterfaceBase):
            input_fields = fields

        first = PlannedInterface(a=1, b="x")
        second = PlannedInterface(a=2, b="y")

        self.assertEqual(first.identification, {"a": 1, "b": "x", "c": None})
        self.assertEqual(second.identification, {"a": 2, "b": "y", "c": None})
        self.assertEqual(fields.keys_calls, 1)
        self.assertEqual(fields.items_calls, 1)

    def test_single_required_input_skips_generic_argument_mapping(self):
        class SingleInputInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {"id": DummyInput(int)}

        with patch(
            "general_manager.interface.base_interface.args_to_kwargs",
            side_effect=AssertionError("single input should use fast path"),
        ):
            instance = SingleInputInterface(7)

        self.assertEqual(instance.identification, {"id": 7})

    def test_single_required_input_reuses_loaded_input_field_for_validation(self):
        class CountingFields(dict):
            getitem_calls = 0

            def __getitem__(self, key):
                self.getitem_calls += 1
                return super().__getitem__(key)

        fields = CountingFields({"id": DummyInput(int)})

        class SingleInputInterface(InterfaceBase):
            input_fields = fields

        instance = SingleInputInterface(7)

        self.assertEqual(instance.identification, {"id": 7})
        self.assertEqual(fields.getitem_calls, 0)

    def test_input_parsing_uses_fresh_plan_fields_without_item_lookup(self):
        class UnexpectedItemLookup(AssertionError):
            pass

        class NoItemLookupFields(dict):
            def __getitem__(self, key):
                raise UnexpectedItemLookup

        fields = NoItemLookupFields(
            {
                "name": DummyInput(str),
                "count": DummyInput(int),
            }
        )

        class PlannedInterface(InterfaceBase):
            input_fields = fields

        instance = PlannedInterface(name="demo", count=3)

        self.assertEqual(instance.identification, {"name": "demo", "count": 3})

    def test_exact_keyword_inputs_without_dependencies_skip_generic_argument_mapping(
        self,
    ):
        class ExactKeywordInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {
                "manager": DummyInput(DummyGM),
                "name": DummyInput(str),
                "count": DummyInput(int),
            }

        manager = DummyGM({"id": 7})
        with patch(
            "general_manager.interface.base_interface.args_to_kwargs",
            side_effect=AssertionError("exact keyword input should use fast path"),
        ):
            instance = ExactKeywordInterface(
                manager=manager,
                name="demo",
                count=3,
            )

        self.assertEqual(
            instance.identification,
            {"manager": {"id": 7}, "name": "demo", "count": 3},
        )

    def test_keyword_inputs_with_omitted_optional_skip_generic_argument_mapping(
        self,
    ):
        class OptionalKeywordInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {
                "name": DummyInput(str),
                "search_date": DummyInput(object, required=False),
            }

        with patch(
            "general_manager.interface.base_interface.args_to_kwargs",
            side_effect=AssertionError("known keyword input should use fast path"),
        ):
            instance = OptionalKeywordInterface(name="demo")

        self.assertEqual(
            instance.identification,
            {"name": "demo", "search_date": None},
        )

    def test_keyword_dependency_inputs_fast_path_preserves_input_field_order(
        self,
    ):
        class DependentInput(DummyInput):
            def cast(self, value, identification=None, *, cache_context=None):
                del cache_context
                identification = identification or {}
                return f"{identification['parent']}:{value}"

        class DependencyInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {
                "child": DependentInput(str, depends_on=["parent"]),
                "parent": DummyInput(str),
                "search_date": DummyInput(object, required=False),
            }

        with patch(
            "general_manager.interface.base_interface.args_to_kwargs",
            side_effect=AssertionError("known dependency input should use fast path"),
        ):
            parsed = DependencyInterface(child="child", parent="parent")

        self.assertEqual(
            list(parsed.identification), ["child", "parent", "search_date"]
        )
        self.assertEqual(
            parsed.identification,
            {
                "child": "parent:child",
                "parent": "parent",
                "search_date": None,
            },
        )

    def test_single_required_input_reuses_pure_scalar_parse_inside_run_context(self):
        class SingleInputInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {"id": Input(int)}

        input_field = SingleInputInterface.input_fields["id"]

        with (
            CalculationRunContext(),
            patch.object(input_field, "cast", wraps=input_field.cast) as cast_input,
        ):
            first = SingleInputInterface(7)
            second = SingleInputInterface(7)

        self.assertEqual(first.identification, {"id": 7})
        self.assertEqual(second.identification, {"id": 7})
        self.assertIsNot(first.identification, second.identification)
        self.assertEqual(cast_input.call_count, 1)

    def test_single_required_keyword_input_reuses_pure_scalar_parse_inside_run_context(
        self,
    ):
        class SingleInputInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {"id": Input(int)}

        input_field = SingleInputInterface.input_fields["id"]

        with (
            CalculationRunContext(),
            patch.object(input_field, "cast", wraps=input_field.cast) as cast_input,
        ):
            first = SingleInputInterface(id=7)
            second = SingleInputInterface(id=7)

        self.assertEqual(first.identification, {"id": 7})
        self.assertEqual(second.identification, {"id": 7})
        self.assertIsNot(first.identification, second.identification)
        self.assertEqual(cast_input.call_count, 1)

    def test_single_scalar_input_skips_identification_formatting(self):
        class SingleInputInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {"id": Input(int)}

        with patch.object(
            SingleInputInterface,
            "format_identification",
            side_effect=AssertionError("scalar identification needs no formatting"),
        ):
            parsed = SingleInputInterface(7)

        self.assertEqual(parsed.identification, {"id": 7})

    def test_single_manager_input_still_formats_identification(self):
        class SingleManagerInputInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {"manager": Input(DummyGM)}

        manager = DummyGM({"id": 7})

        parsed = SingleManagerInputInterface(manager)

        self.assertEqual(parsed.identification, {"manager": {"id": 7}})

    def test_get_capability_handler_skips_initialization_when_already_initialized(
        self,
    ):
        handler = object()

        class InitializedInterface(InterfaceBase):
            pass

        InitializedInterface._capability_selection = object()  # type: ignore[assignment]
        InitializedInterface._configured_capabilities_applied = True
        InitializedInterface._capability_handlers = {"read": handler}

        with patch.object(
            InitializedInterface,
            "_ensure_capabilities_initialized",
            side_effect=AssertionError("initialized lookup should use fast path"),
        ):
            result = InitializedInterface.get_capability_handler("read")

        self.assertIs(result, handler)

    def test_input_parsing_plan_accepts_id_alias_and_preserves_overwrite_behavior(
        self,
    ):
        class AliasInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {
                "owner": DummyInput(DummyGM),
                "name": DummyInput(str),
            }

        owner = DummyGM({"id": 10})
        alias_owner = DummyGM({"id": 11})
        parsed = AliasInterface(owner_id=owner, name="demo")
        overwritten = AliasInterface(owner=owner, owner_id=alias_owner, name="demo")

        self.assertEqual(parsed.identification, {"owner": {"id": 10}, "name": "demo"})
        self.assertEqual(
            overwritten.identification,
            {"owner": {"id": 11}, "name": "demo"},
        )

    def test_input_parsing_plan_detects_circular_dependencies(self):
        class CircularInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {
                "a": DummyInput(int, depends_on=["b"]),
                "b": DummyInput(int, depends_on=["a"]),
            }

        with self.assertRaisesRegex(
            ValueError,
            "Circular dependency detected among inputs",
        ):
            CircularInterface(a=1, b=2)

    def test_input_validation_takes_precedence_over_separate_circular_dependencies(
        self,
    ):
        class MixedCircularInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {
                "independent": DummyInput(int),
                "a": DummyInput(int, depends_on=["b"]),
                "b": DummyInput(int, depends_on=["a"]),
            }

        with self.assertRaises(InvalidInputTypeError):
            MixedCircularInterface(independent="bad", a=1, b=2)

    def test_input_parsing_plan_missing_args_take_precedence_over_circular_dependencies(
        self,
    ):
        class CircularInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {
                "a": DummyInput(int, depends_on=["b"]),
                "b": DummyInput(int, depends_on=["a"]),
            }

        with self.assertRaises(MissingInputArgumentsError):
            CircularInterface(a=1)

    def test_input_parsing_plan_extra_args_take_precedence_over_circular_dependencies(
        self,
    ):
        class CircularInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {
                "a": DummyInput(int, depends_on=["b"]),
                "b": DummyInput(int, depends_on=["a"]),
            }

        with self.assertRaises(UnexpectedInputArgumentsError):
            CircularInterface(a=1, b=2, extra=3)

    def test_input_parsing_plan_processes_dependencies_before_dependents(self):
        events: list[tuple[str, dict[str, object]]] = []

        class ObservedInput(DummyInput):
            def __init__(self, name, dependency=None):
                super().__init__(str, depends_on=[dependency] if dependency else None)
                self.name = name
                self.dependency = dependency

            def cast(self, value, identification=None, *, cache_context=None):
                del cache_context
                identification = identification or {}
                events.append((self.name, dict(identification)))
                if self.dependency is None:
                    return value
                dependency_value = identification[self.dependency]
                return f"{dependency_value}:{value}"

        class DependencyInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {
                "child": ObservedInput("child", dependency="parent"),
                "parent": ObservedInput("parent"),
            }

        parsed = DependencyInterface(child="child", parent="parent")

        self.assertEqual(
            events,
            [
                ("parent", {}),
                ("child", {"parent": "parent"}),
            ],
        )
        self.assertEqual(
            parsed.identification,
            {"child": "parent:child", "parent": "parent"},
        )

    def test_input_parsing_plan_preserves_input_field_order_after_dependencies(
        self,
    ):
        class DependentInput(DummyInput):
            def cast(self, value, identification=None, *, cache_context=None):
                del cache_context
                identification = identification or {}
                return f"{identification['parent']}:{value}"

        class DependencyInterface(InterfaceBase):
            input_fields: ClassVar[dict] = {
                "child": DependentInput(str, depends_on=["parent"]),
                "parent": DummyInput(str),
            }

        parsed = DependencyInterface(child="child", parent="parent")

        self.assertEqual(list(parsed.identification), ["child", "parent"])
        self.assertEqual(
            parsed.identification,
            {"child": "parent:child", "parent": "parent"},
        )

    def test_input_parsing_plan_reflects_added_required_field_after_first_parse(self):
        fields = {
            "a": DummyInput(int),
        }

        class MutableInterface(InterfaceBase):
            input_fields = fields

        first = MutableInterface(a=1)
        fields["b"] = DummyInput(str)
        second = MutableInterface(a=2, b="x")

        self.assertEqual(first.identification, {"a": 1})
        self.assertEqual(second.identification, {"a": 2, "b": "x"})

    def test_input_parsing_plan_reflects_field_order_mutation_after_first_parse(self):
        a_input = DummyInput(int)
        b_input = DummyInput(str)
        fields = {
            "a": a_input,
            "b": b_input,
        }

        class MutableInterface(InterfaceBase):
            input_fields = fields

        first = MutableInterface(1, "x")
        MutableInterface.input_fields = {
            "b": b_input,
            "a": a_input,
        }
        second = MutableInterface("y", 2)

        self.assertEqual(first.identification, {"a": 1, "b": "x"})
        self.assertEqual(second.identification, {"b": "y", "a": 2})

    def test_input_parsing_plan_reflects_required_mutation_after_first_parse(self):
        fields = {
            "a": DummyInput(int),
            "maybe": DummyInput(str, required=False),
        }

        class MutableInterface(InterfaceBase):
            input_fields = fields

        first = MutableInterface(a=1)
        fields["maybe"].required = True

        self.assertEqual(first.identification, {"a": 1, "maybe": None})
        with self.assertRaises(MissingInputArgumentsError):
            MutableInterface(a=2)
        parsed = MutableInterface(a=2, maybe="x")
        self.assertEqual(parsed.identification, {"a": 2, "maybe": "x"})

    def test_input_parsing_plan_reflects_single_field_required_mutation(self):
        fields = {
            "a": DummyInput(int, required=False),
        }

        class MutableInterface(InterfaceBase):
            input_fields = fields

        first = MutableInterface()
        fields["a"].required = True

        self.assertEqual(first.identification, {"a": None})
        with self.assertRaises(MissingInputArgumentsError):
            MutableInterface()
        parsed = MutableInterface(a=2)
        self.assertEqual(parsed.identification, {"a": 2})

    def test_input_parsing_plan_reflects_dependency_mutation_after_first_parse(self):
        events: list[tuple[str, dict[str, object]]] = []

        class ObservedInput(DummyInput):
            def __init__(self, name):
                super().__init__(str)
                self.name = name

            def cast(self, value, identification=None, *, cache_context=None):
                del cache_context
                identification = identification or {}
                events.append((self.name, dict(identification)))
                if self.depends_on:
                    dependency_name = self.depends_on[0]
                    return f"{identification[dependency_name]}:{value}"
                return value

        fields = {
            "child": ObservedInput("child"),
            "parent": ObservedInput("parent"),
        }

        class MutableInterface(InterfaceBase):
            input_fields = fields

        first = MutableInterface(child="child", parent="parent")
        fields["child"].depends_on = ["parent"]
        events.clear()
        second = MutableInterface(child="child", parent="parent")

        self.assertEqual(first.identification, {"child": "child", "parent": "parent"})
        self.assertEqual(
            events,
            [
                ("parent", {}),
                ("child", {"parent": "parent"}),
            ],
        )
        self.assertEqual(
            second.identification,
            {"child": "parent:child", "parent": "parent"},
        )


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
