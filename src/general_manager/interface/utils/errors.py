"""Public exception types shared by GeneralManager interface implementations."""

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
    "MissingReadOnlyBindingError",
    "MissingReadOnlyDataError",
    "MissingUniqueFieldError",
    "ReadOnlyRelationLookupError",
    "UnknownFieldError",
]


class InvalidFieldValueError(ValueError):
    """Raised when model-field assignment rejects a value.

    Used by writable ORM capabilities after Django/model assignment raises
    `ValueError` for a payload field.
    """

    def __init__(self, field_name: str, value: object) -> None:
        """Build an error message for an invalid field value.

        Args:
            field_name: Payload/model field name that rejected the value.
            value: Rejected value included in the message with `str(value)`.

        Message:
            `Invalid value for {field_name}: {value}.`
        """
        super().__init__(f"Invalid value for {field_name}: {value}.")


class InvalidFieldTypeError(TypeError):
    """Raised when model-field assignment rejects a value type.

    Used by writable ORM capabilities after Django/model assignment raises
    `TypeError` for a payload field.
    """

    def __init__(self, field_name: str, error: TypeError) -> None:
        """Build an error message for a field type failure.

        Args:
            field_name: Payload/model field name that rejected the value.
            error: Original `TypeError`; its message is embedded unchanged.

        Message:
            `Type error for {field_name}: {error}.`
        """
        super().__init__(f"Type error for {field_name}: {error}.")


class UnknownFieldError(ValueError):
    """Raised when write payloads reference fields absent from the model."""

    def __init__(self, field_name: str, model_name: str) -> None:
        """Build an error message for an unknown model field.

        Args:
            field_name: Payload field name that was not recognized.
            model_name: Name of the Django model checked for that field.

        Message:
            `{field_name} does not exist in {model_name}.`
        """
        super().__init__(f"{field_name} does not exist in {model_name}.")


class DuplicateFieldNameError(ValueError):
    """Raised when generated interface descriptors collide on one name."""

    def __init__(self) -> None:
        """Build the fixed `Field name already exists.` message."""
        super().__init__("Field name already exists.")


class MissingActivationSupportError(TypeError):
    """Raised when soft delete needs model `is_active` support."""

    def __init__(self, model_name: str) -> None:
        """Build an error message for missing soft-delete support.

        Args:
            model_name: Name of the model class missing `is_active`.

        Message:
            `{model_name} must define an 'is_active' attribute.`
        """
        super().__init__(f"{model_name} must define an 'is_active' attribute.")


class MissingReadOnlyDataError(ValueError):
    """Raised when a read-only manager lacks its `_data` source."""

    def __init__(self, interface_name: str) -> None:
        """Build an error message for missing read-only data.

        Args:
            interface_name: Name of the read-only manager/interface owner.

        Message:
            `ReadOnlyInterface '{interface_name}' must define a '_data' attribute.`
        """
        super().__init__(
            f"ReadOnlyInterface '{interface_name}' must define a '_data' attribute."
        )


class MissingUniqueFieldError(ValueError):
    """Raised when read-only sync cannot determine a row identity."""

    def __init__(self, interface_name: str) -> None:
        """Build an error message for missing unique field metadata.

        Args:
            interface_name: Name of the read-only manager/interface owner.

        Message:
            `ReadOnlyInterface '{interface_name}' must declare at least one unique field.`
        """
        super().__init__(
            f"ReadOnlyInterface '{interface_name}' must declare at least one unique field."
        )


class ReadOnlyRelationLookupError(ValueError):
    """Raised when read-only sync resolves zero or multiple related rows."""

    def __init__(
        self,
        interface_name: str,
        field_name: str,
        matches: int,
        lookup: object,
    ) -> None:
        """Build an error message for an ambiguous relation lookup.

        Args:
            interface_name: Name of the read-only manager/interface owner.
            field_name: Relation field being resolved.
            matches: Number of matching rows; exactly one is required.
            lookup: Lookup payload included in the message with `repr(lookup)`.

        Message:
            `ReadOnlyInterface '{interface_name}' could not resolve relation
            '{field_name}' (expected 1 match, found {matches}) for lookup
            {lookup!r}.`
        """
        super().__init__(
            (
                f"ReadOnlyInterface '{interface_name}' could not resolve relation "
                f"'{field_name}' (expected 1 match, found {matches}) for lookup "
                f"{lookup!r}."
            )
        )


class InvalidReadOnlyDataFormatError(TypeError):
    """Raised when read-only `_data` has an invalid row/list shape."""

    def __init__(self) -> None:
        """Build the fixed `_data JSON must decode to a list of dictionaries.` message."""
        super().__init__("_data JSON must decode to a list of dictionaries.")


class InvalidReadOnlyDataTypeError(TypeError):
    """Raised when read-only `_data` is neither a JSON string nor a list."""

    def __init__(self) -> None:
        """Build the fixed `_data must be a JSON string or a list of dictionaries.` message."""
        super().__init__("_data must be a JSON string or a list of dictionaries.")


class MissingReadOnlyBindingError(RuntimeError):
    """Raised when read-only sync runs before lifecycle binding completes."""

    def __init__(self, interface_name: str) -> None:
        """Build an error message for missing manager/model binding.

        Args:
            interface_name: Name of the unbound read-only interface class.

        Message:
            `ReadOnlyInterface '{interface_name}' must be bound to a manager and model before syncing.`
        """
        super().__init__(
            f"ReadOnlyInterface '{interface_name}' must be bound to a manager and model before syncing."
        )


class MissingModelConfigurationError(ValueError):
    """Raised when an `ExistingModelInterface` does not declare `model`."""

    def __init__(self, interface_name: str) -> None:
        """Build an error message for missing existing-model configuration.

        Args:
            interface_name: Name of the interface class missing `model`.

        Message:
            `{interface_name} must define a 'model' attribute.`
        """
        super().__init__(f"{interface_name} must define a 'model' attribute.")


class InvalidModelReferenceError(TypeError):
    """Raised when an existing-model reference cannot be resolved."""

    def __init__(self, reference: object) -> None:
        """Build an error message for an invalid model reference.

        Args:
            reference: Invalid value from `ExistingModelInterface.model`; its
                string form is included in the message.

        Message:
            `Invalid model reference '{reference}'.`
        """
        super().__init__(f"Invalid model reference '{reference}'.")
