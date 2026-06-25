"""Unit tests for built-in capability implementations."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from typing import Callable, TypeAlias

import pytest

from general_manager.interface.capabilities.builtin import (
    AccessControlCapability,
    BaseCapability,
    CreateCapability,
    DeleteCapability,
    HistoryCapability,
    NotificationCapability,
    ObservabilityCapability,
    ReadCapability,
    SchedulingCapability,
    UpdateCapability,
    ValidationCapability,
)
from general_manager.interface.capabilities.exceptions import CapabilityBindingError

InterfaceFactory: TypeAlias = Callable[[], type]


def _build_interface(required_attrs: Iterable[str] = ()) -> type:
    """Return a fresh interface type exposing the supplied attributes."""

    attrs: dict[str, object] = {"_capability_handlers": {}}
    for name in required_attrs:
        attrs[name] = lambda *_args, **_kwargs: None
    return type("TemporaryInterface", (), attrs)


class MultiRequirementCapability(BaseCapability):
    """Capability used to verify missing-attribute error ordering."""

    name = "validation"
    required_attributes = ("zeta", "alpha")


@pytest.mark.parametrize(
    ("capability_factory", "required_attrs"),
    [
        (ReadCapability, ("get_data",)),
        (CreateCapability, ("create",)),
        (UpdateCapability, ("update",)),
        (DeleteCapability, ("delete",)),
        (HistoryCapability, ("get_attribute_types",)),
        (ValidationCapability, ("get_attribute_types",)),
    ],
)
def test_capability_registers_when_requirements_met(
    capability_factory: type[BaseCapability], required_attrs: tuple[str, ...]
) -> None:
    """Each capability should register itself when the interface defines required attrs."""
    capability = capability_factory()
    interface_cls = _build_interface(required_attrs)

    capability.setup(interface_cls)

    assert capability.name in interface_cls._capability_handlers
    assert interface_cls._capability_handlers[capability.name] is capability

    capability.teardown(interface_cls)
    assert capability.name not in interface_cls._capability_handlers


@pytest.mark.parametrize(
    "capability_factory",
    [
        ReadCapability,
        CreateCapability,
        UpdateCapability,
        DeleteCapability,
        HistoryCapability,
        ValidationCapability,
    ],
)
def test_capability_setup_raises_when_missing_required_attrs(
    capability_factory: type[BaseCapability],
) -> None:
    """Capabilites enforcing required attributes should raise when they are absent."""
    capability = capability_factory()
    interface_cls = _build_interface(())

    with pytest.raises(CapabilityBindingError):
        capability.setup(interface_cls)


def test_capability_setup_lists_missing_required_attrs_in_sorted_order() -> None:
    """Missing-attribute errors should be deterministic for users and tests."""
    capability = MultiRequirementCapability()
    interface_cls = _build_interface(())

    with pytest.raises(
        CapabilityBindingError,
        match=r"missing required attributes: alpha, zeta",
    ):
        capability.setup(interface_cls)


def test_capability_setup_copies_existing_handler_registry() -> None:
    """Setup should avoid mutating an inherited handler mapping in place."""
    inherited_handlers = {"read": ReadCapability()}
    interface_cls = type(
        "TemporaryInterface",
        (),
        {
            "_capability_handlers": inherited_handlers,
            "get_data": lambda *_args, **_kwargs: None,
            "create": lambda *_args, **_kwargs: None,
        },
    )
    capability = CreateCapability()

    capability.setup(interface_cls)

    assert interface_cls._capability_handlers is not inherited_handlers
    assert interface_cls._capability_handlers["read"] is inherited_handlers["read"]
    assert interface_cls._capability_handlers["create"] is capability
    assert set(inherited_handlers) == {"read"}


def test_capability_setup_converts_custom_registry_to_plain_dict() -> None:
    """Setup should normalize custom mapping subclasses to a plain dict."""
    inherited_handlers = OrderedDict([("read", ReadCapability())])
    interface_cls = type(
        "TemporaryInterface",
        (),
        {
            "_capability_handlers": inherited_handlers,
            "create": lambda *_args, **_kwargs: None,
        },
    )

    CreateCapability().setup(interface_cls)

    assert type(interface_cls._capability_handlers) is dict
    assert list(interface_cls._capability_handlers) == ["read", "create"]


def test_capability_teardown_copies_existing_handler_registry() -> None:
    """Teardown should remove only this capability from a copied registry."""
    read_capability = ReadCapability()
    create_capability = CreateCapability()
    inherited_handlers = {
        "read": read_capability,
        "create": create_capability,
    }
    interface_cls = type(
        "TemporaryInterface",
        (),
        {"_capability_handlers": inherited_handlers},
    )

    create_capability.teardown(interface_cls)

    assert interface_cls._capability_handlers is not inherited_handlers
    assert interface_cls._capability_handlers == {"read": read_capability}
    assert set(inherited_handlers) == {"read", "create"}


def test_capability_teardown_converts_custom_registry_to_plain_dict() -> None:
    """Teardown should normalize custom mapping subclasses to a plain dict."""
    read_capability = ReadCapability()
    inherited_handlers = OrderedDict(
        [
            ("read", read_capability),
            ("create", CreateCapability()),
        ]
    )
    interface_cls = type(
        "TemporaryInterface",
        (),
        {"_capability_handlers": inherited_handlers},
    )

    read_capability.teardown(interface_cls)

    assert type(interface_cls._capability_handlers) is dict
    assert interface_cls._capability_handlers == {
        "create": inherited_handlers["create"]
    }


@pytest.mark.parametrize(
    "capability_factory",
    [
        NotificationCapability,
        SchedulingCapability,
        AccessControlCapability,
        ObservabilityCapability,
    ],
)
def test_optional_capabilities_register_without_requirements(
    capability_factory: type[BaseCapability],
) -> None:
    """Capabilities without required attrs should always register successfully."""
    capability = capability_factory()
    interface_cls = _build_interface(())

    capability.setup(interface_cls)
    assert capability.name in interface_cls._capability_handlers

    capability.teardown(interface_cls)
    assert capability.name not in interface_cls._capability_handlers
