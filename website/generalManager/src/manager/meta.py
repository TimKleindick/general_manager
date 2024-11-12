from __future__ import annotations
from generalManager.src.interface import (
    InterfaceBase,
)
from website.settings import AUTOCREATE_GRAPHQL
from typing import Any, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from generalManager.src.interface import ReadOnlyInterface
    from generalManager.src.manager.generalManager import GeneralManager


class GeneralManagerMeta(type):
    read_only_classes: list[Type[ReadOnlyInterface]] = []
    pending_graphql_interfaces: list[Type[GeneralManager]] = []

    def __new__(mcs, name: str, bases: tuple[type, ...], attrs: dict[str, Any]) -> type:
        if "Interface" in attrs:
            interface = attrs.pop("Interface")
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
