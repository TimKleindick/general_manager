# ADR 0003: Configurable Interface Capabilities via Django Settings

- Status: Proposed
- Date: 2025-11-12

## Context

We recently moved every interface to declare its behavior via `configured_capabilities`
(bundles of capability configs). That keeps the interface classes tiny, but it also
means the capability set is effectively "baked in" unless a developer edits the class.
Teams would like to be able to:

1. **Enable optional capabilities** (e.g., notifications, scheduling) without touching code.
2. **Disable optional capabilities** (e.g., observability) when they are not desired.
3. **Swap capability implementations** (e.g., provide a custom observability handler).

Today the `ManifestCapabilityBuilder` already knows which capability *names* are
required vs optional thanks to `CAPABILITY_MANIFEST`. The manifest is how we enforce
that required capabilities cannot be disabled and optional ones can be toggled. We want
to expose that toggleability via Django settings so it can be managed centrally.

## Decision

We will introduce a settings-based override surface, e.g. a `GENERAL_MANAGER_CAPABILITIES`
dict keyed by fully qualified interface class names:

```python
GENERAL_MANAGER_CAPABILITIES = {
    "general_manager.interface.backends.database.database_interface.DatabaseInterface": {
        "enabled": {"notification"},
        "disabled": set(),
        "flags": {"access_control": True},
        "overrides": {
            "observability": "path.to.CustomObservabilityCapability",
        },
    },
}
```

At startup (inside AppConfig.ready or a dedicated bootstrap helper), we will:

1. Import each interface listed in the settings mapping.
2. Build a `CapabilityConfig` from the `enabled/disabled/flags` entry.
3. Apply any capability handler overrides (e.g., swap a capability class for a custom one)
   before instantiating capabilities.
4. Invoke `ManifestCapabilityBuilder.build(interface_cls, config=config)` so the manifest
   enforces required/optional rules and instantiates the appropriate capability handlers.

Any interface *not* mentioned in settings will continue to use the capabilities declared
in its class (today's default behavior).

## Manifest Rationale

The manifest remains critical for several reasons:

- **Contract definition**: It declares which capability names are required vs optional for
  each interface family. Bundles tell us *which classes* to instantiate; the manifest tells
  the builder *which names* must be present. This lets us keep validation (e.g., "you cannot
  disable `read` on DatabaseInterface") centralized.

- **Config validation**: When an operator specifies `disabled={"observability"}`, the manifest
  lets the builder reject that if `observability` is required, or accept it if it's optional.

- **Registry metadata**: Tools/tests rely on the manifest plan to inspect the capability set
  for a given interface. Removing the manifest would force us to infer this on the fly from
  bundles, making it harder to enforce stability.

## Consequences

- Teams get a central place (settings) to enable/disable optional capabilities or swap
  implementations without editing interface classes.
- We retain safety: required capabilities cannot be disabled because the manifest still
  drives the `ManifestCapabilityBuilder` (and raises errors when a config tries to remove them).
- Implementation-wise we need to add a settings loader + helper function that resolves the
  interface class, builds `CapabilityConfig`, applies overrides, and calls the builder.
- Tests must cover the settings-driven toggles (e.g., ensure disabling required capability
  still raises, enabling optional capability via settings works, custom capability classes
  can be injected).
