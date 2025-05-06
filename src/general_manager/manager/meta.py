from __future__ import annotations
from general_manager.interface.baseInterface import (
    InterfaceBase,
)
from django.conf import settings
from typing import Any, Type, TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from general_manager.interface.databaseInterface import ReadOnlyInterface
    from general_manager.manager.generalManager import GeneralManager

GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")


class GeneralManagerMeta(type):
    all_classes: list[Type[GeneralManager]] = []
    read_only_classes: list[Type[ReadOnlyInterface]] = []
    pending_graphql_interfaces: list[Type[GeneralManager]] = []
    pending_attribute_initialization: list[Type[GeneralManager]] = []
    Interface: type[InterfaceBase]

    def __new__(mcs, name: str, bases: tuple[type, ...], attrs: dict[str, Any]) -> type:
        def createNewGeneralManagerClass(
            mcs, name: str, bases: tuple[type, ...], attrs: dict[str, Any]
        ) -> Type[GeneralManager]:
            return super().__new__(mcs, name, bases, attrs)

        if "Interface" in attrs:
            interface = attrs.pop("Interface")
            if not issubclass(interface, InterfaceBase):
                raise TypeError(
                    f"Interface must be a subclass of {InterfaceBase.__name__}"
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
        attributes: dict[str, Any], new_class: Type[GeneralManager]
    ):

        def desciptorMethod(attr_name: str, new_class: type):
            class Descriptor(Generic[GeneralManagerType]):
                def __init__(self, attr_name: str, new_class: Type[GeneralManager]):
                    self.attr_name = attr_name
                    self.new_class = new_class

                def __get__(
                    self,
                    instance: GeneralManager[GeneralManagerType] | None,
                    owner: type | None = None,
                ):
                    if instance is None:
                        return self.new_class.Interface.getFieldType(self.attr_name)
                    attribute = instance._attributes[attr_name]
                    if callable(attribute):
                        return attribute(instance._interface)
                    return attribute

            return Descriptor(attr_name, new_class)

        for attr_name in attributes.keys():
            setattr(new_class, attr_name, desciptorMethod(attr_name, new_class))
