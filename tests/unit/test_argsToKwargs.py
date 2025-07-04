from django.test import TestCase
from general_manager.auxiliary.argsToKwargs import args_to_kwargs


class TestArgsToKwargs(TestCase):
    def setUp(self):
        """Setup shared test data."""
        self.keys = ["a", "b", "c"]
        self.args = (1, 2, 3)
        self.existing_kwargs = {"d": 4}

    def test_standard_case(self):
        """Test case with standard args and existing kwargs."""
        result = args_to_kwargs(self.args, self.keys, self.existing_kwargs)
        self.assertEqual(result, {"a": 1, "b": 2, "c": 3, "d": 4})

    def test_no_existing_kwargs(self):
        """Test case without existing kwargs."""
        result = args_to_kwargs(self.args, self.keys)
        self.assertEqual(result, {"a": 1, "b": 2, "c": 3})

    def test_fewer_args_than_keys(self):
        """Test case where fewer args than keys are provided."""
        result = args_to_kwargs((1,), self.keys)
        self.assertEqual(result, {"a": 1})

    def test_more_args_than_keys(self):
        """Test case where more args than keys are provided."""
        with self.assertRaises(ValueError):
            args_to_kwargs((1, 2, 3, 4), self.keys)

    def test_empty_args_and_keys(self):
        """Test case with empty args and keys."""
        result = args_to_kwargs((), [])
        self.assertEqual(result, {})

    def test_only_existing_kwargs(self):
        """Test case with only existing kwargs provided."""
        result = args_to_kwargs((), [], {"x": 42})
        self.assertEqual(result, {"x": 42})

    def test_conflicts_in_existing_kwargs(self):
        """Test case with conflicts in existing kwargs."""
        with self.assertRaises(ValueError):
            args_to_kwargs((5,), ["x"], {"x": 42, "y": 43})
