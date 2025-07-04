from django.test import SimpleTestCase
from general_manager.auxiliary.makeCacheKey import make_cache_key


class TestMakeCacheKey(SimpleTestCase):

    def test_make_cache_key(self):
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
        kwargs = {"y": 3}

        result = make_cache_key(sample_function, args, kwargs)
        self.assertIsNotNone(result)

        result2 = make_cache_key(sample_function, args, kwargs)
        self.assertEqual(result, result2)

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
