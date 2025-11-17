# ADR 0002: Declarative Capability Configuration

- Status: Implemented
- Date: 2025-11-12

## Context

The interface layer is being simplified so each interface class merely declares the capabilities it needs. Capabilities encapsulate every behavioral concern—lifecycle wiring, metadata exposure, bucket/query helpers, read/write paths, validation, etc. The recent changes introduced:

- Configuration helpers (`InterfaceCapabilityConfig`, `CapabilitySet`) for listing individual capabilities or reusable bundles.
- An InterfaceBase that instantiates, registers, and delegates to capabilities defined via configuration rather than hard-coded overrides.
- Calculation interfaces piloting the model by composing `CALCULATION_CORE_CAPABILITIES` (read, query, lifecycle) and deleting bespoke helper methods.

## Decision

1. **Interfaces declare capability bundles**
   Each interface sets `configured_capabilities` to a tuple of capability configs or bundles. InterfaceBase flattens the config, instantiates handlers (injecting constructor kwargs when provided), and registers them like manifest-driven capabilities. This keeps interface classes declarative and removes duplicated helper methods.

2. **InterfaceBase delegates framework hooks to capabilities**
   Core methods (`get_data`, `get_attribute_types`, `filter`, `exclude`, `all`, `get_field_type`, lifecycle hooks) now defer entirely to the configured capability handlers. If an interface wants custom behavior, it swaps the capability in configuration instead of overriding the method.

3. **Lifecycle capabilities retain distinct names**
   Every lifecycle capability publishes a unique `name` (e.g., `calculation_lifecycle`, `orm_lifecycle`). Interfaces set `lifecycle_capability_name` so `handle_interface()` can retrieve the correct handler. We intentionally avoid collapsing these into a single literal like `"lifecycle"` so multiple lifecycle variants can coexist on the same interface without clobbering each other in `_capability_handlers`, and so the base class can deterministically pick the intended lifecycle flavor.

## Rollout

- **CalculationInterface** already ships as a pure configuration shell that composes `CALCULATION_CORE_CAPABILITIES`.
- **OrmInterfaceBase** remains the single ORM base; concrete interfaces (DatabaseInterface, ExistingModelInterface, ReadOnlyInterface) point their `configured_capabilities` at the appropriate bundles instead of relying on inheritance layers.
- **OrmInterfaceBase** hosts only the minimal initialization/search-date plumbing and delegates `_pre_create/_post_create` plus metadata lookups to capabilities, keeping both persistence and writable variants thin configuration shells.
- **ExistingModelInterface** consumes the writable bundle plus `ExistingModelResolutionCapability`, and its lifecycle is declared via `lifecycle_capability_name = "existing_model_resolution"`.
- **ReadOnlyInterface** composes `READ_ONLY_CAPABILITIES`, letting the read-only management capability provide schema syncing and lifecycle decorations.

## Consequences

- Adding a new interface behavior is achieved by writing/adding a capability to a bundle; interface classes stay small.
- Capability instances can be parameterized per interface using `InterfaceCapabilityConfig(options=...)`.
- Tests must exercise capability configuration rather than interface overrides (e.g., verifying custom bundles result in custom handlers).
- Lifecycle flexibility is preserved: interfaces can mix different lifecycle capabilities and explicitly choose which one powers manager creation by pointing `lifecycle_capability_name` at the desired capability name.

## Implementation Notes

- Every shipped interface now sets `configured_capabilities` to bundles from `general_manager.interface.bundles.*`, and the manifest/builder lives under `general_manager.interface.manifests`.
- Interfaces remain declarative shells; behavior is swapped by editing capability configs (or, in the future, via the settings override described by ADR-0003).

## Alternatives Considered

- **Single lifecycle capability name** – rejected because only one handler per name can live in `_capability_handlers`; using a shared `"lifecycle"` key would cause the latest capability to overwrite previous ones, making hybrid interfaces impossible and complicating capability selection.
- **Interface-specific overrides** – prior approach required manual overrides in each interface; it led to copy/paste and inconsistent behavior. Declarative configuration with shared bundles provides the same flexibility with less duplication.
