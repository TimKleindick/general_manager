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
        """
        Initialize the ManifestCapabilityBuilder with an optional capability manifest and registry.

        Parameters:
            manifest (CapabilityManifest | None): The capability manifest to resolve interfaces from. If omitted, uses the module default `CAPABILITY_MANIFEST`.
            registry (CapabilityRegistry | None): The registry used to register resolved capability selections and instances. If omitted, a new `CapabilityRegistry` is created.
        """
        self._manifest = manifest or CAPABILITY_MANIFEST
        self._registry = registry or CapabilityRegistry()

    @property
    def registry(self) -> CapabilityRegistry:
        """
        Access the capability registry used by this builder.

        Returns:
            CapabilityRegistry: The registry containing resolved capability selections and registered capability instances.
        """
        return self._registry

    def build(
        self,
        interface_cls: type[InterfaceBase],
        *,
        config: CapabilityConfig | None = None,
    ) -> CapabilitySelection:
        """
        Resolve and apply capability selections for an interface class.

        Builds a CapabilitySelection for the given interface by resolving the manifest with an optional CapabilityConfig, validates that no required capability is disabled, determines activated optional capabilities, instantiates and attaches capability instances to the interface, and registers the resulting capability set in the registry.

        Parameters:
            interface_cls (type[InterfaceBase]): The interface class whose capabilities should be resolved and attached.
            config (CapabilityConfig | None): Optional configuration that can enable/disable capabilities and toggle flag-driven capabilities. If omitted, a default CapabilityConfig is used.

        Returns:
            CapabilitySelection: A selection containing the sets of required, optional, and activated optional capabilities.

        Raises:
            ValueError: If any capability declared required is disabled in the provided config.
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
        interface_cls.set_capability_selection(selection)
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
        """
        Resolve which optional capabilities should be activated for an interface.

        Determines the final set of activated optional capabilities by:
        - enabling capabilities whose feature flags are set in `config`,
        - applying manual enables from `config.enabled`,
        - validating that any enabled capability is declared optional,
        - removing any capabilities listed in `config.disabled`.

        Parameters:
            flagged_capabilities (Iterable[tuple[str, CapabilityName]]): Pairs of (flag_name, capability)
                where the capability should be activated if the corresponding flag is enabled in `config`.
            optional (set[CapabilityName]): Capability names declared optional for the interface.
            config (CapabilityConfig): Configuration that exposes enabled/disabled sets and
                a method `is_flag_enabled(flag_name: str)` to query feature flags.

        Returns:
            set[CapabilityName]: The final set of activated optional capability names.

        Raises:
            ValueError: If a flag refers to a capability that is not declared optional, or if
                any manually enabled capability is not declared optional.
        """
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
        """
        Instantiate capability objects for an interface in a deterministic order.

        Parameters:
            interface_cls (type[InterfaceBase]): Interface class for which capabilities will be built; any
                `capability_overrides` attribute on this class will be applied.
            capability_names (frozenset[CapabilityName]): Set of capability names to instantiate.

        Returns:
            list[Capability]: Capability instances corresponding to the given names, built in sorted order
            and created with the interface's overrides applied.
        """
        ordered_names = sorted(capability_names)
        overrides = getattr(interface_cls, "capability_overrides", {}) or {}
        return build_capabilities(interface_cls, ordered_names, overrides)

    def _attach_capabilities(
        self,
        interface_cls: type[InterfaceBase],
        capabilities: list["Capability"],
    ) -> None:
        """
        Attach capability instances to an interface class by binding each capability to the interface.

        Parameters:
            interface_cls (type[InterfaceBase]): The interface class to attach capabilities to.
            capabilities (list[Capability]): Capability instances to attach; each will be bound to the interface in order.
        """
        for capability in capabilities:
            interface_cls._bind_capability_handler(capability)
