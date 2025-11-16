"""Read-only capability re-exports."""

from general_manager.interface.capabilities.core.utils import (
    with_observability as with_observability,
)

from . import management as _management
from .lifecycle import ReadOnlyLifecycleCapability

ReadOnlyManagementCapability = _management.ReadOnlyManagementCapability
django_transaction = _management.django_transaction
logger = _management.logger

__all__ = [
    "ReadOnlyLifecycleCapability",
    "ReadOnlyManagementCapability",
    "django_transaction",
    "logger",
    "with_observability",
]
