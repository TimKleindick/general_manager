"""Abstract interface layer shared by all GeneralManager implementations."""

from __future__ import annotations
from abc import ABC, abstractmethod
import warnings
from typing import (
    Type,
    TYPE_CHECKING,
    Any,
    TypeVar,
    Iterable,
    ClassVar,
    Callable,
    TypedDict,
    cast,
)
from datetime import datetime
from django.conf import settings
from django.db.models import Model

from general_manager.utils.args_to_kwargs import args_to_kwargs
from general_manager.api.property import GraphQLProperty

if TYPE_CHECKING:
    from general_manager.manager.input import Input
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.bucket.base_bucket import Bucket


GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")
type generalManagerClassName = str
type attributes = dict[str, Any]
type interfaceBaseClass = Type[InterfaceBase]
type newlyCreatedInterfaceClass = Type[InterfaceBase]
type relatedClass = Type[Model] | None
type newlyCreatedGeneralManagerClass = Type[GeneralManager]

type classPreCreationMethod = Callable[
    [generalManagerClassName, attributes, interfaceBaseClass],
    tuple[attributes, interfaceBaseClass, relatedClass],
]

type classPostCreationMethod = Callable[
    [newlyCreatedGeneralManagerClass, newlyCreatedInterfaceClass, relatedClass],
    None,
]


class AttributeTypedDict(TypedDict):
    """Describe metadata captured for each interface attribute."""

    type: type
    default: Any
    is_required: bool
    is_editable: bool
    is_derived: bool


class UnexpectedInputArgumentsError(TypeError):
    """Raised when parseInputFields receives keyword arguments not defined by the interface."""

    def __init__(self, extra_args: Iterable[str]) -> None:
        """
        Initialize the exception with a message listing unexpected input argument names.

        Parameters:
            extra_args (Iterable[str]): Names of the unexpected keyword arguments to include in the error message.
        """
        extras = ", ".join(extra_args)
        super().__init__(f"Unexpected arguments: {extras}.")


class MissingInputArgumentsError(TypeError):
    """Raised when required interface inputs are not supplied."""

    def __init__(self, missing_args: Iterable[str]) -> None:
        """
        Initialize the exception for missing required input arguments.

        Parameters:
            missing_args (Iterable[str]): Names of required input arguments that were not provided; these will be joined into the exception message.
        """
        missing = ", ".join(missing_args)
        super().__init__(f"Missing required arguments: {missing}.")


class CircularInputDependencyError(ValueError):
    """Raised when input fields declare circular dependencies."""

    def __init__(self, unresolved: Iterable[str]) -> None:
        """
        Initialize the CircularInputDependencyError with the names of inputs involved in the cycle.

        Parameters:
            unresolved (Iterable[str]): Iterable of input names that form the detected circular dependency.
        """
        names = ", ".join(unresolved)
        super().__init__(f"Circular dependency detected among inputs: {names}.")


class InvalidInputTypeError(TypeError):
    """Raised when an input value does not match its declared type."""

    def __init__(self, name: str, provided: type, expected: type) -> None:
        """
        Initialize the InvalidInputTypeError with a message describing a type mismatch for a named input.

        Parameters:
            name (str): The name of the input field with the invalid type.
            provided (type): The actual type that was provided.
            expected (type): The type that was expected.
        """
        super().__init__(f"Invalid type for {name}: {provided}, expected: {expected}.")


class InvalidPossibleValuesTypeError(TypeError):
    """Raised when an input's possible_values configuration is not callable or iterable."""

    def __init__(self, name: str) -> None:
        """
        Exception raised when an input's `possible_values` configuration is neither callable nor iterable.

        Parameters:
            name (str): The input field name whose `possible_values` is invalid; included in the exception message.
        """
        super().__init__(f"Invalid type for possible_values of input {name}.")


class InvalidInputValueError(ValueError):
    """Raised when a provided input value is not within the allowed set."""

    def __init__(self, name: str, value: object, allowed: Iterable[object]) -> None:
        """
        Initialize the exception with a message describing an invalid input value for a specific field.

        Parameters:
            name (str): The name of the input field that received the invalid value.
            value (object): The value that was provided and deemed invalid.
            allowed (Iterable[object]): An iterable of permitted values for the field; used to include allowed options in the exception message.
        """
        super().__init__(
            f"Invalid value for {name}: {value}, allowed: {list(allowed)}."
        )


