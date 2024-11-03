from __future__ import annotations
from generalManager.src.manager.interface import (
    DBBasedInterface,
    ReadOnlyInterface,
    GeneralManagerModel,
    InterfaceBase,
)
from website.settings import AUTOCREATE_GRAPHQL
from typing import Type


class GeneralManagerMeta(type):
    read_only_classes: list[Type] = []
    pending_graphql_interfaces: list[Type] = []

    def __new__(mcs, name, bases, attrs):
        from generalManager.src.api.graphql import GraphQL

        if "Interface" in attrs:
            interface: InterfaceBase = attrs.pop("Interface")
            preCreation, postCreation = interface.handleInterface()
            attrs, interface_cls, model = preCreation(name, attrs, interface)
            new_class = super().__new__(mcs, name, bases, attrs)
            postCreation(mcs, new_class, interface_cls, model)

        else:
            new_class = super().__new__(mcs, name, bases, attrs)

        if AUTOCREATE_GRAPHQL:
            mcs.pending_graphql_interfaces.append(new_class)

        return new_class
