from __future__ import annotations

from django.conf import settings
from typing import Any, Type, TYPE_CHECKING, Generic, TypeVar, Iterable
from general_manager.interface.baseInterface import InterfaceBase

if TYPE_CHECKING:
    from general_manager.interface.readOnlyInterface import ReadOnlyInterface
    from general_manager.manager.generalManager import GeneralManager


GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")


class _nonExistent:
    pass


class GeneralManagerMeta(type):
    all_classes: list[Type[GeneralManager]] = []
    read_only_classes: list[Type[GeneralManager[Any, ReadOnlyInterface]]] = []
    pending_graphql_interfaces: list[Type[GeneralManager]] = []
    pending_attribute_initialization: list[Type[GeneralManager]] = []
    Interface: type[InterfaceBase]

    def __new__(mcs, name: str, bases: tuple[type, ...], attrs: dict[str, Any]) -> type:
        """
        Creates a new class using the metaclass, integrating interface hooks and registering the class for attribute initialization and tracking.

        If the class definition includes an `Interface` attribute, validates it as a subclass of `InterfaceBase`, applies pre- and post-creation hooks from the interface, and registers the resulting class for attribute initialization and management. Regardless of interface presence, the new class is tracked for pending GraphQL interface creation.

        Returns:
            The newly created class, potentially augmented with interface integration and registration logic.
        """

        def createNewGeneralManagerClass(
            mcs, name: str, bases: tuple[type, ...], attrs: dict[str, Any]
        ) -> Type[GeneralManager]:
            """
            Create a new GeneralManager class using the standard metaclass instantiation process.

            Returns:
                The newly created GeneralManager subclass.
            """
            return super().__new__(mcs, name, bases, attrs)

        if "Interface" in attrs:
            interface = attrs.pop("Interface")
            if not issubclass(interface, InterfaceBase):
                raise TypeError(
                    f"{interface.__name__} must be a subclass of InterfaceBase"
                )
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
    ):
        """
        Dynamically assigns property descriptors to a class for each specified attribute name.

        For each attribute, creates a descriptor that:
        - Returns the field type from the class's interface when accessed on the class.
        - Retrieves the value from the instance's `_attributes` dictionary when accessed on an instance.
        - Invokes the attribute with the instance's interface if it is callable.
        - Raises `AttributeError` if the attribute is missing or if an error occurs during callable invocation.
        """

        def desciptorMethod(attr_name: str, new_class: type):
            """
            Creates a property descriptor for an attribute, enabling dynamic access and callable resolution.

            When accessed on the class, returns the field type from the associated interface. When accessed on an instance, retrieves the attribute value from the instance's `_attributes` dictionary, invoking it with the instance's interface if the value is callable. Raises `AttributeError` if the attribute is missing or if a callable attribute raises an exception.
            """

            class Descriptor(Generic[GeneralManagerType]):
                def __init__(self, attr_name: str, new_class: Type[GeneralManager]):
                    self.attr_name = attr_name
                    self.new_class = new_class

                def __get__(
                    self,
                    instance: GeneralManager[GeneralManagerType, InterfaceBase] | None,
                    owner: type | None = None,
                ):
                    if instance is None:
                        return self.new_class.Interface.getFieldType(self.attr_name)
                    attribute = instance._attributes.get(attr_name, _nonExistent)
                    if attribute is _nonExistent:
                        raise AttributeError(
                            f"{self.attr_name} not found in {instance.__class__.__name__}"
                        )
                    if callable(attribute):
                        try:
                            attribute = attribute(instance._interface)
                        except Exception as e:
                            raise AttributeError(
                                f"Error calling attribute {self.attr_name}: {e}"
                            ) from e
                    return attribute

            return Descriptor(attr_name, new_class)

        for attr_name in attributes:
            setattr(new_class, attr_name, desciptorMethod(attr_name, new_class))
