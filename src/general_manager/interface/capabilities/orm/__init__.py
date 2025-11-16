"""ORM-backed capability implementations."""

from __future__ import annotations

from general_manager.interface.capabilities.core.utils import (
    with_observability as with_observability,
)
from simple_history.utils import update_change_reason as update_change_reason

from .history import OrmHistoryCapability
from .lifecycle import OrmLifecycleCapability
from .mutations import (
    OrmCreateCapability,
    OrmDeleteCapability,
    OrmMutationCapability,
    OrmUpdateCapability,
    OrmValidationCapability,
)
from .support import (
    OrmPersistenceSupportCapability,
    OrmQueryCapability,
    OrmReadCapability,
    SoftDeleteCapability,
    get_support_capability,
    is_soft_delete_enabled,
)

__all__ = [
    "OrmCreateCapability",
    "OrmDeleteCapability",
    "OrmHistoryCapability",
    "OrmLifecycleCapability",
    "OrmMutationCapability",
    "OrmPersistenceSupportCapability",
    "OrmQueryCapability",
    "OrmReadCapability",
    "OrmUpdateCapability",
    "OrmValidationCapability",
    "SoftDeleteCapability",
    "get_support_capability",
    "is_soft_delete_enabled",
    "update_change_reason",
    "with_observability",
]
