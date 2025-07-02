from django.test import SimpleTestCase
from general_manager.auxiliary.formatString import (
    snake_to_camel,
    snake_to_pascal,
    camel_to_snake,
    pascal_to_snake,
)


class FormatStringTests(SimpleTestCase):
    def test_snake_to_pascal(self):
        """
        Test that snake_case strings are correctly converted to PascalCase by the snake_to_pascal function.
        
        Covers typical multi-word cases, single words, empty strings, and sequences of single characters.
        """
        self.assertEqual(snake_to_pascal("test_string"), "TestString")
        self.assertEqual(snake_to_pascal("another_test_case"), "AnotherTestCase")
        self.assertEqual(snake_to_pascal("singleword"), "Singleword")
        self.assertEqual(snake_to_pascal(""), "")
        self.assertEqual(snake_to_pascal("a_b_c"), "ABC")
        self.assertEqual(snake_to_pascal("a"), "A")

    def test_snake_to_camel(self):
        """
        Test that `snake_to_camel` correctly converts snake_case strings to camelCase, including edge cases such as empty strings and single-character segments.
        """
        self.assertEqual(snake_to_camel("test_string"), "testString")
        self.assertEqual(snake_to_camel("another_test_case"), "anotherTestCase")
        self.assertEqual(snake_to_camel("singleword"), "singleword")
        self.assertEqual(snake_to_camel(""), "")
        self.assertEqual(snake_to_camel("a_b_c"), "aBC")
        self.assertEqual(snake_to_camel("a"), "a")
        self.assertEqual(snake_to_camel("a_b"), "aB")

    def test_pascal_to_snake(self):
        """
        Test that the pascal_to_snake function correctly converts PascalCase strings to snake_case.
        
        Covers typical multi-word inputs, single words, empty strings, acronyms, and single-character cases.
        """
        self.assertEqual(pascal_to_snake("TestString"), "test_string")
        self.assertEqual(pascal_to_snake("AnotherTestCase"), "another_test_case")
        self.assertEqual(pascal_to_snake("Singleword"), "singleword")
        self.assertEqual(pascal_to_snake(""), "")
        self.assertEqual(pascal_to_snake("ABC"), "a_b_c")
        self.assertEqual(pascal_to_snake("A"), "a")

    def test_camel_to_snake(self):
        """
        Test that the `camel_to_snake` function correctly converts camelCase strings to snake_case.
        
        Verifies conversion for typical camelCase inputs, single words, empty strings, and edge cases with acronyms or single characters.
        """
        self.assertEqual(camel_to_snake("testString"), "test_string")
        self.assertEqual(camel_to_snake("anotherTestCase"), "another_test_case")
        self.assertEqual(camel_to_snake("singleword"), "singleword")
        self.assertEqual(camel_to_snake(""), "")
        self.assertEqual(camel_to_snake("aBC"), "a_b_c")
        self.assertEqual(camel_to_snake("a"), "a")
        self.assertEqual(camel_to_snake("aB"), "a_b")
