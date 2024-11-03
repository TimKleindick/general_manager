from __future__ import annotations
from generalManager.src.interface import (
    InterfaceBase,
)
from website.settings import AUTOCREATE_GRAPHQL
from typing import Type


class GeneralManagerMeta(type):
    read_only_classes: list[Type] = []
    pending_graphql_interfaces: list[Type] = []

    def __new__(mcs, name, bases, attrs):
        if "Interface" in attrs:
            interface: Type[InterfaceBase] = attrs.pop("Interface")
            if not issubclass(interface, InterfaceBase):
                raise TypeError(
                    f"Interface must be a subclass of {InterfaceBase.__name__}"
                )
            preCreation, postCreation = interface.handleInterface()
            attrs, interface_cls, model = preCreation(name, attrs, interface)
            new_class = super().__new__(mcs, name, bases, attrs)
            postCreation(mcs, new_class, interface_cls, model)

        else:
            new_class = super().__new__(mcs, name, bases, attrs)

        if AUTOCREATE_GRAPHQL:
            mcs.pending_graphql_interfaces.append(new_class)

        return new_class