class InterfaceBase(ABC):
    """Common base API for interfaces backing GeneralManager classes."""

    _parent_class: ClassVar[Type["GeneralManager"]]
    _interface_type: ClassVar[str]
    _use_soft_delete: ClassVar[bool]
    input_fields: ClassVar[dict[str, "Input"]]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Initialize the interface using the provided identification inputs.

        Positional arguments are mapped to the interface's declared input fields by position; keyword arguments are matched by name. Inputs are validated and normalized according to the interface's input field definitions and the resulting normalized identification is stored on the instance as `self.identification`.

        Parameters:
            *args: Positional identification values corresponding to the interface's input field order.
            **kwargs: Named identification values matching the interface's input field names.
        """
        identification = self.parse_input_fields_to_identification(*args, **kwargs)
        self.identification = self.format_identification(identification)

    def parse_input_fields_to_identification(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Convert positional and keyword inputs into a validated identification mapping for the interface's input fields.

        Parameters:
            *args: Positional arguments matched, in order, to the interface's defined input fields.
            **kwargs: Keyword arguments supplying input values by name.

        Returns:
            dict[str, Any]: Mapping of input field names to their validated values.

        Raises:
            UnexpectedInputArgumentsError: If extra keyword arguments are provided that do not match any input field (after allowing keys suffixed with "_id").
            MissingInputArgumentsError: If one or more required input fields are not provided.
            CircularInputDependencyError: If input fields declare dependencies that form a cycle and cannot be resolved.
            InvalidInputTypeError: If a provided value does not match the declared type for an input.
            InvalidPossibleValuesTypeError: If an input's `possible_values` configuration is neither callable nor iterable.
            InvalidInputValueError: If a provided value is not in the allowed set defined by an input's `possible_values`.
        """
        identification: dict[str, Any] = {}
        kwargs = cast(
            dict[str, Any], args_to_kwargs(args, self.input_fields.keys(), kwargs)
        )
        # Check for extra arguments
        extra_args = set(kwargs.keys()) - set(self.input_fields.keys())
        if extra_args:
            handled: set[str] = set()
            for extra_arg in list(extra_args):
                if extra_arg.endswith("_id"):
                    base = extra_arg[:-3]
                    if base in self.input_fields:
                        kwargs[base] = kwargs.pop(extra_arg)
                        handled.add(extra_arg)
            # recompute remaining unknown keys after handling known *_id aliases
            remaining = (extra_args - handled) | (
                set(kwargs.keys()) - set(self.input_fields.keys())
            )
            if remaining:
                raise UnexpectedInputArgumentsError(remaining)

        missing_args = set(self.input_fields.keys()) - set(kwargs.keys())
        if missing_args:
            raise MissingInputArgumentsError(missing_args)

        # process input fields with dependencies
        processed: set[str] = set()
        while len(processed) < len(self.input_fields):
            progress_made = False
            for name, input_field in self.input_fields.items():
                if name in processed:
                    continue
                depends_on = input_field.depends_on
                if all(dep in processed for dep in depends_on):
                    value = self.input_fields[name].cast(kwargs[name])
                    self._process_input(name, value, identification)
                    identification[name] = value
                    processed.add(name)
                    progress_made = True
            if not progress_made:
                # detect circular dependencies
                unresolved = set(self.input_fields.keys()) - processed
                raise CircularInputDependencyError(unresolved)
        return identification

    @staticmethod
    def format_identification(identification: dict[str, Any]) -> dict[str, Any]:
        """
        Normalise identification data by replacing manager instances with their IDs.

        Parameters:
            identification (dict[str, Any]): Raw identification mapping possibly containing manager instances.

        Returns:
            dict[str, Any]: Identification mapping with nested managers replaced by their identifications.
        """
        from general_manager.manager.general_manager import GeneralManager

        for key, value in identification.items():
            if isinstance(value, GeneralManager):
                identification[key] = value.identification
            elif isinstance(value, (list, tuple)):
                identification[key] = []
                for v in value:
                    if isinstance(v, GeneralManager):
                        identification[key].append(v.identification)
                    elif isinstance(v, dict):
                        identification[key].append(
                            InterfaceBase.format_identification(v)
                        )
                    else:
                        identification[key].append(v)
            elif isinstance(value, dict):
                identification[key] = InterfaceBase.format_identification(value)
        return identification

    def _process_input(
        self, name: str, value: Any, identification: dict[str, Any]
    ) -> None:
        """
        Validate a single input value against its declared Input definition.

        Checks that the provided value matches the declared Python type and, when DEBUG is enabled, verifies the value is allowed by the input's `possible_values` (which may be an iterable or a callable that receives dependent input values).

        Parameters:
            name: The input field name being validated.
            value: The value to validate.
            identification: Partially resolved identification mapping used to supply dependent input values when evaluating `possible_values`.

        Raises:
            InvalidInputTypeError: If `value` is not an instance of the input's declared `type`.
            InvalidPossibleValuesTypeError: If `possible_values` is neither callable nor iterable.
            InvalidInputValueError: If `value` is not contained in the evaluated `possible_values` (only checked when DEBUG is true).
        """
        input_field = self.input_fields[name]
        if not isinstance(value, input_field.type):
            raise InvalidInputTypeError(name, type(value), input_field.type)
        if settings.DEBUG:
            # `possible_values` can be a callable or an iterable
            possible_values = input_field.possible_values
            if possible_values is not None:
                if callable(possible_values):
                    depends_on = input_field.depends_on
                    dep_values = {
                        dep_name: identification.get(dep_name)
                        for dep_name in depends_on
                    }
                    allowed_values = possible_values(**dep_values)
                elif isinstance(possible_values, Iterable):
                    allowed_values = possible_values
                else:
                    raise InvalidPossibleValuesTypeError(name)

                if value not in allowed_values:
                    raise InvalidInputValueError(name, value, allowed_values)

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """
        Create a new managed record in the underlying data store using the interface's inputs.

        Parameters:
            *args: Positional input values corresponding to the interface's defined input fields.
            **kwargs: Input values provided by name; unexpected extra keywords will be rejected.

        Returns:
            The created record or a manager-specific representation of the newly created entity.
        """
        raise NotImplementedError

    def update(self, *args: Any, **kwargs: Any) -> Any:
        """
        Update the underlying record.

        Returns:
            The updated record or a manager-specific result.
        """
        raise NotImplementedError

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        """
        Perform deletion of the underlying record managed by this interface.

        Returns:
            The result of the deletion operation as defined by the concrete implementation.
        """
        raise NotImplementedError

    def deactivate(self, *args: Any, **kwargs: Any) -> Any:
        """
        Provide a deprecated compatibility wrapper that issues a DeprecationWarning and performs the record deletion.

        Parameters:
            *args: Positional arguments forwarded to the underlying deletion implementation.
            **kwargs: Keyword arguments forwarded to the underlying deletion implementation.

        Returns:
            The result returned by the deletion operation.
        """
        warnings.warn(
            "deactivate() is deprecated; use delete() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.delete(*args, **kwargs)

    @abstractmethod
    def get_data(self) -> Any:
        """
        Return materialized data for the manager object.

        Subclasses must implement this to provide the concrete representation of the underlying managed record.

        Returns:
            The materialized data for this manager (implementation-defined).

        Raises:
            NotImplementedError: if the method is not implemented by the subclass.
        """
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
        """Return metadata describing each attribute exposed on the manager."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def get_attributes(cls) -> dict[str, Any]:
        """Return attribute values exposed via the interface."""
        raise NotImplementedError

    @classmethod
    def get_graph_ql_properties(cls) -> dict[str, GraphQLProperty]:
        """Return GraphQLProperty descriptors defined on the parent manager class."""
        if not hasattr(cls, "_parent_class"):
            return {}
        return {
            name: prop
            for name, prop in vars(cls._parent_class).items()
            if isinstance(prop, GraphQLProperty)
        }

    @classmethod
    @abstractmethod
    def filter(cls, **kwargs: Any) -> Bucket[Any]:
        """Return a bucket filtered by the provided lookup expressions."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def exclude(cls, **kwargs: Any) -> Bucket[Any]:
        """Return a bucket excluding records that match the provided lookup expressions."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def handle_interface(
        cls,
    ) -> tuple[
        classPreCreationMethod,
        classPostCreationMethod,
    ]:
        """
        Return hooks executed around GeneralManager class creation.

        Returns:
            tuple[classPreCreationMethod, classPostCreationMethod]:
                Callables executed before and after the manager class is created.
        """
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def get_field_type(cls, field_name: str) -> type:
        """
        Return the declared Python type for an input field.

        Parameters:
            field_name (str): Name of the input field.

        Returns:
            type: Python type associated with the field.

        Raises:
            NotImplementedError: This method must be implemented by subclasses.
        """
        raise NotImplementedError
