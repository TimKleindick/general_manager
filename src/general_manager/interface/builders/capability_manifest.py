"""Declarative manifest mapping interfaces to their capability plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Iterable, Mapping as TypingMapping

from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.backends.calculation.calculation_interface import (
    CalculationInterface,
)
from general_manager.interface.backends.database.database_based_interface import (
    OrmPersistenceInterface,
    OrmWritableInterface,
)
from general_manager.interface.backends.database.database_interface import (
    DatabaseInterface,
)
from general_manager.interface.backends.existing_model.existing_model_interface import (
    ExistingModelInterface,
)
from general_manager.interface.backends.read_only.read_only_interface import (
    ReadOnlyInterface,
)
from general_manager.interface.capabilities import CapabilityName

from .capability_models import CapabilityPlan


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    """Resolver that folds interface inheritance hierarchies into a single plan."""

    plans: Mapping[type, CapabilityPlan]

    def resolve(self, interface_cls: type[InterfaceBase]) -> CapabilityPlan:
        """Aggregate plans along the class hierarchy."""
        required: set[CapabilityName] = set()
        optional: set[CapabilityName] = set()
        flags: dict[str, CapabilityName] = {}
        for cls in reversed(interface_cls.__mro__):
            plan = self.plans.get(cls)  # type: ignore[arg-type]
            if plan is None:
                continue
            required.update(plan.required)
            optional.update(plan.optional)
            flags.update(plan.flags)
        return CapabilityPlan(
            required=frozenset(required),
            optional=frozenset(optional),
            flags=flags,
        )

    def __contains__(self, interface_cls: type[InterfaceBase]) -> bool:
        """Return True when a concrete plan is stored for the interface."""
        return interface_cls in self.plans


DEFAULT_FLAG_MAPPING: dict[str, CapabilityName] = {
    "notifications": "notification",
    "scheduling": "scheduling",
    "access_control": "access_control",
    "observability": "observability",
}


def names(*values: CapabilityName) -> tuple[CapabilityName, ...]:
    """Helper ensuring CapabilityName literals are type-checked."""
    return values


def _plan(
    *,
    required: Iterable[CapabilityName],
    optional: Iterable[CapabilityName] = (),
    flags: TypingMapping[str, CapabilityName] | None = None,
) -> CapabilityPlan:
    return CapabilityPlan(
        required=frozenset(required),
        optional=frozenset(optional),
        flags=flags or {},
    )


CAPABILITY_MANIFEST = CapabilityManifest(
    plans={
        InterfaceBase: _plan(required=()),
        OrmPersistenceInterface: _plan(
            required=names(
                "orm_support",
                "orm_lifecycle",
                "read",
                "validation",
                "query",
                "observability",
            ),
            optional=names("notification", "scheduling", "access_control"),
            flags=DEFAULT_FLAG_MAPPING,
        ),
        OrmWritableInterface: _plan(
            required=names(
                "orm_support",
                "orm_mutation",
                "orm_lifecycle",
                "create",
                "update",
                "delete",
                "history",
                "query",
                "observability",
            ),
            optional=names("notification", "scheduling", "access_control"),
            flags=DEFAULT_FLAG_MAPPING,
        ),
        DatabaseInterface: _plan(
            required=names(),
            optional=names(
                "notification", "scheduling", "access_control", "observability"
            ),
            flags=DEFAULT_FLAG_MAPPING,
        ),
        ExistingModelInterface: _plan(
            required=names("existing_model_resolution"),
            optional=names(
                "notification", "scheduling", "access_control", "observability"
            ),
            flags=DEFAULT_FLAG_MAPPING,
        ),
        ReadOnlyInterface: _plan(
            required=names(
                "read", "validation", "observability", "query", "read_only_management"
            ),
            optional=names("notification", "access_control"),
            flags={"notifications": "notification", "access_control": "access_control"},
        ),
        CalculationInterface: _plan(
            required=names(
                "read", "validation", "observability", "query", "calculation_lifecycle"
            ),
            optional=names("notification", "scheduling", "access_control"),
            flags={
                "notifications": "notification",
                "scheduling": "scheduling",
                "access_control": "access_control",
            },
        ),
    }
)
