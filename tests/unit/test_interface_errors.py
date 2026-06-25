"""Unit tests for general_manager.interface.utils.errors module."""

from django.test import SimpleTestCase

import general_manager.interface.utils.errors as errors_module
from general_manager.interface.utils.errors import (
    DuplicateFieldNameError,
    InvalidFieldTypeError,
    InvalidFieldValueError,
    InvalidModelReferenceError,
    InvalidReadOnlyDataFormatError,
    InvalidReadOnlyDataTypeError,
    MissingActivationSupportError,
    MissingModelConfigurationError,
    MissingReadOnlyBindingError,
    MissingReadOnlyDataError,
    MissingUniqueFieldError,
    ReadOnlyRelationLookupError,
    UnknownFieldError,
)


class InterfaceErrorsPublicExportsTests(SimpleTestCase):
    """Tests for the public exception-module export contract."""

    def test_module_exports_shared_interface_errors(self) -> None:
        self.assertEqual(
            errors_module.__all__,
            [
                "DuplicateFieldNameError",
                "InvalidFieldTypeError",
                "InvalidFieldValueError",
                "InvalidModelReferenceError",
                "InvalidReadOnlyDataFormatError",
                "InvalidReadOnlyDataTypeError",
                "MissingActivationSupportError",
                "MissingModelConfigurationError",
                "MissingReadOnlyBindingError",
                "MissingReadOnlyDataError",
                "MissingUniqueFieldError",
                "ReadOnlyRelationLookupError",
                "UnknownFieldError",
            ],
        )


class InterfaceErrorMessageFormatTests(SimpleTestCase):
    """Tests for the public exception message formats."""

    def test_message_formats_are_stable(self) -> None:
        cases = [
            (InvalidFieldValueError("age", "bad"), "Invalid value for age: bad."),
            (
                InvalidFieldTypeError("count", TypeError("expected int")),
                "Type error for count: expected int.",
            ),
            (
                UnknownFieldError("invalid_field", "MyModel"),
                "invalid_field does not exist in MyModel.",
            ),
            (DuplicateFieldNameError(), "Field name already exists."),
            (
                MissingActivationSupportError("Product"),
                "Product must define an 'is_active' attribute.",
            ),
            (
                MissingReadOnlyDataError("CategoryInterface"),
                "ReadOnlyInterface 'CategoryInterface' must define a '_data' attribute.",
            ),
            (
                MissingUniqueFieldError("StatusInterface"),
                "ReadOnlyInterface 'StatusInterface' must declare at least one unique field.",
            ),
            (
                ReadOnlyRelationLookupError(
                    "ProductInterface", "category", 0, {"code": "XYZ"}
                ),
                "ReadOnlyInterface 'ProductInterface' could not resolve relation "
                "'category' (expected 1 match, found 0) for lookup {'code': 'XYZ'}.",
            ),
            (
                InvalidReadOnlyDataFormatError(),
                "_data JSON must decode to a list of dictionaries.",
            ),
            (
                InvalidReadOnlyDataTypeError(),
                "_data must be a JSON string or a list of dictionaries.",
            ),
            (
                MissingReadOnlyBindingError("CountryInterface"),
                "ReadOnlyInterface 'CountryInterface' must be bound to a manager and model before syncing.",
            ),
            (
                MissingModelConfigurationError("ExistingModelInterface"),
                "ExistingModelInterface must define a 'model' attribute.",
            ),
            (
                InvalidModelReferenceError("invalid.path.to.Model"),
                "Invalid model reference 'invalid.path.to.Model'.",
            ),
        ]

        for error, expected_message in cases:
            with self.subTest(error=error.__class__.__name__):
                self.assertEqual(str(error), expected_message)


class InvalidFieldValueErrorTests(SimpleTestCase):
    """Tests for InvalidFieldValueError."""

    def test_message_includes_field_and_value(self) -> None:
        """Verify error message contains field name and invalid value."""
        error = InvalidFieldValueError("age", -5)
        self.assertIn("age", str(error))
        self.assertIn("-5", str(error))


class InvalidFieldTypeErrorTests(SimpleTestCase):
    """Tests for InvalidFieldTypeError."""

    def test_message_includes_field_and_error(self) -> None:
        """Verify error message contains field name and underlying error."""
        underlying = TypeError("expected int")
        error = InvalidFieldTypeError("count", underlying)
        self.assertIn("count", str(error))
        self.assertIn("expected int", str(error))

    def test_message_is_stable(self) -> None:
        error = InvalidFieldTypeError("count", TypeError("expected int"))

        self.assertEqual(str(error), "Type error for count: expected int.")


class UnknownFieldErrorTests(SimpleTestCase):
    """Tests for UnknownFieldError."""

    def test_message_includes_field_and_model(self) -> None:
        """Verify error message contains field name and model name."""
        error = UnknownFieldError("invalid_field", "MyModel")
        self.assertIn("invalid_field", str(error))
        self.assertIn("MyModel", str(error))

    def test_message_is_stable(self) -> None:
        error = UnknownFieldError("invalid_field", "MyModel")

        self.assertEqual(str(error), "invalid_field does not exist in MyModel.")


