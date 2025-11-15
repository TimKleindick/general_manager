# ADR 0004: Capability-Driven Startup Hooks

- Status: Proposed
- Date: 2025-11-12

## Context

Read-only interfaces currently expose classmethods (`sync_data`, `ensure_schema_is_up_to_date`, etc.) and `apps.py` hard-codes the bootstrap sequence for them. This coupling causes two issues:

1. **Limited extensibility** – Any new interface that needs a startup task (data sync, cache warm-up, schema verification, etc.) must change `apps.py` or duplicate the read-only helpers.
2. **Interface bloat** – `ReadOnlyInterface` still defines convenience classmethods even though the real behavior already lives in `ReadOnlyManagementCapability`. We want interfaces to be mere capability shells.

As we push more behavior into capabilities, we need a consistent way for capabilities to register “run this when the app boots” actions without coupling to specific interfaces.

## Decision

Introduce a **Startup Hook Registry** driven by capabilities:

- Define a lightweight `StartupHook` protocol (e.g. `Callable[[type[InterfaceBase]], None]`) and registry API (`register_startup_hook(interface_cls, hook)`).
- Extend the capability binding pipeline (e.g. inside `InterfaceBase._bind_capability_handler`) to detect capabilities that expose either:
  - A `startup_hooks` iterable of callables, or
  - A `get_startup_hooks(interface_cls)` method returning callables.
- When such hooks exist, register them along with their owning interface in the global registry.
- Replace the bespoke `patch_read_only_interface_sync` logic in `general_manager.apps` with a generic runner that executes every registered hook before management commands / runserver (keeping the existing autoreload guards).
- Allow multiple hooks per interface; ordering follows capability attachment order so a capability can request cache warm-up and data sync independently.

## Consequences

- **Interfaces stay slim** – Read-only (and future) interfaces no longer need to expose helper classmethods or know about app wiring; they simply declare the capabilities that provide startup hooks.
- **Extensible startup behavior** – Any capability (existing or custom) can opt into the startup pipeline without framework changes. For example:
  - `ReadOnlyManagementCapability` registers a data sync hook.
  - A caching capability registers a cache warm-up hook.
  - A telemetry capability registers a “flush stale metrics” hook.
- **Predictable orchestration** – `apps.py` runs hooks via a single registry, ensuring consistent logging/observability and keeping permission to skip in autoreload scenarios.
- **Backward compatibility** – We can keep the existing `.sync_data()` method on interfaces temporarily as a thin delegator to the capability hook to avoid breaking public APIs, but the preferred access path shifts to startup hooks.
- **Testing** – Unit tests can patch the registry or capability-provided hooks directly, avoiding database touches in `SimpleTestCase`. Integration tests keep calling `Interface.sync_data()` until we deprecate that entry point.

## Implementation Plan

1. Define `StartupHook` protocol and a module-level registry (with helpers to `register`, `iter_hooks`, and `reset` for tests).
2. Update `InterfaceBase._bind_capability_handler` to inspect new optional capability attributes/methods and register any provided hooks.
3. Teach `ReadOnlyManagementCapability` to return its sync hook via the new mechanism.
4. Replace `general_manager.apps.patch_read_only_interface_sync` with a generic `patch_startup_hooks_runner` that uses the registry.
5. Add tests covering:
   - Capability registration of multiple hooks.
   - Apps runner executing all registered hooks exactly once per command (with runserver autoreload guard).
   - Backwards compatibility of existing `.sync_data()` delegations.
6. Update ADR 0001/0002 and developer docs once the new API lands.

This ADR documents the target state so that we can incrementally implement the registry while keeping the current behavior working. Once the hooks infrastructure exists, new interfaces gain startup extensibility “for free”.
