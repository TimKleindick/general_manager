# Capability-First Interfaces

Interfaces in GeneralManager are now declarative shells that list the capabilities they need. Instead of overriding lifecycle or persistence hooks, each interface sets `configured_capabilities` to a tuple of bundles or individual capability configurations. InterfaceBase (or OrmInterfaceBase) then instantiates those capability handlers, registers startup hooks/system checks if provided, and delegates CRUD/query operations entirely to the capabilities.

## Why capabilities?

- **Separation of concerns**: Each capability owns a single behavioural slice (ORM lifecycle, read-only sync, calculation lifecycle, observability, etc.), making the code easier to reason about and test.
- **Composable interfaces**: Database, read-only, existing-model, and calculation interfaces mix and match the same capability building blocks. For example, existing-model interfaces reuse the ORM writable bundle plus `ExistingModelResolutionCapability`.
- **Customization without inheritance chains**: To change behaviour you swap or extend capabilities (and update the bundles) instead of subclassing deep hierarchies.
- **Extensibility hooks**: Capabilities can provide startup hooks, system checks, and observability without interfaces needing bespoke wiring.

## Configuration entries

Use `InterfaceCapabilityConfig(CapabilityClass, options=None)` for a single
capability. `options` is copied into keyword arguments when the capability is
constructed; `None` means no keyword arguments, while an explicitly supplied
mapping is honored even when it is empty or has falsey truthiness. Constructor
errors and mapping-conversion errors are not wrapped. The config object is
frozen, but the mapping is copied only when `instantiate()` runs.

Use `CapabilitySet(label, entries)` for reusable bundles. The entries are stored
as an immutable tuple after construction; the constructor accepts any iterable of
concrete `InterfaceCapabilityConfig` entries and does not deep-copy or
runtime-validate the entry values beyond normal iteration.
`configured_capabilities` may contain a mix of direct `InterfaceCapabilityConfig`
values and `CapabilitySet` bundles. GeneralManager expands bundles one level,
preserves order, and does not deduplicate entries; later capabilities with the
same name replace earlier handlers when the interface binds them. Invalid
non-`CapabilitySet` runtime values pass through the expansion helpers unchanged
if callers bypass static typing, and iteration-time exceptions from supplied
iterables propagate unchanged. Interface binding is the validation boundary.

Manifest-driven builders record resolved declarations and concrete handlers in
`CapabilityRegistry`. Declaration registration consumes names into a temporary
set before mutating state, collapses duplicates, merges by default, and replaces
the existing declaration when `replace=True`. Concrete handler bindings are
stored separately as ordered tuples; changing declared names does not clear
instances, and binding instances does not register names. `get()` and
`snapshot()` return immutable declaration views, with `snapshot()` producing a
point-in-time read-only mapping that is not affected by later registry changes.
The registry is process-local and unsynchronized.

`CapabilityPlan` is the immutable manifest-side declaration. Its capability
fields use `CapabilityName`, the public string-literal capability identifier
type exported by `general_manager.interface.capabilities`. Required and optional
capability iterables are normalized to frozensets, duplicate names collapse, and
flag mappings are copied into a read-only mapping proxy. The plan model
intentionally allows a name to appear in both required and optional sets; later
manifest/build steps decide whether that combination is valid for a specific
interface.

`CapabilityConfig` is the mutable runtime input for optional capability toggles.
It copies the supplied enabled/disabled sets and flag mapping at construction,
then can be mutated by caller code before it is passed to the builder. Flag
values are not limited to booleans: the builder calls
`is_flag_enabled(name)`, which uses Python truthiness and treats missing flags
as disabled. `enabled` requests optional capability names, while `disabled`
removes optional names after flag and manual enables have been validated. If the
same optional capability appears in both sets, `disabled` wins; manually
enabling a non-optional capability still raises in the builder even if the name
is also disabled. `CapabilitySelection` is the immutable output of build
resolution; its `all` property is the set of required names plus activated
optional names, not every optional name in the manifest.

`ManifestCapabilityBuilder` is the public helper that turns a manifest plan into
bound handlers. `build(interface_cls, config=None)` resolves required and
optional capability names, rejects disabled required names, enables optionals
from flags or manual `CapabilityConfig.enabled`, removes disabled optional
names last, instantiates handlers in sorted capability-name order with
`interface_cls.capability_overrides`, binds them to the interface, and publishes
the final declaration plus concrete instances into the registry. If a selected
capability cannot be instantiated or attached, or if registry publication fails, the
builder restores the interface's previous capability selection, name set, and
handler mapping. Registry implementations own rollback for registry-side state
they mutate before raising. The builder does not wrap manifest, override,
handler, startup-hook, system-check, or registry errors.

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