class DuplicateFieldNameErrorTests(SimpleTestCase):
    """Tests for DuplicateFieldNameError."""

    def test_message_is_standard(self) -> None:
        """Verify error uses standard message."""
        error = DuplicateFieldNameError()
        self.assertEqual(str(error), "Field name already exists.")


class MissingActivationSupportErrorTests(SimpleTestCase):
    """Tests for MissingActivationSupportError."""

    def test_message_includes_model_name(self) -> None:
        """Verify error message contains model name."""
        error = MissingActivationSupportError("Product")
        self.assertIn("Product", str(error))
        self.assertIn("is_active", str(error))

    def test_message_is_stable(self) -> None:
        error = MissingActivationSupportError("Product")

        self.assertEqual(str(error), "Product must define an 'is_active' attribute.")


class MissingReadOnlyDataErrorTests(SimpleTestCase):
    """Tests for MissingReadOnlyDataError."""

    def test_message_includes_interface_name(self) -> None:
        """Verify error message contains interface name."""
        error = MissingReadOnlyDataError("CategoryInterface")
        self.assertIn("CategoryInterface", str(error))
        self.assertIn("_data", str(error))


class MissingUniqueFieldErrorTests(SimpleTestCase):
    """Tests for MissingUniqueFieldError."""

    def test_message_includes_interface_name(self) -> None:
        """Verify error message contains interface name."""
        error = MissingUniqueFieldError("StatusInterface")
        self.assertIn("StatusInterface", str(error))
        self.assertIn("unique field", str(error))


class ReadOnlyRelationLookupErrorTests(SimpleTestCase):
    """Tests for ReadOnlyRelationLookupError."""

    def test_message_with_zero_matches(self) -> None:
        """Verify error message for zero matches includes relevant details."""
        lookup = {"code": "XYZ"}
        error = ReadOnlyRelationLookupError("ProductInterface", "category", 0, lookup)
        message = str(error)
        self.assertIn("ProductInterface", message)
        self.assertIn("category", message)
        self.assertIn("0", message)
        self.assertIn("XYZ", message)
        self.assertIn("expected 1 match", message)

    def test_message_with_multiple_matches(self) -> None:
        """Verify error message for multiple matches includes count."""
        lookup = {"status": "active"}
        error = ReadOnlyRelationLookupError("OrderInterface", "customer", 3, lookup)
        message = str(error)
        self.assertIn("OrderInterface", message)
        self.assertIn("customer", message)
        self.assertIn("3", message)
        self.assertIn("active", message)

    def test_message_with_non_dict_lookup(self) -> None:
        """Verify error handles non-dict lookup values."""
        error = ReadOnlyRelationLookupError("ItemInterface", "supplier", 2, 42)
        message = str(error)
        self.assertIn("ItemInterface", message)
        self.assertIn("supplier", message)
        self.assertIn("42", message)


class InvalidReadOnlyDataFormatErrorTests(SimpleTestCase):
    """Tests for InvalidReadOnlyDataFormatError."""

    def test_message_describes_expected_format(self) -> None:
        """Verify error message describes expected list of dictionaries."""
        error = InvalidReadOnlyDataFormatError()
        message = str(error)
        self.assertIn("list of dictionaries", message)


class InvalidReadOnlyDataTypeErrorTests(SimpleTestCase):
    """Tests for InvalidReadOnlyDataTypeError."""

    def test_message_describes_expected_types(self) -> None:
        """Verify error message describes expected JSON string or list."""
        error = InvalidReadOnlyDataTypeError()
        message = str(error)
        self.assertIn("JSON string", message)
        self.assertIn("list of dictionaries", message)


class MissingReadOnlyBindingErrorTests(SimpleTestCase):
    """Tests for MissingReadOnlyBindingError."""

    def test_message_includes_interface_name(self) -> None:
        """Verify error message contains interface name and binding requirement."""
        error = MissingReadOnlyBindingError("CountryInterface")
        message = str(error)
        self.assertIn("CountryInterface", message)
        self.assertIn("bound", message)
        self.assertIn("manager and model", message)


class MissingModelConfigurationErrorTests(SimpleTestCase):
    """Tests for MissingModelConfigurationError."""

    def test_message_includes_interface_name(self) -> None:
        """Verify error message contains interface name."""
        error = MissingModelConfigurationError("ExistingModelInterface")
        message = str(error)
        self.assertIn("ExistingModelInterface", message)
        self.assertIn("model", message)


class InvalidModelReferenceErrorTests(SimpleTestCase):
    """Tests for InvalidModelReferenceError."""

    def test_message_includes_reference(self) -> None:
        """Verify error message contains the invalid reference."""
        error = InvalidModelReferenceError("invalid.path.to.Model")
        message = str(error)
        self.assertIn("invalid.path.to.Model", message)

    def test_message_with_object_reference(self) -> None:
        """Verify error handles non-string references."""
        obj = object()
        error = InvalidModelReferenceError(obj)
        message = str(error)
        self.assertIn("object", message)
