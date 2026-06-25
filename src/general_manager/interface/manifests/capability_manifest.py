"""Declarative manifest mapping interfaces to their capability plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Iterable

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
from general_manager.interface.interfaces.request import (
    RequestInterface,
)
from general_manager.interface.capabilities import CapabilityName

from .capability_models import CapabilityPlan


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    """Resolve interface capability plans across an interface class hierarchy.

    ``CapabilityManifest`` is a maintainer-facing manifest helper used by the
    capability builder. It stores exact interface-class plans, then resolves a
    concrete interface by walking its MRO from base class to derived class.
    Required and optional capabilities are stored as frozensets, so duplicate
    capability names collapse and no ordering is preserved. A capability may
    appear in both required and optional sets if contributing plans declare it
    both ways; the manifest does not reconcile that conflict. Later flag
    mappings override earlier mappings with the same flag name.

    The supplied ``plans`` mapping is stored by reference. The dataclass is
    frozen, but callers that pass a mutable mapping can still mutate that mapping
    externally and affect future resolution. Runtime non-class values or classes
    outside the ``InterfaceBase`` family are outside the documented contract and
    fail through ordinary Python attribute or membership errors.
    """

    plans: Mapping[type[InterfaceBase], CapabilityPlan]

    def resolve(self, interface_cls: type[InterfaceBase]) -> CapabilityPlan:
        """Aggregate capability requirements for an interface class.

        Args:
            interface_cls: Interface class whose MRO is traversed from base to
                derived. Only classes present in ``plans`` contribute a plan.

        Returns:
            Consolidated capability plan allocated for this call. Its
            ``required`` and ``optional`` values are frozenset unions of all
            matching plans. Its ``flags`` mapping is copied into a new immutable
            ``CapabilityPlan`` mapping after derived-class plans override
            base-class plans for duplicate flag names.
        """
        required: set[CapabilityName] = set()
        optional: set[CapabilityName] = set()
        flags: dict[str, CapabilityName] = {}
        for cls in reversed(interface_cls.__mro__):
            plan = self.plans.get(cls)
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
        """Return whether an exact interface class has a registered plan.

        Returns:
            ``True`` when ``interface_cls`` appears directly in ``plans``.
            Inherited plans do not make a derived class containable unless the
            derived class itself is registered.
        """
        return interface_cls in self.plans


DEFAULT_FLAG_MAPPING: dict[str, CapabilityName] = {
    "notifications": "notification",
    "scheduling": "scheduling",
    "access_control": "access_control",
    "observability": "observability",
}


def names(*values: CapabilityName) -> tuple[CapabilityName, ...]:
    """Collect capability names into a typed tuple for manifest declarations.

    Args:
        values: Capability names to include.

    Returns:
        The supplied capability names in the same order.
    """
    return values


def _plan(
    *,
    required: Iterable[CapabilityName],
    optional: Iterable[CapabilityName] = (),
    flags: Mapping[str, CapabilityName] | None = None,
) -> CapabilityPlan:
    """Construct an immutable capability plan for a manifest entry.

    Args:
        required: Capability names that must always be attached.
        optional: Capability names that may be enabled by configuration.
        flags: Mapping from configuration flag names to optional capability
            names. ``None`` means no flag mappings.

    Returns:
        Capability plan with frozen required/optional sets and immutable flags.
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
        RequestInterface: _plan(
            required=names(
                "read",
                "validation",
                "observability",
                "query",
                "request_lifecycle",
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
