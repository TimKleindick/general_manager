"""Exception types shared across GeneralManager interfaces."""

from __future__ import annotations

__all__ = [
    "DuplicateFieldNameError",
    "InvalidFieldTypeError",
    "InvalidFieldValueError",
    "InvalidModelReferenceError",
    "InvalidReadOnlyDataFormatError",
    "InvalidReadOnlyDataTypeError",
    "MissingActivationSupportError",
    "MissingModelConfigurationError",
    "MissingReadOnlyDataError",
    "MissingUniqueFieldError",
    "UnknownFieldError",
]


class InvalidFieldValueError(ValueError):
    """Raised when assigning a value incompatible with the model field."""

    def __init__(self, field_name: str, value: object) -> None:
        """
        Initialize the exception indicating a value is invalid for a specific model field.

        Parameters:
            field_name: Name of the field that received the invalid value.
            value: The invalid value that caused the error.
        """
        super().__init__(f"Invalid value for {field_name}: {value}.")


class InvalidFieldTypeError(TypeError):
    """Raised when assigning a value with an unexpected type."""

    def __init__(self, field_name: str, error: Exception) -> None:
        """
        Construct an InvalidFieldTypeError describing a type-related error for a model field.

        Parameters:
            field_name (str): Name of the field that received a value with an unexpected type.
            error (Exception): The underlying exception or error message that caused the type error; its text is included in the constructed message.
        """
        super().__init__(f"Type error for {field_name}: {error}.")


class UnknownFieldError(ValueError):
    """Raised when keyword arguments reference fields not present on the model."""

    def __init__(self, field_name: str, model_name: str) -> None:
        """
        Initialize the exception for a missing field on a model.

        Parameters:
            field_name (str): Name of the field that was not found.
            model_name (str): Name of the model where the field was expected.
        """
        super().__init__(f"{field_name} does not exist in {model_name}.")


class DuplicateFieldNameError(ValueError):
    """Raised when a dynamically generated field name conflicts with an existing one."""

    def __init__(self) -> None:
        """
        Initialize the DuplicateFieldNameError with a standard message indicating a field name conflict.

        The exception message is "Field name already exists." and no parameters are accepted.
        """
        super().__init__("Field name already exists.")


class MissingActivationSupportError(TypeError):
    """Raised when a model does not expose the expected `is_active` attribute."""

    def __init__(self, model_name: str) -> None:
        """
        Initialize the error with a message stating that the given model must expose an `is_active` attribute.

        Parameters:
            model_name (str): Name of the model missing the required `is_active` attribute.
        """
        super().__init__(f"{model_name} must define an 'is_active' attribute.")


class MissingReadOnlyDataError(ValueError):
    """Raised when a read-only manager lacks the `_data` source."""

    def __init__(self, interface_name: str) -> None:
        """
        Error raised when a read-only interface does not declare the required `_data` attribute.

        Parameters:
            interface_name (str): Name of the read-only interface missing the `_data` attribute; used to construct the exception message.
        """
        super().__init__(
            f"ReadOnlyInterface '{interface_name}' must define a '_data' attribute."
        )


class MissingUniqueFieldError(ValueError):
    """Raised when read-only models provide no unique identifiers."""

    def __init__(self, interface_name: str) -> None:
        """
        Initialize the exception raised when a read-only interface does not declare any unique identifier fields.

        Parameters:
            interface_name (str): Name of the read-only interface missing a unique field; included in the exception message.
        """
        super().__init__(
            f"ReadOnlyInterface '{interface_name}' must declare at least one unique field."
        )


class InvalidReadOnlyDataFormatError(TypeError):
    """Raised when `_data` JSON does not decode into a list of dictionaries."""

    def __init__(self) -> None:
        """
        Initialize the exception with a standardized message about the expected `_data` JSON structure.

        Sets the exception message to "_data JSON must decode to a list of dictionaries."
        """
        super().__init__("_data JSON must decode to a list of dictionaries.")


class InvalidReadOnlyDataTypeError(TypeError):
    """Raised when `_data` is neither JSON string nor list."""

    def __init__(self) -> None:
        """
        Error raised when a read-only manager's `_data` is neither a JSON string nor a list of dictionaries.

        Initializes the exception with the message: "_data must be a JSON string or a list of dictionaries."
        """
        super().__init__("_data must be a JSON string or a list of dictionaries.")


class MissingModelConfigurationError(ValueError):
    """Raised when an ExistingModelInterface does not declare a `model`."""

    def __init__(self, interface_name: str) -> None:
        """
        Indicates that an interface is missing its required `model` configuration.

        Parameters:
            interface_name (str): Name of the interface that must declare a `model` attribute.

        Description:
            Initializes the exception with a message stating which interface must define a `model` attribute.
        """
        super().__init__(f"{interface_name} must define a 'model' attribute.")


class InvalidModelReferenceError(TypeError):
    """Raised when the configured model reference cannot be resolved."""

    def __init__(self, reference: object) -> None:
        """
        Initialize the exception for an invalid model reference.

        Parameters:
            reference (object): The model reference that could not be resolved; its representation is included in the exception message.
        """
        super().__init__(f"Invalid model reference '{reference}'.")
