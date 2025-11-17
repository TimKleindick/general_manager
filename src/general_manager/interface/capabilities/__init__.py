"""Capability package exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["Capability", "CapabilityName", "CapabilityRegistry"]

if TYPE_CHECKING:  # pragma: no cover
    from .base import Capability, CapabilityName
    from .registry import CapabilityRegistry


def __getattr__(name: str) -> object:
    """
    Lazily resolve and return a named public attribute from the capabilities package.

    When `name` is one of the exported identifiers ("Capability", "CapabilityName", "CapabilityRegistry"),
    the corresponding object is imported from its submodule and returned. For any other `name`, an
    AttributeError is raised.

    Parameters:
        name (str): The attribute name being accessed on the module.

    Returns:
        object: The resolved attribute object corresponding to `name`.

    Raises:
        AttributeError: If `name` is not a known exported attribute.
    """
    if name == "Capability":
        from .base import Capability

        return Capability
    if name == "CapabilityName":
        from .base import CapabilityName

        return CapabilityName
    if name == "CapabilityRegistry":
        from .registry import CapabilityRegistry

        return CapabilityRegistry
    raise AttributeError(name)
