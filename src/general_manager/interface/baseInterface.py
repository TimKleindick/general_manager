"""Abstract interface layer shared by all GeneralManager implementations."""

from __future__ import annotations
from abc import ABC, abstractmethod
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

from general_manager.utils import args_to_kwargs
from general_manager.api.property import GraphQLProperty

if TYPE_CHECKING:
    from general_manager.manager.input import Input
    from general_manager.manager.generalManager import GeneralManager
    from general_manager.bucket.baseBucket import Bucket


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


class InterfaceBase(ABC):
    """Common base API for interfaces backing GeneralManager classes."""

    _parent_class: Type[GeneralManager]
    _interface_type: ClassVar[str]
    input_fields: dict[str, Input]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Construct the interface using the supplied identification arguments.

        Parameters:
            *args: Positional arguments passed to the interface constructor.
            **kwargs: Keyword arguments passed to the interface constructor.

        Returns:
            None
        """
        identification = self.parseInputFieldsToIdentification(*args, **kwargs)
        self.identification = self.formatIdentification(identification)

    def parseInputFieldsToIdentification(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Parse raw arguments into a validated identification mapping.

        Parameters:
            *args (Any): Positional arguments matched to the interface's input field order.
            **kwargs (dict[str, Any]): Keyword arguments supplied by the caller.

        Returns:
            dict[str, Any]: Mapping of input field names to validated values.

        Raises:
            TypeError: If required inputs are missing, unexpected inputs are provided, or a value fails type checking.
            ValueError: If circular dependencies prevent resolution of the inputs.
        """
        identification: dict[str, Any] = {}
        kwargs = cast(
            dict[str, Any], args_to_kwargs(args, self.input_fields.keys(), kwargs)
        )
        # Check for extra arguments
        extra_args = set(kwargs.keys()) - set(self.input_fields.keys())
        if extra_args:
            for extra_arg in extra_args:
                if extra_arg.replace("_id", "") in self.input_fields.keys():
                    kwargs[extra_arg.replace("_id", "")] = kwargs.pop(extra_arg)
                else:
                    raise TypeError(f"Unexpected arguments: {', '.join(extra_args)}")

        missing_args = set(self.input_fields.keys()) - set(kwargs.keys())
        if missing_args:
            raise TypeError(f"Missing required arguments: {', '.join(missing_args)}")

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
                raise ValueError(
                    f"Circular dependency detected among inputs: {', '.join(unresolved)}"
                )
        return identification

    @staticmethod
    def formatIdentification(identification: dict[str, Any]) -> dict[str, Any]:
        """
        Normalise identification data by replacing manager instances with their IDs.

        Parameters:
            identification (dict[str, Any]): Raw identification mapping possibly containing manager instances.

        Returns:
            dict[str, Any]: Identification mapping with nested managers replaced by their identifications.
        """
        from general_manager.manager.generalManager import GeneralManager

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
                            InterfaceBase.formatIdentification(v)
                        )
                    else:
                        identification[key].append(v)
            elif isinstance(value, dict):
                identification[key] = InterfaceBase.formatIdentification(value)
        return identification

    def _process_input(
        self, name: str, value: Any, identification: dict[str, Any]
    ) -> None:
        """
        Validate a single input value against its definition.

        Parameters:
            name (str): Input field name being processed.
            value (Any): Value provided by the caller.
            identification (dict[str, Any]): Partially resolved identification mapping used to evaluate dependencies.

        Returns:
            None

        Raises:
            TypeError: If the value has the wrong type or possible values are misconfigured.
            ValueError: If the value is not permitted by the configured `possible_values`.
        """
        input_field = self.input_fields[name]
        if not isinstance(value, input_field.type):
            raise TypeError(
                f"Invalid type for {name}: {type(value)}, expected: {input_field.type}"
            )
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
                    raise TypeError(f"Invalid type for possible_values of input {name}")

                if value not in allowed_values:
                    raise ValueError(
                        f"Invalid value for {name}: {value}, allowed: {allowed_values}"
                    )

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> Any:
        """Create a new record via the underlying data source."""
        raise NotImplementedError

    def update(self, *args: Any, **kwargs: Any) -> Any:
        """Update the underlying record."""
        raise NotImplementedError

    def deactivate(self, *args: Any, **kwargs: Any) -> Any:
        """Deactivate the underlying record."""
        raise NotImplementedError

    @abstractmethod
    def getData(self, search_date: datetime | None = None) -> Any:
        """Return data materialised for the manager object."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def getAttributeTypes(cls) -> dict[str, AttributeTypedDict]:
        """Return metadata describing each attribute exposed on the manager."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def getAttributes(cls) -> dict[str, Any]:
        """Return attribute values exposed via the interface."""
        raise NotImplementedError

    @classmethod
    def getGraphQLProperties(cls) -> dict[str, GraphQLProperty]:
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
    def handleInterface(
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
    def getFieldType(cls, field_name: str) -> type:
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
