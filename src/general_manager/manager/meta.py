"""Metaclass infrastructure for registering GeneralManager subclasses."""

from __future__ import annotations

from django.conf import settings
from typing import Any, Type, TYPE_CHECKING, ClassVar, TypeVar, Iterable, cast
from general_manager.interface.baseInterface import InterfaceBase

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager


GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")


class InvalidInterfaceTypeError(TypeError):
    """Raised when a GeneralManager is configured with an incompatible Interface class."""

    def __init__(self, interface_name: str) -> None:
        """
        Initialize an InvalidInterfaceTypeError indicating a configured interface is not a subclass of InterfaceBase.

        Parameters:
            interface_name (str): Name of the configured interface class that is invalid; included in the exception message.
        """
        super().__init__(f"{interface_name} must be a subclass of InterfaceBase.")


class MissingAttributeError(AttributeError):
    """Raised when a dynamically generated descriptor cannot locate the attribute."""

    def __init__(self, attribute_name: str, class_name: str) -> None:
        """
        Initialize the MissingAttributeError with the missing attribute and its owning class.

        Parameters:
            attribute_name (str): Name of the attribute that was not found.
            class_name (str): Name of the class where the attribute lookup occurred.

        The exception message is set to "`{attribute_name} not found in {class_name}.`".
        """
        super().__init__(f"{attribute_name} not found in {class_name}.")


class AttributeEvaluationError(AttributeError):
    """Raised when evaluating a callable attribute raises an exception."""

    def __init__(self, attribute_name: str, error: Exception) -> None:
        """
        Initialize an AttributeEvaluationError that wraps an exception raised while evaluating a descriptor attribute.

        Parameters:
            attribute_name (str): Name of the attribute whose evaluation failed.
            error (Exception): The original exception that was raised; retained for inspection.
        """
        super().__init__(f"Error calling attribute {attribute_name}: {error}.")


class _nonExistent:
    pass


