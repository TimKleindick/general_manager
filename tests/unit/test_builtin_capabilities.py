"""Unit tests for built-in capability implementations."""

from __future__ import annotations

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
