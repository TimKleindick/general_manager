"""Metaclass infrastructure for registering GeneralManager subclasses."""

from __future__ import annotations

from django.conf import settings
from typing import Any, Type, TYPE_CHECKING, ClassVar, TypeVar, Iterable, cast
from general_manager.interface.baseInterface import InterfaceBase

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager


GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")


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
        Create a new GeneralManager subclass and register its interface hooks.

        Parameters:
            name (str): Name of the class being created.
            bases (tuple[type, ...]): Base classes inherited by the new class.
            attrs (dict[str, Any]): Class namespace supplied during creation.

        Returns:
            type: Newly created class augmented with interface integration.
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
    ) -> None:
        """
        Attach descriptor-based properties for each attribute declared on the interface.

        Parameters:
            attributes (Iterable[str]): Names of attributes for which descriptors are created.
            new_class (Type[GeneralManager]): Class receiving the generated descriptors.
        """

        def descriptorMethod(
            attr_name: str,
            new_class: type,
        ) -> object:
            """Create a descriptor that resolves attribute values from the interface at runtime."""

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
                    """Return the field type on the class or the stored value on an instance."""
                    if instance is None:
                        return self._class.Interface.getFieldType(self._attr_name)
                    attribute = instance._attributes.get(self._attr_name, _nonExistent)
                    if attribute is _nonExistent:
                        raise AttributeError(
                            f"{self._attr_name} not found in {instance.__class__.__name__}"
                        )
                    if callable(attribute):
                        try:
                            attribute = attribute(instance._interface)
                        except Exception as e:
                            raise AttributeError(
                                f"Error calling attribute {self._attr_name}: {e}"
                            ) from e
                    return attribute

            return Descriptor(attr_name, cast(Type[Any], new_class))

        for attr_name in attributes:
            setattr(new_class, attr_name, descriptorMethod(attr_name, new_class))
