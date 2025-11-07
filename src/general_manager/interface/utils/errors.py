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
        super().__init__(f"Invalid value for {field_name}: {value}.")


class InvalidFieldTypeError(TypeError):
    """Raised when assigning a value with an unexpected type."""

    def __init__(self, field_name: str, error: Exception) -> None:
        super().__init__(f"Type error for {field_name}: {error}.")


class UnknownFieldError(ValueError):
    """Raised when keyword arguments reference fields not present on the model."""

    def __init__(self, field_name: str, model_name: str) -> None:
        super().__init__(f"{field_name} does not exist in {model_name}.")


class DuplicateFieldNameError(ValueError):
    """Raised when a dynamically generated field name conflicts with an existing one."""

    def __init__(self) -> None:
        super().__init__("Field name already exists.")


class MissingActivationSupportError(TypeError):
    """Raised when a model does not expose the expected `is_active` attribute."""

    def __init__(self, model_name: str) -> None:
        super().__init__(f"{model_name} must define an 'is_active' attribute.")


class MissingReadOnlyDataError(ValueError):
    """Raised when a read-only manager lacks the `_data` source."""

    def __init__(self, interface_name: str) -> None:
        super().__init__(
            f"ReadOnlyInterface '{interface_name}' must define a '_data' attribute."
        )


class MissingUniqueFieldError(ValueError):
    """Raised when read-only models provide no unique identifiers."""

    def __init__(self, interface_name: str) -> None:
        super().__init__(
            f"ReadOnlyInterface '{interface_name}' must declare at least one unique field."
        )


class InvalidReadOnlyDataFormatError(TypeError):
    """Raised when `_data` JSON does not decode into a list of dictionaries."""

    def __init__(self) -> None:
        super().__init__("_data JSON must decode to a list of dictionaries.")


class InvalidReadOnlyDataTypeError(TypeError):
    """Raised when `_data` is neither JSON string nor list."""

    def __init__(self) -> None:
        super().__init__("_data must be a JSON string or a list of dictionaries.")


class MissingModelConfigurationError(ValueError):
    """Raised when an ExistingModelInterface does not declare a `model`."""

    def __init__(self, interface_name: str) -> None:
        super().__init__(f"{interface_name} must define a 'model' attribute.")


class InvalidModelReferenceError(TypeError):
    """Raised when the configured model reference cannot be resolved."""

    def __init__(self, reference: object) -> None:
        super().__init__(f"Invalid model reference '{reference}'.")
