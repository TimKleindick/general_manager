"""Builder that resolves capability manifests into concrete selections."""

from __future__ import annotations

from typing import Iterable, TYPE_CHECKING

from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities import CapabilityName, CapabilityRegistry
from general_manager.interface.capabilities.factory import build_capabilities

from .capability_manifest import CAPABILITY_MANIFEST, CapabilityManifest
from .capability_models import CapabilityConfig, CapabilitySelection

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.capabilities.base import Capability


class ManifestCapabilityBuilder:
    """Resolve capabilities for an interface using the declarative manifest."""

    def __init__(
        self,
        *,
        manifest: CapabilityManifest | None = None,
        registry: CapabilityRegistry | None = None,
    ) -> None:
        self._manifest = manifest or CAPABILITY_MANIFEST
        self._registry = registry or CapabilityRegistry()

    @property
    def registry(self) -> CapabilityRegistry:
        """Expose the registry storing resolved capabilities."""
        return self._registry

    def build(
        self,
        interface_cls: type[InterfaceBase],
        *,
        config: CapabilityConfig | None = None,
    ) -> CapabilitySelection:
        """
        Resolve the interface capability plan, apply configuration toggles, and register the result.
        """
        plan = self._manifest.resolve(interface_cls)
        resolved_config = config or CapabilityConfig()
        required = set(plan.required)
        disallowed_required = resolved_config.disabled.intersection(required)
        if disallowed_required:
            message = (
                "Required capabilities cannot be disabled: "
                f"{sorted(disallowed_required)}"
            )
            raise ValueError(message)
        optional = set(plan.optional)
        activated = self._resolve_optional(
            plan.flags.items(), optional, resolved_config
        )
        selection = CapabilitySelection(
            required=frozenset(required),
            optional=frozenset(optional),
            activated_optional=frozenset(activated),
        )
        capability_instances = self._instantiate_capabilities(
            interface_cls, selection.all
        )
        self._attach_capabilities(interface_cls, capability_instances)
        self._registry.register(interface_cls, selection.all, replace=True)
        self._registry.bind_instances(interface_cls, capability_instances)
        return selection

    def _resolve_optional(
        self,
        flagged_capabilities: Iterable[tuple[str, CapabilityName]],
        optional: set[CapabilityName],
        config: CapabilityConfig,
    ) -> set[CapabilityName]:
        activated: set[CapabilityName] = set()

        # Flag-driven toggles
        for flag_name, capability in flagged_capabilities:
            if config.is_flag_enabled(flag_name):
                if capability not in optional:
                    message = (
                        f"Capability '{capability}' referenced by flag '{flag_name}' "
                        "must be declared optional."
                    )
                    raise ValueError(message)
                activated.add(capability)

        # Manual overrides
        activated.update(config.enabled)

        disallowed = activated - optional
        if disallowed:
            message = f"Capabilities {sorted(disallowed)} are not optional for this interface."
            raise ValueError(message)

        # Disable explicit opt-outs
        activated.difference_update(config.disabled)

        return activated

    def _instantiate_capabilities(
        self,
        interface_cls: type[InterfaceBase],
        capability_names: frozenset[CapabilityName],
    ) -> list["Capability"]:
        ordered_names = sorted(capability_names)
        overrides = getattr(interface_cls, "capability_overrides", {}) or {}
        return build_capabilities(interface_cls, ordered_names, overrides)

    def _attach_capabilities(
        self,
        interface_cls: type[InterfaceBase],
        capabilities: list["Capability"],
    ) -> None:
        for capability in capabilities:
            capability.setup(interface_cls)
