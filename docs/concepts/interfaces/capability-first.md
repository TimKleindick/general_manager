# Capability-First Interfaces

Interfaces in GeneralManager are now declarative shells that list the capabilities they need. Instead of overriding lifecycle or persistence hooks, each interface sets `configured_capabilities` to a tuple of bundles or individual capability configurations. InterfaceBase (or OrmInterfaceBase) then instantiates those capability handlers, registers startup hooks/system checks if provided, and delegates CRUD/query operations entirely to the capabilities.

## Why capabilities?

- **Separation of concerns**: Each capability owns a single behavioural slice (ORM lifecycle, read-only sync, calculation lifecycle, observability, etc.), making the code easier to reason about and test.
- **Composable interfaces**: Database, read-only, existing-model, and calculation interfaces mix and match the same capability building blocks. For example, existing-model interfaces reuse the ORM writable bundle plus `ExistingModelResolutionCapability`.
- **Customization without inheritance chains**: To change behaviour you swap or extend capabilities (and update the bundles) instead of subclassing deep hierarchies.
- **Extensibility hooks**: Capabilities can provide startup hooks, system checks, and observability without interfaces needing bespoke wiring.

## Directory structure

- `general_manager.interface.interfaces` – concrete interface classes (Database, ReadOnly, ExistingModel, Calculation).
- `general_manager.interface.bundles` – reusable capability sets (`ORM_WRITABLE_CAPABILITIES`, `READ_ONLY_CAPABILITIES`, etc.).
- `general_manager.interface.capabilities` – capability implementations grouped by concern (`orm/`, `read_only/`, `calculation/`, etc.).
- `general_manager.interface.manifests` – manifest definitions and builders that enforce required vs optional capability names.
- `general_manager.interface.utils` – shared plumbing (ORM base models, protocol definitions, error helpers).
- `general_manager.interface.infrastructure` – startup hook and system-check registries.

## Next steps

- Follow the [custom interface type how-to](../../howto/create_custom_interface_type.md) to build your own interface shell.
- Follow the [custom capability how-to](../../howto/create_custom_capability.md) to implement new behaviour that interfaces can compose.
- Browse the [custom capability examples](../../examples/custom_capability_examples.md) for ready-made snippets.
