from django.test import SimpleTestCase

from general_manager.utils.type_checks import safe_issubclass


class SafeIssubclassTests(SimpleTestCase):
    def test_returns_true_for_subclass(self) -> None:
        """Class candidates that inherit from the parent return True."""

        class Parent:
            pass

        class Child(Parent):
            pass

        self.assertTrue(safe_issubclass(Child, Parent))

    def test_returns_false_for_instances_and_none(self) -> None:
        """Non-class candidates return False instead of raising TypeError."""

        class Parent:
            pass

        self.assertFalse(safe_issubclass(Parent(), Parent))
        self.assertFalse(safe_issubclass(None, Parent))

    def test_accepts_parent_tuple(self) -> None:
        """Tuple parents use Python issubclass tuple semantics."""

        class First:
            pass

        class Second:
            pass

        self.assertTrue(safe_issubclass(Second, (First, Second)))
        self.assertFalse(safe_issubclass(str, (First, Second)))

    def test_invalid_parent_tuple_type_error_propagates(self) -> None:
        """Invalid parent tuple entries are not wrapped."""
        with self.assertRaises(TypeError):
            safe_issubclass(str, (int, "not-a-class"))  # type: ignore[arg-type]
