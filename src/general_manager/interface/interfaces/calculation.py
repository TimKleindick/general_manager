"""Interface implementation for calculation-style GeneralManager classes."""

from __future__ import annotations

from typing import ClassVar, Literal

from general_manager.interface.base_interface import InterfaceBase
from general_manager.manager.input import Input
from general_manager.interface.bundles.calculation import CALCULATION_CORE_CAPABILITIES
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.configuration import CapabilityConfigEntry


class CalculationInterface(InterfaceBase):
    """Interface shell for derived managers that expose typed inputs but no storage.

    Subclasses normally declare input descriptors as class attributes
    (``project = Input(Project)``). During manager-class creation the calculation
    lifecycle capability scans the concrete interface class's own attributes,
    skips dunder names, collects values that are `Input` instances into a fresh
    ``input_fields`` dictionary, and assigns that dictionary to the generated
    interface subclass. Inherited descriptors and any manually assigned
    ``input_fields`` mapping are not merged by that lifecycle step. The generated
    interface receives a ``_parent_class`` backlink to the manager during
    post-create.

    Resolved values are cached per interface instance in
    ``_resolved_input_values``. The cache stores the cast result of each input,
    including manager wrappers for manager-typed inputs, and lives for the
    lifetime of that interface instance. Manager-typed inputs cache the resolved
    ``GeneralManager`` wrapper object returned by ``Input.cast()``. There is no
    cross-instance, async, or thread-level invalidation contract for this cache.
    Query methods ``all()``, ``filter()``, and ``exclude()`` are provided by the
    configured calculation query capability and return ``CalculationBucket``
    instances. The public ``get_data()`` method is backed by the calculation read
    capability and raises ``NotImplementedError("Calculations do not store data.")``.
    Managers still inherit ``create()``, ``update()``, and ``delete()`` from
    ``GeneralManager``, but calculation interfaces configure no create, update,
    or delete capability, so those inherited mutation paths are unsupported and
    fail when they require the missing capability.
    """

    _interface_type: ClassVar[str] = "calculation"
    as_of_policy: ClassVar[Literal["transparent"]] = "transparent"
    input_fields: ClassVar[dict[str, Input[type[object]]]]
    _resolved_input_values: dict[str, object]

    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = (
        CALCULATION_CORE_CAPABILITIES,
    )
    lifecycle_capability_name: ClassVar[CapabilityName | None] = "calculation_lifecycle"
