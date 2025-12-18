# ADR 0004: Capability-Driven Startup Hooks

- Status: Implemented
- Date: 2025-11-12

## Context

Historically, read-only interfaces exposed classmethods (`sync_data`, `ensure_schema_is_up_to_date`, etc.) and `apps.py` hard-coded the bootstrap sequence for them. This coupling causes two issues:

1. **Limited extensibility** – Any new interface that needs a startup task (data sync, cache warm-up, schema verification, etc.) must change `apps.py` or duplicate the read-only helpers.
2. **Interface bloat** – `ReadOnlyInterface` still defines convenience classmethods even though the real behavior already lives in `ReadOnlyManagementCapability`. We want interfaces to be mere capability shells.

As we push more behavior into capabilities, we need a consistent way for capabilities to register “run this when the app boots” actions without coupling to specific interfaces.

## Decision

Introduce capability-driven registries for startup hooks and system checks:

- Define lightweight protocols (`StartupHook`, `SystemCheckHook`) and registries keyed by interface class (e.g. `register_startup_hook(interface_cls, hook)` and `register_system_check(interface_cls, check)`).
- Extend the capability binding pipeline (inside `InterfaceBase._bind_capability_handler`) to detect capabilities that expose either:
  - `get_startup_hooks(interface_cls)` returning callables to run before commands, and/or
  - `get_system_checks(interface_cls)` returning callables that perform schema/config validation.
- When such hooks exist, register them along with their owning interface in the appropriate registry.
- Replace the bespoke `patch_read_only_interface_sync` logic in `general_manager.apps` with a generic runner that executes every registered startup hook before management commands / runserver (keeping the existing autoreload guards). During `AppConfig.ready`, iterate the system-check registry and register each callable with Django’s `checks` framework.
- Allow multiple hooks/checks per interface; ordering follows capability attachment order so a capability can request cache warm-up, data sync, and schema validation independently.

## Consequences

- **Interfaces stay slim** – Read-only (and future) interfaces no longer need to expose helper classmethods or know about app wiring; they simply declare the capabilities that provide startup hooks.
- **Extensible startup behavior** – Any capability (existing or custom) can opt into the startup pipeline without framework changes. For example:
  - `ReadOnlyManagementCapability` registers a data sync hook.
  - A caching capability registers a cache warm-up hook.
  - A telemetry capability registers a “flush stale metrics” hook.
- **Predictable orchestration** – `apps.py` runs hooks via a single registry, ensuring consistent logging/observability and keeping permission to skip in autoreload scenarios.
- **Backward compatibility** – Interfaces no longer expose `.sync_data()` helpers; callers interact with the capability-provided hook directly, matching the rest of the capability-first API.
- **Testing** – Unit tests can patch the registry or capability-provided hooks directly, avoiding database touches in `SimpleTestCase`. Integration tests invoke the capability hook explicitly when they need to force a sync.

## Implementation Plan

1. Define `StartupHook` and `SystemCheckHook` protocols plus module-level registries (with helpers to register/iterate/reset for tests).
2. Update `InterfaceBase._bind_capability_handler` to register any hooks/checks provided by capabilities.
3. Teach `ReadOnlyManagementCapability` (and future capabilities) to expose both sync startup hooks and schema-check hooks via the new mechanism.
4. Replace `general_manager.apps.patch_read_only_interface_sync` with a generic startup-hook runner and add a registry-driven check registration step in `AppConfig.ready`.
5. Add tests covering:
   - Capability registration of multiple hooks and system checks.
   - Apps runner executing hooks (with runserver autoreload guard).
   - Django check registration via the new system-check registry.
6. Update ADR 0001/0002 and developer docs once the new API lands.

This ADR documents the target state so that we can incrementally implement the registry while keeping the current behavior working. Once the hooks infrastructure exists, new interfaces gain startup extensibility “for free”.

## Status Notes

- Startup hooks now live under `general_manager.interface.infrastructure.startup_hooks` and system checks under `infrastructure/system_checks.py`. Startup hooks may carry dependency resolvers so each capability can order its hooks independently (e.g., read-only sync resolves related interfaces first).
- `ReadOnlyManagementCapability` exposes both data sync and schema-check hooks (with a dependency resolver), and `InterfaceBase` registers them as capabilities bind to interfaces.
- `apps.py` runs every registered hook at startup, grouping hooks by resolver and ordering interfaces topologically per group, so future capabilities get bootstrap behavior and their own ordering without editing the app config again.
