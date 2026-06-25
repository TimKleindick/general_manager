"""Builder that resolves capability manifests into concrete selections."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import suppress
from typing import TYPE_CHECKING

from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities import CapabilityName, CapabilityRegistry
from general_manager.interface.capabilities.factory import build_capabilities
from general_manager.interface.infrastructure.startup_hooks import (
    clear_startup_hooks,
    register_startup_hook,
    registered_startup_hook_entries,
)
from general_manager.interface.infrastructure.system_checks import (
    clear_system_checks,
    register_system_check,
    registered_system_checks,
)

from .capability_manifest import CAPABILITY_MANIFEST, CapabilityManifest
from .capability_models import CapabilityConfig, CapabilitySelection

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.capabilities.base import Capability

__all__ = ["ManifestCapabilityBuilder"]


class ManifestCapabilityBuilder:
    """Resolve and bind manifest-declared capabilities for interface classes.

    The builder resolves a `CapabilityManifest` for one interface class, applies
    optional-capability configuration, creates handler instances in sorted
    capability-name order, binds them to the interface, and mirrors the final
    declaration plus concrete handlers into a `CapabilityRegistry`. It mutates
    the interface class by setting its capability selection, capability-name set,
    and handler mapping. If build fails during resolution, instantiation,
    attachment, or registry publication, the previous interface selection,
    names, and handlers are restored. Registry implementations own rollback for
    any registry-side state they mutate before raising.
    """

    def __init__(
        self,
        *,
        manifest: CapabilityManifest | None = None,
        registry: CapabilityRegistry | None = None,
    ) -> None:
        """Initialize the builder with manifest and registry dependencies.

        Args:
            manifest: Manifest used to resolve interface classes. `None`
                selects the module default `CAPABILITY_MANIFEST`.
            registry: Registry that receives resolved declarations and concrete
                capability instances. `None` creates a fresh
                `CapabilityRegistry` for this builder.
        """
        self._manifest = manifest or CAPABILITY_MANIFEST
        self._registry = registry or CapabilityRegistry()

    @property
    def registry(self) -> CapabilityRegistry:
        """Return the registry receiving declarations and concrete handlers.

        Returns:
            The `CapabilityRegistry` object supplied to the constructor, or the
            fresh registry created by the builder.
        """
        return self._registry

    def build(
        self,
        interface_cls: type[InterfaceBase],
        *,
        config: CapabilityConfig | None = None,
    ) -> CapabilitySelection:
        """Resolve, instantiate, attach, and register capabilities.

        The manifest is resolved for `interface_cls`, required capabilities are
        validated against the config, optional capabilities are activated by
        flags and manual enables, and disabled optional capabilities are removed.
        Capability instances are created from `selection.all` in sorted
        capability-name order using the interface's `capability_overrides`, then
        each handler is bound through `InterfaceBase._bind_capability_handler`.
        After successful binding, the registry replaces the interface's declared
        names and concrete handler tuple.

        Args:
            interface_cls: Interface class to mutate and register.
            config: Optional runtime configuration. `None` uses a default
                `CapabilityConfig` with no enabled, disabled, or flagged
                optional capabilities.

        Returns:
            The immutable selection that was attached to the interface.

        Raises:
            ValueError: If a required capability is disabled, if a flag maps to
                a non-optional capability when enabled, or if a manual enable is
                not declared optional for the interface.
            KeyError: If a selected capability has no default class and no
                override.
            Exception: Exceptions from manifest resolution, config iterables or
                mappings, override lookup/call, capability construction,
                handler teardown/setup, startup-hook/system-check registration,
                or registry publication propagate unchanged. Failures restore
                the interface's prior capability selection, name set, and
                handler mapping. Registry implementations own rollback for
                registry-side state they mutate before raising.
        """
        previous_selection = interface_cls._capability_selection
        previous_capabilities = interface_cls._capabilities
        previous_handlers = dict(interface_cls._capability_handlers)
        previous_registry_binding = self._registry._bindings.get(interface_cls)
        previous_registry_instances = self._registry._instances.get(interface_cls)
        startup_hooks_snapshot = registered_startup_hook_entries()
        system_checks_snapshot = registered_system_checks()
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
        try:
            interface_cls.set_capability_selection(selection)
            capability_instances = self._instantiate_capabilities(
                interface_cls, selection.all
            )
            self._attach_capabilities(interface_cls, capability_instances)
            self._registry.register(interface_cls, selection.all, replace=True)
            self._registry.bind_instances(interface_cls, capability_instances)
        except Exception:
            for name, handler in tuple(interface_cls._capability_handlers.items()):
                if previous_handlers.get(name) is not handler:
                    with suppress(Exception):
                        handler.teardown(interface_cls)
            interface_cls._capability_selection = previous_selection
            interface_cls._capabilities = previous_capabilities
            interface_cls._capability_handlers = previous_handlers
            if previous_registry_binding is None:
                self._registry._bindings.pop(interface_cls, None)
            else:
                self._registry._bindings[interface_cls] = set(previous_registry_binding)
            if previous_registry_instances is None:
                self._registry._instances.pop(interface_cls, None)
            else:
                self._registry._instances[interface_cls] = previous_registry_instances
            clear_startup_hooks()
            for hook_interface, entries in startup_hooks_snapshot.items():
                for entry in entries:
                    register_startup_hook(
                        hook_interface,
                        entry.hook,
                        dependency_resolver=entry.dependency_resolver,
                    )
            clear_system_checks()
            for check_interface, checks in system_checks_snapshot.items():
                for check in checks:
                    register_system_check(check_interface, check)
            raise
        return selection

    def _resolve_optional(
        self,
        flagged_capabilities: Iterable[tuple[str, CapabilityName]],
        optional: set[CapabilityName],
        config: CapabilityConfig,
    ) -> set[CapabilityName]:
        """Resolve which optional capability names should be activated.

        Enabled flags add their mapped capability after verifying that the
        mapping points to an optional capability. Manual enables are then added
        and the combined set must be a subset of `optional`. Disabled optional
        capabilities are removed last, so a name present in both
        `config.enabled` and `config.disabled` is not activated. A non-optional
        manual enable still raises even when the same name is also disabled.

        Args:
            flagged_capabilities: Iterable of `(flag_name, capability_name)`
                pairs from the resolved plan.
            optional: Capability names declared optional for the interface.
            config: Runtime capability configuration.

        Returns:
            Mutable set of activated optional names.

        Raises:
            ValueError: If an enabled flag maps to a non-optional capability or
                any manually enabled name is not optional for the interface.
            Exception: Exceptions from iterating `flagged_capabilities`,
                reading config sets/mappings, or evaluating flags propagate
                unchanged.
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
        """Instantiate selected capabilities in deterministic name order.

        Args:
            interface_cls: Interface class whose `capability_overrides` mapping
                supplies per-name handler classes or zero-argument factories.
            capability_names: Selected capability names.

        Returns:
            Mutable list of capability instances in sorted capability-name order.

        Raises:
            KeyError: If a selected capability has no override and no default
                handler in the capability factory.
            Exception: Exceptions from override mapping access or handler
                construction propagate unchanged.
        """
        ordered_names = sorted(capability_names)
        overrides = getattr(interface_cls, "capability_overrides", {}) or {}
        return build_capabilities(interface_cls, ordered_names, overrides)

    def _attach_capabilities(
        self,
        interface_cls: type[InterfaceBase],
        capabilities: list["Capability"],
    ) -> None:
        """Bind capability instances to an interface in the supplied order.

        Args:
            interface_cls: Interface class to mutate.
            capabilities: Capability instances to bind.

        Raises:
            AttributeError: If a capability lacks a `name` attribute.
            Exception: Exceptions from existing handler teardown, new handler
                setup, startup-hook registration, or system-check registration
                propagate unchanged.
        """
        for capability in capabilities:
            interface_cls._bind_capability_handler(capability)
