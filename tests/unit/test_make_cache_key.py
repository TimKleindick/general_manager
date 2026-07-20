from datetime import datetime
import builtins
import hashlib
import inspect
import json
from unittest.mock import patch

from django.test import SimpleTestCase

from general_manager.api import as_of, current_as_of_date
from general_manager.as_of import normalize_search_date
from general_manager.utils.json_encoder import CustomJSONEncoder
from general_manager.utils import make_cache_key as make_cache_key_module
from general_manager.utils.make_cache_key import make_cache_key
from general_manager.manager.meta import GeneralManagerMeta


class TestMakeCacheKey(SimpleTestCase):
    @staticmethod
    def _historical_manager_class():
        from general_manager.manager.general_manager import GeneralManager

        class CacheKeyInterface:
            _as_of_behavior = "historical"

            def __init__(self, manager_id, search_date=None):
                self.identification = {"id": manager_id}
                self._search_date = (
                    None if search_date is None else normalize_search_date(search_date)
                )

        class CacheKeyManager(GeneralManager):
            pass

        CacheKeyManager.Interface = CacheKeyInterface
        return CacheKeyManager

    def test_make_cache_key_namespaces_ambient_historical_snapshots(self):
        def sample_function(value):
            return value

        current_key = make_cache_key(sample_function, (1,), {})
        with as_of("2022-01-01"):
            date_a_key = make_cache_key(sample_function, (1,), {})
        with as_of(datetime(2022, 1, 1)):
            equivalent_date_a_key = make_cache_key(sample_function, (1,), {})
        with as_of("2022-01-02"):
            date_b_key = make_cache_key(sample_function, (1,), {})

        self.assertNotEqual(current_key, date_a_key)
        self.assertEqual(date_a_key, equivalent_date_a_key)
        self.assertNotEqual(date_a_key, date_b_key)

    def test_manager_fast_path_namespaces_ambient_historical_snapshots(self):
        CacheKeyManager = self._historical_manager_class()

        def sample_function(manager):
            return manager

        current_key = make_cache_key(sample_function, (CacheKeyManager(1),), {})
        with as_of("2022-01-01"):
            date_a_manager = CacheKeyManager(1)
            date_a_key = make_cache_key(sample_function, (date_a_manager,), {})
            date_a_payload = {
                "module": sample_function.__module__,
                "qualname": sample_function.__qualname__,
                "args": {"manager": date_a_manager},
                "as_of": current_as_of_date().isoformat(),
            }
            date_a_generic_key = hashlib.sha256(
                json.dumps(
                    date_a_payload,
                    sort_keys=True,
                    cls=CustomJSONEncoder,
                ).encode(),
                usedforsecurity=False,
            ).hexdigest()
        with as_of(datetime(2022, 1, 1)):
            equivalent_date_a_key = make_cache_key(
                sample_function,
                (CacheKeyManager(1),),
                {},
            )
        with as_of("2022-01-02"):
            date_b_key = make_cache_key(
                sample_function,
                (CacheKeyManager(1),),
                {},
            )

        self.assertNotEqual(current_key, date_a_key)
        self.assertEqual(date_a_key, date_a_generic_key)
        self.assertEqual(date_a_key, equivalent_date_a_key)
        self.assertNotEqual(date_a_key, date_b_key)

    def test_explicit_historical_manager_outside_context_changes_cache_identity(self):
        CacheKeyManager = self._historical_manager_class()

        def sample_function(manager):
            return manager

        current_manager = CacheKeyManager(1)
        historical_manager = CacheKeyManager(1, search_date="2022-01-01")

        current_key = make_cache_key(sample_function, (current_manager,), {})
        historical_key = make_cache_key(
            sample_function,
            (historical_manager,),
            {},
        )
        payload = {
            "module": sample_function.__module__,
            "qualname": sample_function.__qualname__,
            "args": {"manager": historical_manager},
        }
        generic_raw = json.dumps(
            payload,
            sort_keys=True,
            cls=CustomJSONEncoder,
        ).encode()

        self.assertNotEqual(current_key, historical_key)
        self.assertEqual(
            historical_key,
            hashlib.sha256(generic_raw, usedforsecurity=False).hexdigest(),
        )

    def test_make_cache_key(self):
        def sample_function(x, y):
            """
            Compute the sum of two values.

            Both operands must support the `+` operator; the result is the value produced by adding `x` and `y`.

            Parameters:
                x: The first addend.
                y: The second addend.

            Returns:
                The result of `x + y`.
            """
            return x + y

        args = (1,)
        kwargs = {"y": 3}

        result = make_cache_key(sample_function, args, kwargs)
        self.assertIsNotNone(result)

        result2 = make_cache_key(sample_function, args, kwargs)
        self.assertEqual(result, result2)

    def test_make_cache_key_reuses_signature_for_same_function(self):
        def sample_function(x, y=1):
            return x + y

        with patch(
            "general_manager.utils.make_cache_key.inspect.signature",
            wraps=inspect.signature,
        ) as signature:
            make_cache_key(sample_function, (1,), None)
            make_cache_key(sample_function, (2,), None)

        signature.assert_called_once_with(sample_function)

    def test_make_cache_key_skips_binding_for_simple_positional_call(self):
        def sample_function(manager):
            return manager

        args = ("manager-id",)
        payload = {
            "module": sample_function.__module__,
            "qualname": sample_function.__qualname__,
            "args": {"manager": "manager-id"},
        }
        raw = json.dumps(payload, sort_keys=True, cls=CustomJSONEncoder).encode()
        expected = hashlib.sha256(raw, usedforsecurity=False).hexdigest()

        with patch.object(
            inspect.Signature,
            "bind_partial",
            side_effect=AssertionError("simple call should not bind"),
        ):
            result = make_cache_key(sample_function, args, {})

        self.assertEqual(result, expected)

    def test_make_cache_key_supports_unhashable_callable_instances(self):
        class CallableWithoutHash:
            __hash__ = None  # type: ignore[assignment]

            def __init__(self) -> None:
                self.__module__ = __name__
                self.__qualname__ = "CallableWithoutHash"

            def __eq__(self, other):
                return isinstance(other, CallableWithoutHash)

            def __call__(self, value):
                return value

        callable_instance = CallableWithoutHash()
        payload = {
            "module": callable_instance.__module__,
            "qualname": callable_instance.__qualname__,
            "args": {"value": 7},
        }
        raw = json.dumps(payload, sort_keys=True, cls=CustomJSONEncoder).encode()
        expected = hashlib.sha256(raw, usedforsecurity=False).hexdigest()

        result = make_cache_key(callable_instance, (7,), {})

        self.assertEqual(result, expected)

    def test_make_cache_key_fast_path_for_single_manager_arg_matches_generic_key(self):
        from general_manager.manager.general_manager import GeneralManager

        class CacheKeyInterface:
            def __init__(self, manager_id):
                self.identification = {"id": manager_id}

        class CacheKeyManager(GeneralManager):
            pass

        CacheKeyManager.Interface = CacheKeyInterface

        def sample_function(manager):
            return manager

        manager = CacheKeyManager(7)
        payload = {
            "module": sample_function.__module__,
            "qualname": sample_function.__qualname__,
            "args": {"manager": manager},
        }
        raw = json.dumps(payload, sort_keys=True, cls=CustomJSONEncoder).encode()
        expected = hashlib.sha256(raw, usedforsecurity=False).hexdigest()

        with patch(
            "general_manager.utils.make_cache_key.json.dumps",
            side_effect=AssertionError("single manager fast path should not dump"),
        ):
            result = make_cache_key(sample_function, (manager,), {})

        self.assertEqual(result, expected)

    def test_single_manager_fast_path_supports_unhashable_callable_instances(self):
        from general_manager.manager.general_manager import GeneralManager

        class CacheKeyInterface:
            def __init__(self, manager_id):
                self.identification = {"id": manager_id}

        class CacheKeyManager(GeneralManager):
            pass

        CacheKeyManager.Interface = CacheKeyInterface

        class CallableWithoutHash:
            __hash__ = None  # type: ignore[assignment]

            def __init__(self) -> None:
                self.__module__ = __name__
                self.__qualname__ = "CallableWithoutHash"

            def __eq__(self, other):
                return isinstance(other, CallableWithoutHash)

            def __call__(self, manager):
                return manager

        callable_instance = CallableWithoutHash()
        manager = CacheKeyManager(7)
        payload = {
            "module": callable_instance.__module__,
            "qualname": callable_instance.__qualname__,
            "args": {"manager": manager},
        }
        raw = json.dumps(payload, sort_keys=True, cls=CustomJSONEncoder).encode()
        expected = hashlib.sha256(raw, usedforsecurity=False).hexdigest()

        result = make_cache_key(callable_instance, (manager,), {})

        self.assertEqual(result, expected)

    def test_single_manager_fast_path_reuses_static_json_fragments(self):
        from general_manager.manager.general_manager import GeneralManager

        class CacheKeyInterface:
            def __init__(self, manager_id):
                self.identification = {"id": manager_id}

        class CacheKeyManager(GeneralManager):
            pass

        CacheKeyManager.Interface = CacheKeyInterface

        def sample_function(manager):
            return manager

        first_manager = CacheKeyManager(7)
        second_manager = CacheKeyManager(8)

        with patch(
            "general_manager.utils.make_cache_key.encode_basestring_ascii",
            wraps=make_cache_key_module.encode_basestring_ascii,
        ) as encode_string:
            make_cache_key(sample_function, (first_manager,), {})
            first_call_count = encode_string.call_count
            make_cache_key(sample_function, (second_manager,), {})

        self.assertEqual(encode_string.call_count - first_call_count, 1)

    def test_single_manager_fast_path_reuses_hashed_key_for_equivalent_manager(self):
        from general_manager.manager.general_manager import GeneralManager

        class CacheKeyInterface:
            def __init__(self, manager_id):
                self.identification = {"id": manager_id}

        class CacheKeyManager(GeneralManager):
            pass

        CacheKeyManager.Interface = CacheKeyInterface

        def sample_function(manager):
            return manager

        with patch(
            "general_manager.utils.make_cache_key.sha256",
            wraps=hashlib.sha256,
        ) as hash_factory:
            first_result = make_cache_key(sample_function, (CacheKeyManager(7),), {})
            second_result = make_cache_key(
                sample_function,
                (CacheKeyManager(7),),
                {},
            )

        self.assertEqual(first_result, second_result)
        self.assertEqual(hash_factory.call_count, 1)

    def test_single_manager_fast_path_caches_general_manager_class_lookup(self):
        from general_manager.manager.general_manager import GeneralManager

        class CacheKeyInterface:
            def __init__(self, manager_id):
                self.identification = {"id": manager_id}

        class CacheKeyManager(GeneralManager):
            pass

        CacheKeyManager.Interface = CacheKeyInterface

        def sample_function(manager):
            return manager

        manager = CacheKeyManager(7)
        imports = 0
        real_import = builtins.__import__

        def counting_import(name, *args, **kwargs):
            nonlocal imports
            if name == "general_manager.manager.general_manager":
                imports += 1
            return real_import(name, *args, **kwargs)

        make_cache_key_module._general_manager_class.cache_clear()
        try:
            with patch("builtins.__import__", side_effect=counting_import):
                make_cache_key(sample_function, (manager,), {})
                make_cache_key(sample_function, (manager,), {})
        finally:
            make_cache_key_module._general_manager_class.cache_clear()

        self.assertEqual(imports, 1)

    def test_make_cache_key_fast_path_for_non_ascii_manager_arg_matches_generic_key(
        self,
    ):
        from general_manager.manager.general_manager import GeneralManager

        class CacheKeyInterface:
            def __init__(self, manager_id):
                self.identification = {"id": manager_id}

        class CacheKeyManager(GeneralManager):
            pass

        CacheKeyManager.Interface = CacheKeyInterface

        def sample_function(manager):
            return manager

        manager = CacheKeyManager("M\u00fcller")
        payload = {
            "module": sample_function.__module__,
            "qualname": sample_function.__qualname__,
            "args": {"manager": manager},
        }
        raw = json.dumps(payload, sort_keys=True, cls=CustomJSONEncoder).encode()
        expected = hashlib.sha256(raw, usedforsecurity=False).hexdigest()

        self.assertEqual(make_cache_key(sample_function, (manager,), {}), expected)

    def test_make_cache_key_single_manager_arg_respects_changed_function_module(self):
        from general_manager.manager.general_manager import GeneralManager

        class CacheKeyInterface:
            def __init__(self, manager_id):
                self.identification = {"id": manager_id}

        class CacheKeyManager(GeneralManager):
            pass

        CacheKeyManager.Interface = CacheKeyInterface

        def sample_function(manager):
            return manager

        manager = CacheKeyManager(7)
        result1 = make_cache_key(sample_function, (manager,), {})

        sample_function.__module__ = "different_module"
        result2 = make_cache_key(sample_function, (manager,), {})

        self.assertNotEqual(result1, result2)

    def test_single_manager_fast_path_reads_name_without_metaclass_lookup(self):
        from general_manager.manager.general_manager import GeneralManager

        class CacheKeyInterface:
            def __init__(self, manager_id):
                self.identification = {"id": manager_id}

        class CacheKeyManager(GeneralManager):
            pass

        CacheKeyManager.Interface = CacheKeyInterface

        def sample_function(manager):
            return manager

        manager = CacheKeyManager(7)
        original_getattribute = GeneralManagerMeta.__getattribute__

        def fail_on_name_lookup(cls, attribute_name):
            if attribute_name == "__name__":
                raise AssertionError
            return original_getattribute(cls, attribute_name)

        with patch.object(
            GeneralManagerMeta,
            "__getattribute__",
            fail_on_name_lookup,
        ):
            result = make_cache_key(sample_function, (manager,), {})

        payload = {
            "module": sample_function.__module__,
            "qualname": sample_function.__qualname__,
            "args": {"manager": manager},
        }
        raw = json.dumps(payload, sort_keys=True, cls=CustomJSONEncoder).encode()
        expected = hashlib.sha256(raw, usedforsecurity=False).hexdigest()
        self.assertEqual(result, expected)

    def test_make_cache_key_with_different_args(self):
        """
        Tests that different positional arguments produce different cache keys for the same function.
        """

        def sample_function(x, y):
            return x + y

        args = (2,)
        kwargs = {"y": 4}

        result1 = make_cache_key(sample_function, args, kwargs)

        args = (1,)
        kwargs = {"y": 3}

        result2 = make_cache_key(sample_function, args, kwargs)
        self.assertNotEqual(result1, result2)

    def test_make_cache_key_with_different_kwargs(self):
        """
        Tests that different keyword arguments produce different cache keys for the same function and positional arguments.
        """

        def sample_function(x, y):
            return x + y

        args = (1,)
        kwargs1 = {"y": 3}
        kwargs2 = {"y": 4}

        result1 = make_cache_key(sample_function, args, kwargs1)
        result2 = make_cache_key(sample_function, args, kwargs2)

        self.assertNotEqual(result1, result2)

    def test_make_cache_key_with_different_function(self):
        """
        Tests that different functions with the same arguments produce different cache keys.
        """

        def sample_function1(x, y):
            return x + y

        def sample_function2(x, y):
            """
            Multiplies two values and returns the result.

            Args:
                x: The first value to multiply.
                y: The second value to multiply.

            Returns:
                The product of x and y.
            """
            return x * y

        args = (1,)
        kwargs = {"y": 3}

        result1 = make_cache_key(sample_function1, args, kwargs)
        result2 = make_cache_key(sample_function2, args, kwargs)

        self.assertNotEqual(result1, result2)

    def test_make_cache_key_with_different_module(self):
        """
        Tests that changing a function's module name results in a different cache key.

        Verifies that altering the `__module__` attribute of a function causes `make_cache_key`
        to generate distinct keys for otherwise identical function calls.
        """

        def sample_function(x, y):
            return x + y

        args = (1,)
        kwargs = {"y": 3}

        result1 = make_cache_key(sample_function, args, kwargs)

        # Simulate a different module by changing the function's __module__ attribute
        sample_function.__module__ = "different_module"
        result2 = make_cache_key(sample_function, args, kwargs)

        self.assertNotEqual(result1, result2)

    def test_make_cache_key_with_different_args_order(self):
        """
        Tests that changing the order of positional arguments results in different cache keys.

        Verifies that `make_cache_key` produces distinct keys when the same function is called
        with positional arguments in different orders.
        """

        def sample_function(x, y):
            return x + y

        args1 = (1, 3)
        kwargs1 = {}

        args2 = (3, 1)
        kwargs2 = {}

        result1 = make_cache_key(sample_function, args1, kwargs1)
        result2 = make_cache_key(sample_function, args2, kwargs2)

        self.assertNotEqual(result1, result2)

    def test_make_cache_key_with_empty_args_and_kwargs(self):
        """
        Tests that make_cache_key returns a non-None key when called with empty arguments and keyword arguments.
        """

        def sample_function():
            return 42

        args = ()
        kwargs = {}

        result = make_cache_key(sample_function, args, kwargs)
        self.assertIsNotNone(result)

    def test_make_cache_key_treats_none_kwargs_as_empty(self):
        def sample_function(x=1):
            return x

        result1 = make_cache_key(sample_function, (), None)
        result2 = make_cache_key(sample_function, (), {})

        self.assertEqual(result1, result2)

    def test_make_cache_key_preserves_falsey_mapping_kwargs(self):
        class FalseyMapping(dict[str, object]):
            def __bool__(self):
                return False

        def sample_function(x=1):
            return x

        result1 = make_cache_key(sample_function, (), FalseyMapping(x=2))
        result2 = make_cache_key(sample_function, (), {})

        self.assertNotEqual(result1, result2)

    def test_make_cache_key_with_none_args_and_kwargs(self):
        """
        Tests that make_cache_key generates a valid cache key when None values are used as arguments and keyword arguments.
        """

        def sample_function(x, y):
            return x + y

        args = (None,)
        kwargs = {"y": None}

        result = make_cache_key(sample_function, args, kwargs)
        self.assertIsNotNone(result)

    def test_make_cache_key_with_special_characters(self):
        """
        Tests that make_cache_key correctly handles arguments and keyword arguments containing special characters.
        """

        def sample_function(x, y):
            return x + y

        args = ("!@#$%^&*()",)
        kwargs = {"y": "[]{}|;:'\",.<>?"}

        result = make_cache_key(sample_function, args, kwargs)
        self.assertIsNotNone(result)

    def test_make_cache_key_with_large_data(self):
        """
        Tests that make_cache_key generates a valid 64-character key when given large data in keyword arguments.
        """

        def sample_function(x, y):
            return x + y

        args = (1,)
        kwargs = {"y": "a" * 10000}

        result = make_cache_key(sample_function, args, kwargs)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 64)

    def test_make_cache_key_with_nested_data(self):
        def sample_function(x, y):
            """
            Returns the sum of two values.

            Args:
                x: The first value to add.
                y: The second value to add.

            Returns:
                The result of adding x and y.
            """
            return x + y

        args = (1,)
        kwargs = {"y": {"a": 1, "b": [2, 3], "c": {"d": 4}}}

        result = make_cache_key(sample_function, args, kwargs)
        self.assertIsNotNone(result)

    def test_make_cache_key_uses_custom_json_encoder(self):
        def sample_function(timestamp):
            return timestamp

        timestamp = datetime(2026, 6, 23, 12, 30, 0)
        result = make_cache_key(sample_function, (timestamp,), {})

        bound = inspect.signature(sample_function).bind_partial(timestamp)
        bound.apply_defaults()
        payload = {
            "module": sample_function.__module__,
            "qualname": sample_function.__qualname__,
            "args": bound.arguments,
        }
        raw = json.dumps(payload, sort_keys=True, cls=CustomJSONEncoder).encode()
        expected = hashlib.sha256(raw, usedforsecurity=False).hexdigest()

        self.assertEqual(result, expected)

    def test_make_cache_key_does_not_override_custom_encoder_default(self):
        def sample_function(value):
            return value

        with patch("general_manager.utils.make_cache_key.json.dumps") as dumps:
            dumps.return_value = "{}"
            make_cache_key(sample_function, (object(),), {})

        self.assertIs(dumps.call_args.kwargs["cls"], CustomJSONEncoder)
        self.assertNotIn("default", dumps.call_args.kwargs)

    def test_make_cache_key_with_custom_object(self):
        """
        Tests that make_cache_key can generate a cache key when custom objects are used as arguments and keyword arguments.
        """

        class CustomObject:
            def __init__(self, value):
                self.value = value

            def __str__(self):
                return f"CustomObject({self.value})"

        def sample_function(x, y):
            """
            Returns the sum of two values.

            Args:
                x: The first value to add.
                y: The second value to add.

            Returns:
                The result of adding x and y.
            """
            return x + y

        args = (CustomObject(1),)
        kwargs = {"y": CustomObject(3)}

        result = make_cache_key(sample_function, args, kwargs)
        self.assertIsNotNone(result)

    def test_make_cache_key_with_function(self):
        """
        Tests that make_cache_key can generate a cache key when a function is passed as a keyword argument.
        """

        def sample_function(x, y):
            return x + y

        def inner_function(a, b):
            """
            Multiplies two values and returns the result.

            Args:
                a: The first value to multiply.
                b: The second value to multiply.

            Returns:
                The product of a and b.
            """
            return a * b

        args = (1,)
        kwargs = {"y": inner_function}

        result = make_cache_key(sample_function, args, kwargs)
        self.assertIsNotNone(result)

    def test_make_cache_key_with_lambda_function(self):
        """
        Tests that make_cache_key can generate a cache key when a lambda function is used as a keyword argument.
        """

        def sample_function(x, y):
            return x + y

        args = (1,)
        kwargs = {"y": lambda a: a * 2}

        result = make_cache_key(sample_function, args, kwargs)
        self.assertIsNotNone(result)

    def test_make_cache_key_with_generator(self):
        """
        Tests that make_cache_key can handle generator objects as keyword arguments and returns a valid cache key.
        """

        def sample_function(x, y):
            return x + y

        def generator_function():
            """
            A generator that yields integers from 0 to 4.

            Yields:
                int: The next integer in the range from 0 to 4.
            """
            yield from range(5)

        args = (1,)
        kwargs = {"y": generator_function()}

        result = make_cache_key(sample_function, args, kwargs)
        self.assertIsNotNone(result)

    def test_make_cache_key_with_same_function_name(self):
        """
        Tests that functions with the same name but different implementations produce different cache keys.
        """

        def create_function():
            def sample_function(x, y):
                return x + y

            return sample_function

        def create_function2():
            """
            Creates and returns a sample function that multiplies two values and scales the result by 5.

            Returns:
                A function that takes two arguments and returns their product multiplied by 5.
            """

            def sample_function(x, y):
                return x * y * 5

            return sample_function

        args = (1,)
        kwargs = {"y": 3}
        sample_function = create_function()
        sample_function2 = create_function2()
        result1 = make_cache_key(sample_function, args, kwargs)
        result2 = make_cache_key(sample_function2, args, kwargs)
        self.assertNotEqual(result1, result2)

    def test_make_cache_key_with_wrong_arg_kwarg_combination(self):
        """
        Tests that make_cache_key raises TypeError for invalid argument and keyword argument combinations.

        Verifies that passing mismatched or excessive positional and keyword arguments to make_cache_key
        with a sample function results in a TypeError.
        """

        def sample_function(x, y):
            return x + y

        cases = [
            ((1,), {"x": 3}),
            ((1, 2), {"y": 3}),
            ((1,), {"y": 3, "z": 4}),
            ((1, 2), {"x": 3, "y": 4}),
            ((), {"x": 3, "y": 4, "z": 5}),
            ((1, 2), {"x": 2}),
            ((), {"z": 3}),
        ]
        for arg_values, kwarg_values in cases:
            with (
                self.subTest(args=arg_values, kwargs=kwarg_values),
                self.assertRaises(TypeError),
            ):
                make_cache_key(sample_function, arg_values, kwarg_values)

    def test_make_cache_key_with_kwargs_as_args(self):
        """
        Tests that passing arguments as positional or keyword arguments produces the same cache key.

        Verifies that `make_cache_key` generates identical keys when function arguments are supplied as positional or as keyword arguments, provided they represent the same function call.
        """

        def sample_function(x, y):
            return x + y

        args = (1,)
        kwargs = {"y": 3}

        result1 = make_cache_key(sample_function, args, kwargs)

        args = (1, 3)
        kwargs = {}

        result2 = make_cache_key(sample_function, args, kwargs)
        self.assertEqual(result1, result2)