class GeneralManagerMeta(type):
    """Metaclass responsible for wiring GeneralManager interfaces and registries."""

    all_classes: ClassVar[list[Type[GeneralManager]]] = []
    read_only_classes: ClassVar[list[Type[GeneralManager]]] = []
    pending_graphql_interfaces: ClassVar[list[Type[GeneralManager]]] = []
    pending_attribute_initialization: ClassVar[list[Type[GeneralManager]]] = []
    Interface: type[InterfaceBase]

    def __new__(
        mcs: type["GeneralManagerMeta"],
        name: str,
        bases: tuple[type, ...],
        attrs: dict[str, Any],
    ) -> type:
        """
        Create a GeneralManager subclass, integrate any declared Interface hooks, and register the class for pending initialization and GraphQL processing.

        If the class body defines an `Interface`, validates it is a subclass of `InterfaceBase`, invokes the interface's `handleInterface()` pre-creation hook to allow modification of the class namespace, creates the class, then invokes the post-creation hook and registers the class for attribute initialization and global tracking. If `Interface` is not defined, creates the class directly. If `settings.AUTOCREATE_GRAPHQL` is true, registers the created class for GraphQL interface processing.

        Parameters:
            mcs (type): The metaclass creating the class.
            name (str): Name of the class being created.
            bases (tuple[type, ...]): Base classes for the new class.
            attrs (dict[str, Any]): Class namespace supplied during creation.

        Returns:
            type: The newly created subclass, possibly modified by Interface hooks.
        """

        def createNewGeneralManagerClass(
            mcs: type["GeneralManagerMeta"],
            name: str,
            bases: tuple[type, ...],
            attrs: dict[str, Any],
        ) -> Type["GeneralManager"]:
            """Helper to instantiate the class via the default ``type.__new__``."""
            return cast(Type["GeneralManager"], type.__new__(mcs, name, bases, attrs))

        if "Interface" in attrs:
            interface = attrs.pop("Interface")
            if not issubclass(interface, InterfaceBase):
                raise InvalidInterfaceTypeError(interface.__name__)
            preCreation, postCreation = interface.handleInterface()
            attrs, interface_cls, model = preCreation(name, attrs, interface)
            new_class = createNewGeneralManagerClass(mcs, name, bases, attrs)
            postCreation(new_class, interface_cls, model)
            mcs.pending_attribute_initialization.append(new_class)
            mcs.all_classes.append(new_class)

        else:
            new_class = createNewGeneralManagerClass(mcs, name, bases, attrs)

        if getattr(settings, "AUTOCREATE_GRAPHQL", False):
            mcs.pending_graphql_interfaces.append(new_class)

        return new_class

    @staticmethod
    def createAtPropertiesForAttributes(
        attributes: Iterable[str], new_class: Type[GeneralManager]
    ) -> None:
        """
        Attach descriptor properties to new_class for each name in attributes.

        Each generated descriptor returns the interface field type when accessed on the class and resolves the corresponding value from instance._attributes when accessed on an instance. If the stored value is callable it is invoked with instance._interface; a missing attribute raises MissingAttributeError and an exception raised while invoking a callable is wrapped in AttributeEvaluationError.

        Parameters:
            attributes (Iterable[str]): Names of attributes for which descriptors will be created.
            new_class (Type[GeneralManager]): Class that will receive the generated descriptor attributes.
        """

        def descriptorMethod(
            attr_name: str,
            new_class: type,
        ) -> object:
            """
            Create a descriptor that provides attribute access backed by an instance's interface attributes.

            When accessed on the class, the descriptor returns the field type by delegating to the class's `Interface.getFieldType` for the configured attribute name. When accessed on an instance, it returns the value stored in `instance._attributes[attr_name]`. If the stored value is callable, it is invoked with `instance._interface` and the resulting value is returned. If the attribute is not present on the instance, a `MissingAttributeError` is raised. If invoking a callable attribute raises an exception, that error is wrapped in `AttributeEvaluationError`.

            Parameters:
                attr_name (str): The name of the attribute the descriptor resolves.
                new_class (type): The class that will receive the descriptor; used to access its `Interface`.

            Returns:
                descriptor (object): A descriptor object suitable for assigning as a class attribute.
            """

            class Descriptor:
                def __init__(
                    self, descriptor_attr_name: str, descriptor_class: Type[Any]
                ) -> None:
                    self._attr_name = descriptor_attr_name
                    self._class = descriptor_class

                def __get__(
                    self,
                    instance: Any | None,
                    owner: type | None = None,
                ) -> Any:
                    """
                    Provide the class field type when accessed on the class, or resolve and return the stored attribute value for an instance.

                    When accessed on a class, returns the field type from the class's Interface via Interface.getFieldType.
                    When accessed on an instance, retrieves the value stored in instance._attributes for this descriptor's attribute name;
                    if the stored value is callable, it is invoked with instance._interface and the result is returned.

                    Returns:
                        The field type (when accessed on the class) or the resolved attribute value from the instance.

                    Raises:
                        MissingAttributeError: If the attribute is not present in instance._attributes.
                        AttributeEvaluationError: If calling a callable attribute raises an exception; the original exception is wrapped.
                    """
                    if instance is None:
                        return self._class.Interface.getFieldType(self._attr_name)
                    attribute = instance._attributes.get(self._attr_name, _nonExistent)
                    if attribute is _nonExistent:
                        raise MissingAttributeError(
                            self._attr_name, instance.__class__.__name__
                        )
                    if callable(attribute):
                        try:
                            attribute = attribute(instance._interface)
                        except Exception as e:
                            raise AttributeEvaluationError(self._attr_name, e) from e
                    return attribute

            return Descriptor(attr_name, cast(Type[Any], new_class))

        for attr_name in attributes:
            setattr(new_class, attr_name, descriptorMethod(attr_name, new_class))
