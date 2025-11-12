"""Capability package exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["Capability", "CapabilityName", "CapabilityRegistry"]

if TYPE_CHECKING:  # pragma: no cover
    from .base import Capability, CapabilityName
    from .registry import CapabilityRegistry


def __getattr__(name: str) -> object:
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
