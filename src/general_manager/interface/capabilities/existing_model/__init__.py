"""Capability set for existing Django models."""

from general_manager.interface.capabilities.core.utils import (
    with_observability as with_observability,
)

from .resolution import ExistingModelResolutionCapability

__all__ = ["ExistingModelResolutionCapability", "with_observability"]
