"""Metaclass infrastructure for registering GeneralManager subclasses."""

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
    """Metaclass responsible for wiring GeneralManager interfaces and registries."""
    all_classes: list[Type[GeneralManager]] = []
    read_only_classes: list[Type[GeneralManager]] = []
    pending_graphql_interfaces: list[Type[GeneralManager]] = []
    pending_attribute_initialization: list[Type[GeneralManager]] = []
    Interface: type[InterfaceBase]

    def __new__(mcs, name: str, bases: tuple[type, ...], attrs: dict[str, Any]) -> type:
        """
        Create a new GeneralManager subclass and register its interface hooks.

        Parameters:
            name (str): Name of the class being created.
            bases (tuple[type, ...]): Base classes inherited by the new class.
            attrs (dict[str, Any]): Class namespace supplied during creation.

        Returns:
            type: Newly created class augmented with interface integration.
        """

        def createNewGeneralManagerClass(
            mcs, name: str, bases: tuple[type, ...], attrs: dict[str, Any]
        ) -> Type[GeneralManager]:
            """Helper to instantiate the class via the default ``type.__new__``."""
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
        Attach descriptor-based properties for each attribute declared on the interface.

        Parameters:
            attributes (Iterable[str]): Names of attributes for which descriptors are created.
            new_class (Type[GeneralManager]): Class receiving the generated descriptors.
        """

        def desciptorMethod(attr_name: str, new_class: type):
            """Create a descriptor that resolves attribute values from the interface at runtime."""

            class Descriptor(Generic[GeneralManagerType]):
                def __init__(self, attr_name: str, new_class: Type[GeneralManager]):
                    self.attr_name = attr_name
                    self.new_class = new_class

                def __get__(
                    self,
                    instance: GeneralManagerType | None,
                    owner: type | None = None,
                ):
                    """Return the field type on the class or the stored value on an instance."""
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
