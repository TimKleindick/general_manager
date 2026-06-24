from django.test import TestCase
from collections.abc import Iterator, Mapping

from general_manager.utils.args_to_kwargs import (
    ConflictingKeywordError,
    TooManyArgumentsError,
    args_to_kwargs,
)


class FalseyMapping(dict[str, object]):
    """Mapping with entries that still evaluates as false."""

    def __bool__(self) -> bool:
        return False


class BrokenKeysError(RuntimeError):
    """Raised by the test iterable when key materialization fails."""


class BrokenValueError(RuntimeError):
    """Raised by the test mapping when value retrieval fails."""


class BrokenKeys:
    """Iterable that raises while keys are materialized."""

    def __iter__(self) -> Iterator[str]:
        raise BrokenKeysError


class BrokenValueMapping(Mapping[str, object]):
    """Mapping that can be iterated but raises when values are read."""

    def __iter__(self) -> Iterator[str]:
        return iter(("b",))

    def __len__(self) -> int:
        return 1

    def __getitem__(self, key: str) -> object:
        raise BrokenValueError


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
        with self.assertRaises(TooManyArgumentsError):
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
        with self.assertRaises(ConflictingKeywordError):
            args_to_kwargs((5,), ["x"], {"x": 42, "y": 43})

    def test_falsey_existing_kwargs_are_merged(self):
        """Falsey custom mappings are still treated as supplied kwargs."""
        result = args_to_kwargs((1,), ["a"], FalseyMapping({"b": 2}))

        self.assertEqual(result, {"a": 1, "b": 2})

    def test_falsey_existing_kwargs_still_conflict(self):
        """Conflicts are detected even when the supplied mapping is falsey."""
        with self.assertRaises(ConflictingKeywordError):
            args_to_kwargs((1,), ["a"], FalseyMapping({"a": 2}))

    def test_keys_iterable_is_materialized_once(self):
        """One-shot key iterables are accepted and consumed in order."""
        result = args_to_kwargs((1, 2), (key for key in ("a", "b", "c")))

        self.assertEqual(result, {"a": 1, "b": 2})

    def test_duplicate_generated_keys_follow_dict_overwrite_semantics(self):
        """Duplicate generated keys keep the last paired positional value."""
        result = args_to_kwargs((1, 2), ["a", "a"])

        self.assertEqual(result, {"a": 2})

    def test_keys_iteration_errors_propagate(self):
        """Errors from materializing keys are not wrapped."""
        with self.assertRaises(BrokenKeysError):
            args_to_kwargs((), BrokenKeys())

    def test_existing_kwargs_value_errors_propagate(self):
        """Errors from reading existing mapping values are not wrapped."""
        with self.assertRaises(BrokenValueError):
            args_to_kwargs((1,), ["a"], BrokenValueMapping())

    def test_return_order_and_existing_kwargs_are_not_mutated(self):
        """Generated keys come first and the supplied mapping is copied."""
        existing_kwargs = {"c": 3, "d": 4}

        result = args_to_kwargs((1, 2), ["a", "b"], existing_kwargs)

        self.assertEqual(list(result.items()), [("a", 1), ("b", 2), ("c", 3), ("d", 4)])
        self.assertEqual(existing_kwargs, {"c": 3, "d": 4})
        self.assertIsNot(result, existing_kwargs)
