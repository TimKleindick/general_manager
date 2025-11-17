"""Declarative manifest mapping interfaces to their capability plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Iterable, Mapping as TypingMapping

from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.interfaces.calculation import (
    CalculationInterface,
)
from general_manager.interface.orm_interface import (
    OrmInterfaceBase,
)
from general_manager.interface.interfaces.database import (
    DatabaseInterface,
)
from general_manager.interface.interfaces.existing_model import (
    ExistingModelInterface,
)
from general_manager.interface.interfaces.read_only import (
    ReadOnlyInterface,
)
from general_manager.interface.capabilities import CapabilityName

from .capability_models import CapabilityPlan


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    """Resolver that folds interface inheritance hierarchies into a single plan."""

    plans: Mapping[type, CapabilityPlan]

    def resolve(self, interface_cls: type[InterfaceBase]) -> CapabilityPlan:
        """
        Aggregate capability requirements for an interface by folding plans from its class hierarchy.

        Parameters:
            interface_cls (type[InterfaceBase]): Interface class whose MRO is traversed from base to derived to collect matching plans.

        Returns:
            CapabilityPlan: Consolidated plan where `required` and `optional` are frozensets of capability names and `flags` is the merged flag-to-capability mapping.
        """
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
        """
        Check whether a concrete capability plan is registered for the given interface class.

        Returns:
            `true` if a plan is present for the interface class, `false` otherwise.
        """
        return interface_cls in self.plans


DEFAULT_FLAG_MAPPING: dict[str, CapabilityName] = {
    "notifications": "notification",
    "scheduling": "scheduling",
    "access_control": "access_control",
    "observability": "observability",
}


def names(*values: CapabilityName) -> tuple[CapabilityName, ...]:
    """
    Collects the provided CapabilityName literals into a tuple.

    Parameters:
        values: One or more CapabilityName literals to include in the result.

    Returns:
        Tuple of the provided CapabilityName values.
    """
    return values


def _plan(
    *,
    required: Iterable[CapabilityName],
    optional: Iterable[CapabilityName] = (),
    flags: TypingMapping[str, CapabilityName] | None = None,
) -> CapabilityPlan:
    """
    Constructs a CapabilityPlan from the given required, optional, and flag capability names.

    Parameters:
        required (Iterable[CapabilityName]): Capability names that are required.
        optional (Iterable[CapabilityName], optional): Capability names that are optional. Defaults to empty.
        flags (Mapping[str, CapabilityName] | None, optional): Mapping of flag identifiers to capability names. Defaults to None.

    Returns:
        CapabilityPlan: A plan containing the provided required and optional capabilities and the flags mapping.
    """
    return CapabilityPlan(
        required=frozenset(required),
        optional=frozenset(optional),
        flags=flags or {},
    )


CAPABILITY_MANIFEST = CapabilityManifest(
    plans={
        InterfaceBase: _plan(required=()),
        OrmInterfaceBase: _plan(
            required=names(
                "orm_support",
                "orm_lifecycle",
                "soft_delete",
                "read",
                "validation",
                "query",
                "observability",
            ),
            optional=names("notification", "scheduling", "access_control"),
            flags=DEFAULT_FLAG_MAPPING,
        ),
        DatabaseInterface: _plan(
            required=names(
                "orm_mutation",
                "create",
                "update",
                "delete",
                "history",
            ),
            optional=names(
                "notification", "scheduling", "access_control", "observability"
            ),
            flags=DEFAULT_FLAG_MAPPING,
        ),
        ExistingModelInterface: _plan(
            required=names(
                "orm_mutation",
                "create",
                "update",
                "delete",
                "history",
                "existing_model_resolution",
            ),
            optional=names(
                "notification", "scheduling", "access_control", "observability"
            ),
            flags=DEFAULT_FLAG_MAPPING,
        ),
        ReadOnlyInterface: _plan(
            required=names("read_only_management"),
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
