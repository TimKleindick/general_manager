# Interface API

## Historical execution context

Use `general_manager.api.as_of(search_date)` to make every supported read in an
operation use one historical snapshot. The positional and explicit keyword
forms are equivalent:

```python
from general_manager.api import as_of, current_as_of_date

with as_of("2022-01-01") as search_date:
    assert current_as_of_date() == search_date

with as_of(search_date="2022-01-01"):
    project = Project(1)
    projects = Project.filter(status="active")
```

`search_date` accepts an ISO date or datetime string (including a trailing
`Z`), a `datetime.date`, or a naive or timezone-aware `datetime.datetime`.
Date-only values become midnight in Django's current timezone. Naive datetimes
also use Django's current timezone; aware datetimes retain their timezone.
`current_as_of_date()` returns the active aware datetime, or `None` outside an
as-of context. Nesting is idempotent when both values represent the same instant,
even when their timezones differ. Distinct instants during a daylight-saving
fold still conflict. A different nested date, or a different explicit
date resolved while a context is active, raises
`HistoricalContextConflictError` without changing the outer context. The
previous value is restored when the context exits, including when its body
raises an exception.

Invalid or unsupported inputs raise `InvalidSearchDateError`. An explicit
`search_date` passed to a manager or query inside the context must represent the
same instant; it cannot override the operation snapshot. Managers and buckets
bound to current data or another date likewise raise
`HistoricalContextConflictError` when consumed in the context.

The context is strictly read-only. GeneralManager and direct interface create,
update, and delete entry points raise `HistoricalMutationError` before
permission checks, transports, signals, or database writes. ORM-backed
interfaces provide historical reads and calculation interfaces propagate the
snapshot through their dependencies. Request-backed and custom interfaces are
fail-closed unless they explicitly declare supported behavior: their reads
raise `HistoricalReadNotSupportedError` before external loading begins.

Historical many-to-many membership requires django-simple-history through
tables. Generated `DatabaseInterface` models and auto-registered existing
models include local many-to-many fields in history registration, but the
resulting `Historical<Model>_<field>` tables must be deployed through the
normal Django migration workflow. Only membership changes recorded after those
tables are deployed can be reconstructed; scalar history cannot recover
pre-rollout membership. If membership or target history is unavailable, a
dated relation read fails closed instead of returning current relation data.
Generated relations that name a custom through model by an unresolved string
cannot be registered when the owner model is created; their historical reads
also fail closed. Define and history-track such models explicitly when custom
through history is required.

::: general_manager.interface.base_interface.InterfaceBase

`InterfaceBase` is the capability-driven base for custom interfaces. Subclasses
declare `input_fields` as a mapping of names to `Input[...]` objects. Positional
constructor arguments are mapped to input fields by declaration order, keyword
arguments are matched by name, and `<name>_id` aliases are accepted for declared
inputs when the canonical `<name>` was not already supplied. Positional overflow,
duplicate positional-plus-keyword values, alias collisions, and unknown keyword
or alias names raise `UnexpectedInputArgumentsError`. Required inputs that are
missing raise `MissingInputArgumentsError`; circular input dependencies raise
`CircularInputDependencyError`; invalid input types raise `InvalidInputTypeError`;
failed bounds or validators raise `InvalidInputConstraintError`; invalid
possible-values containers raise `InvalidPossibleValuesTypeError`; and failed
possible-value membership raises `InvalidInputValueError` when possible-value
validation is enabled. Possible-value membership is controlled by
`VALIDATE_INPUT_VALUES`; if that setting is unset, membership validation follows
`settings.DEBUG`.

`create()`, `update()`, `delete()`, `get_data()`, `filter()`, `exclude()`, and
`all()` delegate to configured capability handlers. Base `create()` is typed to
the capability-level result mapping, while `update()`, `delete()`, and
`get_data()` preserve arbitrary capability results. Query capabilities must
return `Bucket` instances from `filter()`, `exclude()`, and `all()`; manager
classes preserve or narrow those bucket types. Missing capabilities raise
`NotImplementedError`.
`get_attribute_types()` and `get_attributes()` require a read capability and do
not synthesize fallbacks from `input_fields`; `get_field_type()` delegates to the
read capability when present and otherwise falls back only to declared inputs.
The base observability executor calls `before_operation`, then the delegated
operation, then `after_operation`; if `before_operation` raises the delegated
operation is not called, and observer hook exceptions propagate. `handle_interface()`
delegates class creation to the configured lifecycle capability's `pre_create`
and `post_create` callables, passing only keyword arguments accepted by each
callable's signature. Missing lifecycle hooks raise `NotImplementedError`;
lifecycle exceptions and malformed lifecycle return values are not wrapped by
`InterfaceBase`.

::: general_manager.interface.orm_interface.OrmInterfaceBase

`OrmInterfaceBase` is the public base for Django ORM-backed interfaces. It
inherits `InterfaceBase`, declares the default `input_fields` as
`{"id": Input(int)}`, and loads a current or historical ORM row for that primary
key into the internal `_instance` attribute during construction. Pass
`search_date=...` to resolve a point-in-time row; naive datetimes are made
timezone-aware with Django's current timezone before lookup. The
`historical_lookup_buffer_seconds` class attribute controls how far in the past a
`search_date` must be before ORM support reads from history tables instead of the
current row.

Subclasses normally use `DatabaseInterface`, `ExistingModelInterface`, or
`ReadOnlyInterface` rather than subclassing this base directly, but custom ORM
interface types can reuse it when they provide the required lifecycle capability
configuration. If a custom ORM interface overrides `input_fields`, preserve
`"id"` unless you also replace initialization and row loading. The underscored
helpers such as `_from_trusted_orm_instance()` and `_default_base_model_class()`
are internal extension hooks for bucket hydration and class creation. Underscored
helpers in ORM capability modules follow the same rule even if generated API
pages render them; application code should prefer the documented manager,
interface, and bucket methods.

`handle_custom_fields(model)` delegates to the configured ORM lifecycle
capability and returns `(field_names, ignore_markers)`, where ignore markers are
the generated `<field>_value` and `<field>_unit` names. Missing or invalid
lifecycle capabilities propagate the capability lookup/type errors.

::: general_manager.interface.interfaces.database.DatabaseInterface

::: general_manager.interface.interfaces.existing_model.ExistingModelInterface

`ExistingModelInterface` is the writable ORM interface for wrapping a Django
model that already exists outside GeneralManager. Direct subclasses set
`model` to a Django model class or to an app-label string accepted by Django's
app registry, such as `settings.AUTH_USER_MODEL` or `"app_label.ModelName"`.
The `existing_model_resolution` lifecycle capability resolves that reference
during manager class creation, stores the resolved class on the concrete
interface as `_model` and `model`, registers database-aware simple-history when
needed, applies interface rules, and builds a factory for the existing table. Missing
model declarations raise `MissingModelConfigurationError`; invalid strings,
non-model classes, and other invalid references raise
`InvalidModelReferenceError`. Auto-registration includes local many-to-many
fields and routes base and relation history to the live row's database alias. A
pre-existing tracker is accepted on the default alias. On a configured
non-default alias its history model must carry GeneralManager's database-aware
marker or manager creation raises `UnsafeHistoryConfigurationError`. Soft
delete is enabled when the resolved model exposes an `is_active` attribute. The simple-history
marker is `model._meta.simple_history_manager_attribute`; interface rules are
the optional `Meta.rules` sequence on the interface. This shell class does not
declare separate create/update/delete signatures. Those public operations are
the inherited GeneralManager and writable ORM capability APIs documented in the
core manager and existing-model guides; `ExistingModelInterface` selects the
existing-model lifecycle for them.

Construction inherits the ORM input contract: pass the wrapped row `id`, parsed
through the default `Input(int)`, and optional `search_date: datetime | None`.
Naive search dates are made aware with Django's current timezone, and history
lookup uses `historical_lookup_buffer_seconds` from `OrmInterfaceBase`. Missing
current or historical rows propagate the wrapped model's `DoesNotExist`
exception. `get_field_type(field_name)` first lazily resolves this interface's
own model, then delegates to ORM read support. Stored model fields return the
Django field class, managed relations return the related model's
`_general_manager_class` when present, and generated relation/custom
descriptors return their metadata `type`; fields absent from both the model and
descriptor map raise Django's `FieldDoesNotExist`. A managed relation means a
Django relation whose related model has `_general_manager_class`; generated
descriptors are ORM support descriptor-map entries for custom fields and
generated relation helpers. The lazy cache is class-local, so a subclass that
declares a different `model` resolves that declaration instead of reusing a
parent interface's cached `_model`.

::: general_manager.interface.interfaces.read_only.ReadOnlyInterface

`ReadOnlyInterface` is the capability shell for static or generated datasets
mirrored into a generated Django model. Subclasses normally appear as a
manager's nested `Interface` and declare Django model fields; the parent
manager provides `_data` as either a JSON string or a list of row dictionaries.
The read-only lifecycle forces `Meta.use_soft_delete = True`, generates the
model with `GeneralManagerBasisModel`, and registers the created manager in the
read-only startup registry so schema checks and data sync can run during
startup.

Construction inherits the ORM input contract: pass the mirrored row `id`,
parsed through default `Input(int)`, and optional
`search_date: datetime | None`. Missing current or historical rows propagate
the generated model's `DoesNotExist` exception. The class itself does not
define separate public mutation methods; read/query/history/validation,
soft-delete, schema-check, and sync behavior come from `READ_ONLY_CAPABILITIES`
and the owning `GeneralManager` API. Sync errors such as
`MissingReadOnlyBindingError`, `MissingReadOnlyDataError`,
`InvalidReadOnlyDataTypeError`, `InvalidReadOnlyDataFormatError`,
`MissingUniqueFieldError`, and `ReadOnlyRelationLookupError` are raised by the
read-only management capability when the parent/model binding, `_data` payload,
unique row identity, or relation lookups are invalid.
The successful public manager surface is construction by id, attribute reads,
`all()`, `filter()`, `exclude()`, and history access. These return manager
instances, buckets of manager instances, or the generated model's history
queryset. `READ_ONLY_CAPABILITIES` intentionally omits create, update, and
delete capabilities, so inherited mutation entry points are not read-only data
APIs. `_interface_type`, `_parent_class`, and `configured_capabilities` are
framework wiring; the parent manager's `_data` attribute is the public dataset
source despite its legacy underscore name. Duplicate composite identities in
`_data` are not rejected during full synchronization; rows for the same identity
are processed in order, so later payload values can overwrite earlier values for
that row. Relation lookup dictionaries are flattened into Django `__` lookups;
zero or multiple matches raise `ReadOnlyRelationLookupError`, while malformed
lookup keys or values propagate the Django query error. For many-to-many fields,
an omitted key leaves the relation unchanged, a present `None` clears it, and a
present list replaces it.

::: general_manager.interface.interfaces.calculation.CalculationInterface

::: general_manager.interface.interfaces.request.RequestInterface

::: general_manager.interface.interfaces.remote_manager.RemoteManagerInterface

`RemoteManagerInterface` is the generated-client interface for another
GeneralManager service's `RemoteAPI` exposure. Direct subclasses read
`Meta.base_url`, optional `Meta.base_path`, `Meta.remote_manager`, optional
`Meta.protocol_version`, and optional `Meta.websocket_invalidation_enabled` at
class creation time. Omitted `Meta.base_path` defaults to `/gm`, omitted
`Meta.protocol_version` defaults to `"v1"`, and
`Meta.websocket_invalidation_enabled` is coerced with `bool(...)`. Only direct
subclasses are configured by this class hook; indirect subclasses inherit the
already configured values unless they inherit directly from
`RemoteManagerInterface`. The interface validates those values, creates standard
detail/list/create/update/delete request operations, installs the protocol
version header, and configures response normalization for RemoteAPI envelopes.
If a subclass already declares `transport_config`, timeout, auth, retry,
metrics, and trace settings are preserved while the base URL and response
normalizer are replaced with the validated remote-manager settings.

`get_websocket_invalidation_url()` returns the websocket invalidation URL for
the interface. Class creation validates `base_url` as HTTP(S). `https` base URLs
become `wss`; `http` base URLs become `ws`; a path prefix in `base_url` is
stripped of trailing slashes and preserved before
`<base_path>/ws/<remote_manager>`; and `protocol_version` is emitted as the
`version` query parameter. Query and fragment components from `base_url` are not
preserved. The helper itself does not check
`websocket_invalidation_enabled`; `RemoteInvalidationClient` performs that
enabled check before opening connections.
`handle_invalidation_event(event)` compares only `protocol_version`,
`base_path`, and `resource_name`; matching events invalidate local remote-query
caches for the parent manager and return `True`, while non-matching events
return `False`. Cache invalidation errors propagate, and calling it before the
interface is bound to a parent manager can raise `AttributeError`.

## Capabilities

::: general_manager.interface.capabilities.base.CapabilityName

::: general_manager.interface.capabilities.base.Capability

::: general_manager.interface.capabilities.builtin.BaseCapability

::: general_manager.interface.capabilities.builtin.ReadCapability

::: general_manager.interface.capabilities.builtin.CreateCapability

::: general_manager.interface.capabilities.builtin.UpdateCapability

::: general_manager.interface.capabilities.builtin.DeleteCapability

::: general_manager.interface.capabilities.builtin.HistoryCapability

::: general_manager.interface.capabilities.builtin.ValidationCapability

::: general_manager.interface.capabilities.builtin.NotificationCapability

::: general_manager.interface.capabilities.builtin.SchedulingCapability

::: general_manager.interface.capabilities.builtin.AccessControlCapability

::: general_manager.interface.capabilities.builtin.ObservabilityCapability

::: general_manager.interface.capabilities.exceptions.CapabilityBindingError

::: general_manager.interface.capabilities.configuration.InterfaceCapabilityConfig

::: general_manager.interface.capabilities.configuration.CapabilitySet

::: general_manager.interface.capabilities.configuration.flatten_capability_entries

::: general_manager.interface.capabilities.configuration.iter_capability_entries

::: general_manager.interface.capabilities.factory.CAPABILITY_CLASS_MAP

::: general_manager.interface.capabilities.factory.CapabilityOverride

::: general_manager.interface.capabilities.factory.build_capabilities

::: general_manager.interface.capabilities.registry.CapabilityRegistry

::: general_manager.interface.capabilities.orm_utils.payload_normalizer.PayloadNormalizer

`general_manager.interface.capabilities.core.utils.with_observability` is a
public helper for wrapping capability operations with optional observability
hooks. Its full behavior is described in the Core Observability Utility section
below.

::: general_manager.interface.bundles.calculation.CALCULATION_CORE_CAPABILITIES

::: general_manager.interface.bundles.database.ORM_PERSISTENCE_CAPABILITIES

::: general_manager.interface.bundles.database.ORM_WRITABLE_CAPABILITIES

::: general_manager.interface.bundles.database.EXISTING_MODEL_CAPABILITIES

::: general_manager.interface.bundles.database.READ_ONLY_CAPABILITIES

::: general_manager.interface.bundles.remote_manager.REMOTE_MANAGER_CAPABILITIES

::: general_manager.interface.bundles.request.REQUEST_CORE_CAPABILITIES

::: general_manager.interface.bundles.request.REQUEST_MUTATION_CAPABILITIES

::: general_manager.interface.bundles.request.REQUEST_CAPABILITIES

`CapabilityName` is the public literal set of supported capability identifiers.
Interfaces use these stable string keys in `_capabilities`,
`_capability_handlers`, `capability_overrides`, manifests, and capability
registries. `Capability` is a runtime-checkable protocol for capability handler
instances: a handler exposes a class-level `name` and implements
`setup(interface_cls) -> None` and `teardown(interface_cls) -> None`. Both
methods mutate the supplied interface class in place. Concrete implementations
own their validation and error types; the protocol does not normalize
exceptions and does not promise idempotency.

`BaseCapability` is the default base class for concrete capability handlers. It
checks every name in `required_attributes` with `hasattr(interface_cls, name)`;
it does not call those attributes or validate their signatures. The dataclass has
no instance fields or constructor options of its own. Successful `setup()` copies
the interface class's current `_capability_handlers` mapping by converting it to
a plain `dict`, or starts from an empty plain `dict` when no registry exists yet,
registers the capability instance under its `name`, and writes the copied
mapping back. `teardown()` uses the same plain-`dict` conversion, or starts from
an empty mapping when no registry exists yet, removes that name if present, and
writes the mapping back; repeated teardown is a no-op.
Missing required attributes raise `CapabilityBindingError` with sorted missing
names. The underlying missing-attribute formatter returns
`"missing required attributes: "` for empty input, though `setup()` only calls it
after at least one name is missing. Non-`AttributeError` failures from
`hasattr()`, registry conversion, and class assignment propagate unchanged.
Repeated setup replaces the handler currently stored for the same capability name.
`ReadCapability`, `CreateCapability`, `UpdateCapability`, and `DeleteCapability`
require `get_data`, `create`, `update`, and `delete` respectively.
`HistoryCapability` and `ValidationCapability` both require
`get_attribute_types`. `NotificationCapability`, `SchedulingCapability`,
`AccessControlCapability`, and `ObservabilityCapability` are marker handlers
with no additional requirements beyond registration.

`CapabilityBindingError(capability_name, reason)` is raised when a capability
handler cannot attach to an interface class. The error subclasses
`RuntimeError`, stores `capability_name` and `reason` unchanged as public
attributes, and formats its message as
`Capability '<name>' could not be attached: <reason>`. Empty and multiline
reasons are preserved exactly in the attributes and formatted message. The
exceptions module's public export list contains only `CapabilityBindingError`.

`InterfaceCapabilityConfig(handler, options=None)` is the public declarative
entry used by `Interface.configured_capabilities`. `handler` is the capability
class. `options=None` constructs it without keyword arguments; any supplied
mapping, including an empty or otherwise falsey mapping, is copied to a plain
`dict` at `instantiate()` time and expanded as keyword arguments. The config is
frozen, but the original mapping is not copied until instantiation. Constructor
errors and mapping-conversion errors propagate unchanged.

`CapabilitySet(label, entries)` names a reusable bundle of concrete
`InterfaceCapabilityConfig` entries. The constructor accepts any iterable and
stores `entries` as an immutable tuple attribute; it does not deep-copy or
runtime-validate entry values beyond normal iteration. `iter_capability_entries(entries)`
and `flatten_capability_entries(entries)` expand `CapabilitySet` values one
level, preserve input order, do not deduplicate, and consume the supplied
iterable once. `iter_capability_entries()` stays lazy for the outer iterable,
while `flatten_capability_entries()` returns a tuple. They do not validate that
handlers implement the capability protocol; invalid non-`CapabilitySet` runtime
values are yielded or returned unchanged if callers bypass static typing.
Iteration-time exceptions from the supplied iterable propagate unchanged.
`InterfaceBase` performs capability protocol checks when it instantiates and
binds configured capabilities. The configuration module's public export list is
`CapabilityConfigEntry`, `CapabilitySet`, `InterfaceCapabilityConfig`,
`flatten_capability_entries`, and `iter_capability_entries`.

`CAPABILITY_CLASS_MAP` is the default mapping from public `CapabilityName`
values to built-in capability classes used by the legacy capability factory.
`CapabilityOverride` is either a capability class or a zero-argument callable
returning a capability instance. `build_capabilities(interface_cls, names,
overrides)` consumes `names` once, preserves order, returns a mutable list, and
creates duplicate instances for duplicate names. A non-`None` override value for
a name takes precedence over `CAPABILITY_CLASS_MAP`, including for runtime names
outside the static `CapabilityName` vocabulary. The current implementation
accepts `interface_cls` for compatibility but does not inspect it or pass it to
handlers. Unknown names without overrides raise `KeyError("Unknown capability
'<name>'")`; iteration, mapping lookup, and handler construction errors
propagate unchanged.

`CapabilityRegistry` is the in-memory registry used by manifest-driven
capability builders to track resolved capability declarations and concrete
capability instances per interface class. `register(interface_cls,
capabilities, replace=False)` consumes `capabilities` exactly once into a
temporary set before mutating internal state. Duplicate names collapse;
`replace=False` merges into existing declarations; `replace=True` replaces the
existing declaration, including with an empty set. Iteration errors propagate
before the registry is changed. `get(interface_cls)` returns a defensive
`frozenset`, or an empty `frozenset` for unregistered interfaces.
`bind_instances(interface_cls, capabilities)` consumes concrete capability
instances once into an ordered tuple before assignment and replaces only the
instance binding for that interface. Declared names and concrete instances are
independent: registering names does not clear instances, and binding instances
does not register names. `instances(interface_cls)` returns the stored tuple or
an empty tuple. `snapshot()` returns a `MappingProxyType` over a newly built
mapping of interface classes to `frozenset` declarations; later registry
mutations do not affect prior snapshots, and concrete instances are not
included. The registry is process-local, has no locking, and trusts runtime
values if callers bypass static typing.

::: general_manager.interface.manifests.capability_models.CapabilityPlan

::: general_manager.interface.manifests.capability_models.CapabilityConfig

::: general_manager.interface.manifests.capability_models.CapabilitySelection

`CapabilityPlan(required=frozenset(), optional=frozenset(), flags={})` is the
immutable manifest entry used by `CapabilityManifest`. `required` and
`optional` are normalized to `frozenset` values, so duplicate capability names
collapse. A name may appear in both sets; the model stores that state and leaves
interpretation to manifest resolution and the builder. `flags` maps runtime
flag names to capability names, is copied to a plain `dict`, and is exposed as a
read-only mapping proxy. Iteration, hashing, and mapping-conversion errors
propagate unchanged. All capability-name fields use the public `CapabilityName`
string-literal type exported by `general_manager.interface.capabilities`.

`CapabilityConfig(enabled=set(), disabled=set(), flags={})` is the mutable
runtime toggle object passed to `ManifestCapabilityBuilder.build()`. The
constructor copies supplied enabled/disabled sets and the flag mapping, then the
instance remains mutable. Flag values are object-valued and truth-tested with
Python `bool(...)`; missing flags are disabled. `enabled` requests optional
capabilities, while `disabled` removes optional capabilities after flag and
manual enables have been validated. If the same optional name appears in both
sets, disabled wins; manually enabling a non-optional name still raises in the
builder even if the name is also disabled. `CapabilitySelection(required,
optional, activated_optional)` is the immutable build result. It normalizes all
three fields to `frozenset` values. Its `all` property returns required names
plus activated optional names and excludes inactive optional names. The model
does not validate that activated names are present in `optional`; the builder is
the validation boundary.

::: general_manager.interface.manifests.capability_builder.ManifestCapabilityBuilder

`ManifestCapabilityBuilder(manifest=None, registry=None)` resolves a
`CapabilityManifest` for one interface class and binds concrete capability
handlers. `manifest=None` uses the module default manifest; `registry=None`
creates a fresh `CapabilityRegistry` for the builder. The `registry` property
returns that exact registry object.

`build(interface_cls, config=None)` resolves the manifest, rejects disabled
required capabilities, activates optional capabilities from enabled flags and
manual enables, removes disabled optional capabilities, creates handlers in
sorted capability-name order using `interface_cls.capability_overrides`, binds
each handler to the interface, then replaces the registry declaration and
concrete handler tuple for that interface. A capability present in both
`enabled` and `disabled` is disabled when it is optional; a non-optional manual
enable still raises even if that name also appears in `disabled`. Unknown
selected capability names raise `KeyError`; invalid optional selections raise
`ValueError`; handler construction, setup/teardown, startup-hook registration,
system-check registration, manifest, config, override, and registry errors
propagate unchanged. If resolution, instantiation, or attachment fails before
registry publication, or if registry publication itself raises, the builder
restores the interface's previous selection, capability-name set, and handler
mapping. Registry implementations own rollback for any registry-side state they
mutate before raising.

`with_observability(target, *, operation, payload, func)` is the shared wrapper
used by capability implementations that emit observability hooks. It is the only
public export from `general_manager.interface.capabilities.core.utils`. The
wrapper looks up `target.get_capability_handler("observability")` when that
method exists. If the method is absent or returns `None`, `func()` is called
directly and the payload is not copied. If a capability is present, the wrapper
reads `before_operation`, `after_operation`, and `on_error` with
`getattr(..., None)`. Absent hook attributes and attributes set to `None` are
ignored; non-`None` values are called as hooks, so non-callable values fail when
called. It shallow-copies `payload` into one plain `dict` after capability lookup
and hook-attribute lookup, then passes the same copy to every hook for that
invocation. Hook order is `before_operation`, then `func()`, then
`after_operation` on success, or `on_error` when `func()` raises.
`before_operation` exceptions prevent `func()` from running; `on_error`
exceptions replace the original `func()` exception; `after_operation` exceptions
replace the successful result. Exceptions from capability lookup, hook-attribute
lookup, payload conversion, hooks, and `func()` propagate unchanged.

`CALCULATION_CORE_CAPABILITIES` is the reusable bundle installed by
`CalculationInterface`. Its label is `"calculation_core"` and its entries are
`CalculationLifecycleCapability`, `CalculationReadCapability`, and
`CalculationQueryCapability`, in that order. The lifecycle entry publishes the
`"calculation_lifecycle"` capability name used by
`CalculationInterface.lifecycle_capability_name`; the read capability backs
attribute access and `get_data()`, and the query capability backs `all()`,
`filter()`, and `exclude()`. The bundle intentionally does not include create,
update, delete, or ORM capabilities.

`ORM_PERSISTENCE_CAPABILITIES` is the shared ORM persistence bundle. Its label is
`"orm_persistence_core"` and its ordered entries install ORM support, ORM
lifecycle, soft-delete handling, ORM read, validation, history, query, and
logging observability capabilities. `ORM_WRITABLE_CAPABILITIES` has label
`"orm_writable_core"`, starts with the persistence entries in the same order,
then adds mutation, create, update, and delete capabilities; this is the bundle
installed by `DatabaseInterface`. `EXISTING_MODEL_CAPABILITIES` has label
`"existing_model_core"`, starts with the writable entries in the same order, then
adds `ExistingModelResolutionCapability`; this is the bundle installed by
`ExistingModelInterface`. `READ_ONLY_CAPABILITIES` has label `"read_only_core"`
and installs ORM support, read-only lifecycle, soft-delete handling, ORM read,
validation, history, query, logging observability, and read-only management
capabilities, in that order. The read-only lifecycle capability intentionally
publishes the shared `"orm_lifecycle"` capability name so `OrmInterfaceBase`
lifecycle lookup continues to work. The bundle intentionally omits mutation,
create, update, and delete capabilities.

`REMOTE_MANAGER_CAPABILITIES` is the bundle installed by
`RemoteManagerInterface`. Its label is `"remote_manager"` and its ordered
entries install request lifecycle, request read, request validation, remote
manager query, request create, request update, request delete, and logging
observability capabilities. `RemoteManagerQueryCapability` publishes the shared
`"query"` capability name and is also present in
`RemoteManagerInterface.capability_overrides` so request query behavior is
replaced by the RemoteAPI-aware query capability.

`REQUEST_CORE_CAPABILITIES` is the bundle installed by `RequestInterface`. Its
label is `"request_core"` and its ordered entries install request lifecycle,
request read, request validation, request query, and logging observability
capabilities. `REQUEST_CAPABILITIES` is an alias for `REQUEST_CORE_CAPABILITIES`
kept for callers that need the default request-interface bundle. `REQUEST_MUTATION_CAPABILITIES`
has label `"request_mutation"` and contains request create, request update, and
request delete capabilities, in that order. `RequestInterface` does not install
the mutation bundle wholesale by default; during subclass configuration it adds
the matching create, update, and delete capability entries only when
`Meta.create_operation`, `Meta.update_operation`, or `Meta.delete_operation` is
declared and the handler is not already present.

::: general_manager.interface.capabilities.orm

`OrmLifecycleCapability` is the class-creation capability behind generated ORM
interfaces such as `DatabaseInterface` and read-only variants. Its
`pre_create(name=..., attrs=..., interface=..., base_model_class=...)` hook
collects Django `models.Field` attributes declared on the interface. The class
creation path reaches that logic through `OrmInterfaceBase.handle_custom_fields()`;
the lifecycle capability method that performs the inspection is
`describe_custom_fields(model)`. The hook extracts `Meta.use_soft_delete` and
`Meta.rules`, builds a generated Django model class, builds a concrete interface
subclass bound to that model, and installs `attrs["Interface"]`,
`attrs["Factory"]`, and `attrs["_interface_type"]`.
`attrs["Factory"]` may provide a custom factory definition; otherwise the nested
`interface.Factory` definition is used. The returned tuple is the updated attrs
mapping, the concrete `OrmInterfaceBase[models.Model]` subclass, and the
generated model class. Extracting `Meta.use_soft_delete` and `Meta.rules`
mutates the original nested `Meta` class by deleting those two attributes before
the remaining `Meta` attributes are attached to the generated Django model.

`post_create(new_class=..., interface_class=..., model=...)` wires the created
manager class back into the interface and model by setting
`interface_class._parent_class`, `model._general_manager_class`, and the manager
objects exposed as `new_class.objects`. When soft delete is enabled it also
sets `new_class.all_objects` to a manager that includes inactive rows. A `None`
model is a no-op for lifecycle compatibility.

`OrmPersistenceSupportCapability` centralizes ORM support helpers used by read,
query, history, and mutation capabilities. `get_database_alias()` reads the
interface `database` class attribute. `get_manager(interface_cls,
only_active=True)` returns the selected Django manager, honoring the database
alias and soft-delete state, and caches the active manager on the interface.
`get_queryset()` returns `get_manager(..., only_active=True).all()`.
`get_payload_normalizer()` binds a `PayloadNormalizer` to the interface model.
`get_field_descriptors()` builds and caches descriptor metadata and accessors.
Descriptor metadata is generated from concrete model fields, foreign keys,
reverse one-to-one relations, many-to-many relations, and reverse one-to-many
relations. Each descriptor contains metadata keys `type`, `default`,
`is_required`, `is_editable`, and `is_derived`. Big-integer-like fields may add
`graphql_scalar`. Direct and collection relations also add
`relation_kind="direct"` or `"collection"` and `filter_lookup` when the relation
can be used as a lookup root. Collection relations are exposed as `<name>_list`
attributes and resolve to the related manager/queryset or to a filtered
GeneralManager class when the related model has one. Collection descriptor names
prefer the declared relation or related model name, then the Django accessor
fallback, then relation-field-derived fallback names; if every candidate
collides, `DuplicateFieldNameError` is raised. Descriptor construction expects
the interface class to expose `_model` as a Django model class. A custom
collection resolver is called with positional arguments
`(interface_instance, field_call, field_name)`, and resolver exceptions are not
wrapped. GeneralManager-backed collection accessors raise
`MissingRelatedFieldsError` only when no explicit relation lookup was supplied
and no related field can be found at access time; multiple discovered related
fields are all used as filter constraints.
`resolve_many_to_many()` returns a related queryset for a many-to-many accessor;
when the relation points through simple-history rows and the interface has a
historical `search_date`, it returns the target history queryset as of that date,
otherwise it returns live target rows filtered by related ids. Missing target
relation metadata returns an empty queryset rather than raising. Invalid
many-to-many accessor names raise `AttributeError`, and invalid interface model
field names raise Django's `FieldDoesNotExist`. A dated relation read fails with
`HistoricalReadNotSupportedError` (chained from `HistoryNotSupportedError`) when
either membership history or target-row history is unavailable; it never falls
back to current relation data. This remains true inside the scalar historical
lookup buffer: a source row may be hydrated from the live table, but its
many-to-many membership is resolved from the source history row at the effective
date. Equal `history_date` values are broken by greatest `history_id` for both
source and related rows.

Historical through-table columns are resolved from the concrete
`ManyToManyField` source and reverse field metadata, including explicit
`through_fields`; this preserves direction for asymmetric self-relations and
custom through models with multiple foreign keys to the same model. Configured
database aliases are applied independently to source membership and target
history queries. When both querysets use the same alias, membership remains a
lazy SQL subquery. A cross-alias relation instead materializes the membership ID
list before querying target history because Django cannot execute a correct
cross-database subquery.

Generated `DatabaseInterface` models register declared many-to-many fields with
django-simple-history. Deployments must provision the resulting
`Historical<Model>_<field>` through tables using the project's normal Django
schema and migration workflow. Membership history is reliable only for changes
recorded after that schema is rolled out: pre-rollout membership cannot be
reconstructed or backfilled from scalar history alone. A generated relation
whose custom through model is still an unresolved string during owner-model
creation is not auto-registered for history; define and track that through
model explicitly if its membership must support historical reads.

`OrmReadCapability.get_data(interface_instance)` returns the live model row for
`pk` or, when `search_date` is older than the interface
`historical_lookup_buffer_seconds`, the matching historical row. It caches reads
inside the active calculation run using interface class, primary key, database
alias, active/all manager choice, and search date. Missing rows propagate the
generated model's `DoesNotExist`.

`OrmHistoryCapability` is the history helper used by ORM read/query support.
`get_historical_record(interface_cls, instance, search_date=None)` resolves the
primary key from `instance.pk`, then `instance.id`, then
`instance.identification["id"]`; it prefers `instance.history` when present,
otherwise falls back to `interface_cls._model.history`. When `search_date` is
provided it filters with `history_date__lte=search_date`, orders by
`history_date` and `history_id`, applies any configured database alias, and
returns the latest historical model row or `None`.
`get_history_queryset_for_manager()` returns the
model history queryset scoped to a manager-like object's primary key and raises
`HistoryNotSupportedError` when the model has no history manager or the manager
cannot be identified; it applies any configured database alias before returning
the filtered queryset. `get_historical_queryset(interface_cls, search_date)`
applies any configured database alias and selects the latest non-deletion
history row per object at or before `search_date`, using `history_id` to break
same-timestamp ties. It raises `HistoryNotSupportedError` when history is
unavailable.
`get_historical_record_by_pk()` filters
`id=pk, history_date__lte=search_date` and returns `None` when `search_date` is
`None`, history is unavailable, or no row matches.

`OrmQueryCapability.filter()` and `exclude()` normalize payloads through the ORM
payload normalizer, translate snake_case reverse-relation aliases, honor
`include_inactive`, and accept `search_date` as either `date` or `datetime`.
Dates are converted to midnight datetimes and then normalized through
`OrmInterfaceBase.normalize_search_date()`. Invalid `search_date` inputs raise
`SearchDateInputError` before normalization, and custom normalizers that return
non-datetimes raise `SearchDateNormalizationError`. The visible
`InvalidSearchDateTypeError` is a compatibility exception for custom validators
that want to raise a message-bearing type; the built-in query path uses the two
more specific errors above. Historical querysets require the history capability;
otherwise `HistoryNotSupportedError` is raised. Ambiguous reverse aliases raise
`AmbiguousReverseFilterAliasError`.

`OrmReadCapability.get_field_type(name)` returns a type object: the Django field
class for stored fields, the related manager class for GeneralManager-backed
relations, or the synthetic descriptor metadata `"type"` value. Unknown names
raise Django's `FieldDoesNotExist`.

`DjangoManagerSelector(model, database_alias, use_soft_delete,
cached_active=None)` is the public ORM utility that chooses Django managers for
ORM-backed capability reads. `active_manager()` returns the model default manager
when soft delete is disabled or when the model exposes an `all_objects`
attribute by presence check; otherwise it reuses `cached_active` as-is or creates
and caches a lightweight manager whose queryset filters `is_active=True`.
`all_manager()` returns the `all_objects` attribute value only when soft delete
is enabled and the attribute exists; otherwise it returns the default manager.
The selector does not validate that `all_objects` or `cached_active` is a manager
or belongs to `model`. A truthy `database_alias` rebinds the selected source
manager through `manager.db_manager(alias)` on each call, while `None` and `""`
leave the original manager unchanged. The selector caches only the unaliased
generated or caller-supplied active-manager source, not the aliased result. The
generated active manager starts from the model default manager queryset and
preserves aliases applied through `db_manager()`. The selector does not query the
database by itself and has no global cache. Missing or malformed manager
attributes and `db_manager()` failures propagate unchanged at the point the
selector reads or uses them.

`discard_orm_instance_cache(interface_cls, pk)` clears cached ORM reads for one
interface class and primary key inside the active calculation run. It is a no-op
when no calculation run context is active and returns `None`.

`OrmMutationCapability` owns shared write helpers. `assign_simple_attributes()`
sets normalized non-many-to-many values on a Django model instance, skips
`models.NOT_PROVIDED`, wraps assignment `ValueError` as `InvalidFieldValueError`,
and wraps assignment `TypeError` as `InvalidFieldTypeError`. `save_with_history()`
sets `_history_user` for simple-history when the model does not expose
`changed_by`, sets `changed_by_id` when the model has that field, runs
`full_clean()`, saves inside an atomic transaction using the interface database
alias when configured, returns the saved primary key, and applies
`history_comment` with simple-history after the save. `apply_many_to_many()`
expects normalized `<relation>_id_list` entries, strips the suffix to find the
relation manager, calls `.set(values)`, and applies the history comment again
after relation updates.

`OrmCreateCapability.create(interface_cls, **payload)` is the capability-level
implementation behind manager creation. Positional arguments are accepted only
for signature compatibility and are ignored. The payload may contain reserved
metadata keys `creator_id` and `history_comment`; other keys are validated as
model fields/attributes or many-to-many aliases such as `<relation>_id_list`.
The capability removes the metadata keys, validates and normalizes the remaining
payload through the validation/support normalizer, creates the model instance,
saves it, and applies many-to-many updates in one alias-aware transaction before
returning `{"id": pk}`. Validation, save, history-reason, relation, and
transaction errors propagate and roll back that unit. The
GeneralManager layer consumes that result and returns the public manager
instance from `Manager.create(...)`.

`OrmUpdateCapability.update()` loads the target row by `pk` with inactive rows
included, ignores positional arguments, applies the same
normalization/save/many-to-many transaction, discards the run-scoped ORM read
cache only after that transaction succeeds, and returns the capability result
`{"id": pk}`. Inside a caller-owned transaction on the same alias, ORM reads
bypass the run-scoped identity cache so uncommitted rows are not published into
it. After rollback, callers should reconstruct any in-place-updated manager from
its ID. The
GeneralManager layer consumes that result, refreshes the public manager state,
and returns the same manager instance from `manager.update(...)`.

`OrmDeleteCapability.delete()` loads the row with inactive rows included and
ignores positional arguments. It accepts only the reserved metadata keys
`creator_id` and `history_comment`; other keyword arguments are currently
ignored by this capability. With soft delete enabled it requires activation
support, sets `is_active=False`, saves with a deactivation history comment,
clears the read cache, and returns `{"id": pk}`. Without soft delete it sets
history actor metadata, applies a deletion change reason, hard-deletes in an
atomic transaction using the configured database alias, clears the read cache,
and returns `{"id": pk}`. The GeneralManager layer consumes that result and
invalidates the public manager instance for later field reads. Missing
activation support raises `MissingActivationSupportError`; queryset `.get()`,
validation, transaction, delete, many-to-many, history, cache invalidation, and
observability errors propagate unchanged.

`OrmValidationCapability.normalize_payload()` validates payload keys, splits
many-to-many values, normalizes manager-valued foreign keys to identifiers, and
returns `(simple_payload, many_to_many_payload)`. The many-to-many payload keeps
`<relation>_id_list` keys after normalization; `apply_many_to_many()` strips
that suffix before resolving the relation manager. `MutationPayload` is
`dict[str, object]`, `ManyToManyPayload` is `dict[str, list[object]]`, and
`MutationResult` is `dict[str, object]` because primary keys and payload values
may be non-integer and heterogeneous. Unknown fields raise `UnknownFieldError`
from the payload normalizer.

`PayloadNormalizer` is the model-bound helper used by ORM query and mutation
capabilities. `normalize_filter_kwargs(kwargs)` returns a new mapping and
unwraps manager-valued filter values to their underlying ORM instance when one
is cached on the manager interface, otherwise to `identification["id"]`.
Here "manager-valued" means an actual `GeneralManager` instance. If the manager
has malformed identification state, such as a missing `"id"`, the underlying
exception propagates. `validate_keys(kwargs)` accepts model attributes from
`vars(model)` (including descriptors, properties, methods, and other class
attributes), names from `model._meta.get_fields()` (including forward and
reverse Django relations), and many-to-many aliases. The canonical
many-to-many write alias is `<relation>_id_list`; the GraphQL-facing
`<relation>_list` spelling is also accepted only when the base relation is an
actual local many-to-many field. Unknown keys raise `UnknownFieldError`.

`split_many_to_many(kwargs)` mutates and returns the provided mapping: matching
many-to-many aliases are removed from the first returned mapping and placed in
the second mapping under the canonical `<relation>_id_list` key. The first
returned mapping is the same object passed in. Plain fields whose names merely
end in `_list` are left untouched unless their base name is a real many-to-many
relation. If both `<relation>_list` and `<relation>_id_list` are present,
iteration order decides the last canonical value stored in the many-to-many
mapping.

`split_many_to_many_non_mutating(kwargs)` returns new simple and many-to-many
mappings without changing `kwargs`. It uses the same many-to-many alias
recognition, canonical `<relation>_id_list` output keys, plain `_list` field
preservation, and insertion-order collision behavior as the mutating
`split_many_to_many()` helper.

`normalize_simple_values(kwargs)` returns a new mapping. It converts
manager-valued foreign-key/one-to-one values to identifiers and renames
unsuffixed relation keys to `<relation>_id`; raw non-model values for those
forward `ForeignKey` and `OneToOneField` names are also sent to the
`<relation>_id` key so Django receives an identifier assignment. Reverse
relations, generic relations, and many-to-many fields are not renamed by this
method. If both a relation key and its `<relation>_id` key appear, the later
normalized key overwrites the earlier value according to input iteration order.
`normalize_many_values(kwargs)` also returns a new mapping and preserves input
keys exactly. It skips `None` and `models.NOT_PROVIDED`, treats strings and
bytes as scalar one-item assignments, expands other iterables, consumes
generators once, treats dictionaries as iterables over their keys, and resolves
manager-like items to `identification["id"]` while preserving unrecognized
values. Call `split_many_to_many()` or `split_many_to_many_non_mutating()` first
when canonical many-to-many keys are required.

`SoftDeleteCapability` stores the effective soft-delete state for an interface.
During setup it prefers `_soft_delete_default`, then
`_model._meta.use_soft_delete`, and otherwise keeps the capability's configured
default. `is_soft_delete_enabled()` checks the capability first, then model
metadata, then `_soft_delete_default`.

The public custom-field helper exposed through `OrmInterfaceBase` returns two
lists: discovered Django field names and ignore markers for the generated
backing attributes, currently `<field>_value` and `<field>_unit`. Rules attached
through `Meta.rules` are installed on the generated model metadata and used by
the generated `full_clean()` hook. Lifecycle errors are intentionally not
wrapped and GeneralManager does not promise stable lifecycle wrapper exception
types here: invalid Django fields, invalid model bases, invalid nested `Meta`
attributes, factory class construction errors, rule setup errors,
custom-field-discovery errors, and support-capability lookup errors propagate
from Django, factory_boy, rule construction, custom field code, or the support
capability that raised them.

`OrmInterfaceBase._from_trusted_orm_instance()` is internal even though it is
visible in generated API reference pages. It is only for framework-owned rows
loaded by the interface's Django ORM query paths, not for API, GraphQL, import,
factory, or other user payloads. When a subclass overrides `__init__`, trusted
hydration calls that constructor as `cls(pk)` or `cls(pk, search_date=...)`.
For subclasses using the base initializer, the hook bypasses construction and
installs the trusted row, `{"id": pk}` identification, primary key, and
normalized search date directly.

::: general_manager.interface.capabilities.read_only

The `general_manager.interface.capabilities.read_only` package is the supported
package-level import surface for read-only capability classes:
`ReadOnlyLifecycleCapability` and `ReadOnlyManagementCapability`. It also
exposes `ReadOnlyLogger`, `ReadOnlyObservabilityHook`,
`ReadOnlyEnsureSchemaOperation`, `ReadOnlySyncDataOperation`,
`ReadOnlyObservabilityOperation`, `ReadOnlyEnsureSchemaObservabilityEvent`,
`ReadOnlySyncObservabilityEvent`, `ReadOnlyObservabilityPayload`,
`ReadOnlyObservabilityTarget`, `ReadOnlySchemaObservabilityPayload`,
`ReadOnlySyncObservabilityPayload`, `logger`, and `with_observability` through
`__all__` as patch points and type helpers for tests or advanced
instrumentation. The private `_compat.call_with_observability()` helper exists
only to make read-only management resolve the package-level hook at call time;
import `with_observability` from the package instead.

`ReadOnlyLifecycleCapability` is the lifecycle hook behind
`ReadOnlyInterface`. During class creation it adds a nested `Meta` class when
needed, overwrites `Meta.use_soft_delete = True` even when a subclass set it to
`False`, ignores any caller-supplied model base, and delegates model generation
to the ORM lifecycle with `GeneralManagerBasisModel`. The class-creation
namespace is passed through the ORM lifecycle, which mutates and returns it with
the generated interface, factory, and interface-type entries. After class
creation it runs the ORM post-create linking first, then registers the new
manager in `GeneralManagerMeta.read_only_classes` when that exact class object
is not already present, so startup hooks can find it for schema checks and
synchronization. If the upstream lifecycle supplies `model=None`, ORM linking
returns without attaching model state, but the read-only class is still
registered. Lifecycle exceptions are not wrapped: invalid Django fields or Meta
options, factory or rule construction errors, custom-field discovery failures,
support-capability lookup failures, and ORM manager setup errors propagate from
the component that raised them.

`logger` is a `GeneralManagerLoggerAdapter`. Test doubles can follow
`ReadOnlyLogger`: a minimal protocol with `debug()`, `info()`, and `warning()`
methods that accept a message, positional logging arguments, and an optional
`context` mapping keyword. Read-only management resolves
`general_manager.interface.capabilities.read_only.logger` at log time, so
patching that package-level name affects subsequent schema and sync logs.

`ReadOnlyObservabilityHook` is the replacement protocol for
`with_observability`: a synchronous callable accepting a `target`, keyword-only
`operation`, `payload`, and `func`, and returning the result of `func`.
`ReadOnlyObservabilityTarget` is `type[object]`; read-only management passes the
read-only interface class, and the alias stays broad so dynamically generated
interfaces and test doubles are accepted without importing ORM base classes into
the package surface. Production replacement hooks should call `func` exactly
once and return its result. Test hooks may intentionally skip, repeat, or fail
the operation to simulate edge cases. The default hook creates one shallow copy
of `payload` for the event and passes that same copy to every callback. It calls
`before_operation` before `func`; if `before_operation` raises, `func` is not
called. It calls `on_error` only when `func` raises; if `on_error` raises, that
exception replaces the original `func` exception. It calls `after_operation`
only after `func` succeeds; if `after_operation` raises, that exception replaces
the successful return. Otherwise the hook returns `func`'s result. A patched hook
receives the management payload dictionary directly. It may mutate that
per-event dictionary during the call, but should not retain it for later
mutation; read-only management treats the payload as disposable after the hook
returns and does not read it again. Async functions are not awaited by this
wrapper; an awaitable returned by `func` is returned as-is. Read-only management
resolves the hook from the package at call time, so patching
`general_manager.interface.capabilities.read_only.with_observability` affects
subsequent schema-check and sync calls.

Default observability callbacks have the same keyword shape used by the shared
observability capability: `before_operation(operation=..., target=...,
payload=...)`, `after_operation(operation=..., target=..., payload=...,
result=...)`, and `on_error(operation=..., target=..., payload=...,
error=...)`.

`general_manager.interface.capabilities.core.with_observability(target,
operation=..., payload=..., func=...)` is the shared wrapper used by capability
packages. It accepts any mapping-shaped payload, copies it once to a dict for
hook calls, and executes `func` directly when the target has no
`get_capability_handler()` method or no observability capability. When a
capability is present, it calls non-`None` optional hooks only: it calls
`before_operation` before `func`, calls
`on_error` only if `func` raises, re-raises the original exception unless the
error hook raises, and calls `after_operation` only after successful completion.
If `before_operation` raises, `func` is not called. Hook exceptions are not
wrapped.

`LoggingObservabilityCapability` is registered as the `"observability"`
capability and writes structured debug/error log events for interface
operations through the `"interface.observability"` logger. The start, end, and
error event messages are `"interface operation start"`,
`"interface operation end"`, and `"interface operation error"`. Each call passes
the structured metadata as the logger keyword argument `context=...`. Context
contains `operation`, the target name (`target.__name__` when that attribute is
a string, otherwise the target class name; `AttributeError` from `__name__`
lookup is treated as missing), sorted `payload_keys`, and selected payload
metadata keys: `service`, `method`, `path`, `status_code`,
`retry_count`, and `request_id`. Selected payload keys are included whenever the
key is present, including when the value is `None`. End events add
`result_type` as the simple runtime type name and copy `status_code`,
`retry_count`, and `request_id` from `result.metadata` when that metadata is a
`collections.abc.Mapping`; missing metadata attributes and non-mapping metadata
values are ignored, and `AttributeError` from `metadata` lookup is treated as
missing metadata. Result metadata keys are included whenever present, including
`None`, and replace same-named payload values in the end-event context. Error
events add `error=repr(error)`,
`error_class=type(error).__name__`, and `status_code` from `error.status_code`
when that attribute exists, including when the value is `None`; missing
`status_code` and `AttributeError` from `status_code` lookup are ignored. Logger
construction, non-`AttributeError` target-name lookup failures, payload-key
sorting, payload/result metadata access, non-`AttributeError` error status
lookup failures, and logger call errors propagate unchanged.

Calculation capabilities use the same package-level replacement pattern for
`general_manager.interface.capabilities.calculation.with_observability`.
Query operations resolve that hook at call time with the calculation interface
class as `target`, a payload dictionary, and one of these operation labels:
`"calculation.query.filter"` with `{"kwargs": dict(kwargs)}`,
`"calculation.query.exclude"` with `{"kwargs": dict(kwargs)}`, or
`"calculation.query.all"` with `{}`. Lifecycle hooks emit
`"calculation.pre_create"` with the user-declared interface class as `target`
and `{"interface": interface.__name__, "name": name}`. They emit
`"calculation.post_create"` with the generated interface class as `target` and
`{"interface": interface_class.__name__}`. The default hook copies the payload
before calling callbacks, executes `func` once, returns its result, and
propagates callback or operation exceptions using the callback ordering
described above.

`ReadOnlyObservabilityOperation` is the literal union
`"read_only.ensure_schema" | "read_only.sync_data"`, and
`ReadOnlyObservabilityPayload` is the union of the two payload schemas below.
`ReadOnlyObservabilityHook` overloads pair each operation literal with the
matching payload schema for type checkers. Schema checks emit
`operation="read_only.ensure_schema"` with
`ReadOnlySchemaObservabilityPayload`: `manager` is the manager class name string
and `model` is the Django model class name string. The paired event alias is
`ReadOnlyEnsureSchemaObservabilityEvent`. Data sync emits
`operation="read_only.sync_data"` with `ReadOnlySyncObservabilityPayload`:
`manager` and `model` are the bound class name strings when present, otherwise
`None`, and `schema_validated` is the boolean flag passed to `sync_data()`. The
paired event alias is `ReadOnlySyncObservabilityEvent`.

```python
from collections.abc import Callable
from typing import TypeVar

from general_manager.interface.capabilities.read_only import (
    ReadOnlyObservabilityOperation,
    ReadOnlySchemaObservabilityPayload,
    ReadOnlySyncObservabilityPayload,
)

ResultT = TypeVar("ResultT")


def traced_read_only_hook(
    target: type[object],
    *,
    operation: ReadOnlyObservabilityOperation,
    payload: ReadOnlySchemaObservabilityPayload | ReadOnlySyncObservabilityPayload,
    func: Callable[[], ResultT],
) -> ResultT:
    return func()
```

The `read_only.management` module below is documented as capability reference
material. Its imported Django transaction module is not exported from
`read_only` and is not package-level API. Patching
`general_manager.interface.capabilities.read_only.management.django_transaction`
is a private test escape hatch for replacing database transaction behavior, not
a supported application extension point.

`ReadOnlyManagementCapability.get_unique_fields(model)` inspects Django model
metadata to find the fields used to reconcile incoming `_data` rows with
database rows. It includes fields marked `unique=True`, `unique_together`
entries, and `UniqueConstraint.fields`, excluding the primary key named `id`.
When a unique field is the concrete value column behind a `MeasurementField`,
the method returns the public measurement descriptor name instead. If two
measurement descriptors point at the same concrete value column, it raises
`ValueError`; if the model has no `_meta`, it returns an empty set. The return
value is intentionally a flat set. When several unique declarations exist,
`sync_data()` uses the union of their fields as one composite identity and
requires every field in that union on each payload row.

`ensure_schema_is_up_to_date(interface_cls, manager_cls, model, *,
connection=None)` checks the bound model against the database table before
read-only sync. It returns Django system-check `Warning` objects for missing
model metadata, missing `db_table`, a missing database table, or mismatched
local concrete column names. It compares column presence only: field types,
nullability, defaults, indexes, generated columns, many-to-many through tables,
and inherited fields outside the model's local concrete field list are not
validated. A clean schema returns an empty list. Database introspection errors
and read-only observability hook errors propagate unchanged.

`sync_data(interface_cls, *, connection=None, transaction=None,
integrity_error=None, json_module=None, logger_instance=None,
unique_fields=None, schema_validated=False)` synchronizes the parent manager's
`_data` payload into the read-only model. `_data` may be a JSON string that
decodes to a list or an in-memory list of row dictionaries. Unique fields are
either supplied explicitly or discovered through `get_unique_fields()`. The
sync creates missing rows, updates editable local fields, applies
many-to-many assignments after save, reactivates matched soft-deleted rows, and
sets previously active rows inactive when their unique identifier is absent
from the payload. Omitted editable scalar fields are left unchanged on existing
rows and excluded from create payloads. Omitted many-to-many fields are left
unchanged; a present value of `None` clears the relation and a present list
replaces it. Relation values may be lookup dictionaries; nested dictionaries
are flattened into Django `__` lookups and must match exactly one related row or
`ReadOnlyRelationLookupError` is raised. Many-to-many relation payloads must be
lists of lookup dictionaries or identifiers.

When `schema_validated` is false, `sync_data()` first calls
`ensure_schema_is_up_to_date()` and logs then aborts without writes if warnings
are returned. It also has a conservative fast path that fingerprints the active
database rows and payload rows and skips the transaction when comparable local
non-relation, non-measurement fields already match the payload fields present on
each row. Related read-only interfaces sync before the local transaction, each
through its own `sync_data()` call; recursive cycles are skipped by an
in-progress stack. Public failures include
`MissingReadOnlyBindingError`, `MissingReadOnlyDataError`,
`InvalidReadOnlyDataTypeError`, `InvalidReadOnlyDataFormatError`,
`MissingUniqueFieldError`, `ReadOnlyRelationLookupError`, the configured
integrity error class, database errors, logger errors, and observability hook
errors.

`get_startup_hooks(interface_cls)` returns a single startup callable when the
interface is already bound to a parent manager and model. That callable runs
`sync_data()` and suppresses only `MissingReadOnlyBindingError`, because binding
metadata can become unavailable between hook registration and startup execution.
When the interface is not bound at registration time, it returns an empty tuple
instead of a no-op callable.
`get_system_checks(interface_cls)` returns a callable that delegates to
`ensure_schema_is_up_to_date()` when binding metadata is available and returns
an empty list otherwise; observability and introspection errors propagate from
the returned callable once binding is present.
`get_startup_hook_dependency_resolver(interface_cls)` returns a resolver that
orders read-only startup sync after direct read-only dependencies referenced by
non-auto-created relation fields, including foreign-key, one-to-one, and
many-to-many fields. The resolver inspects the class passed to the resolver;
transitive dependencies and cycle handling are handled by the startup runner.

::: general_manager.interface.capabilities.read_only.management

::: general_manager.interface.capabilities.calculation.lifecycle

`ExistingModelResolutionCapability` is the lifecycle capability behind
`ExistingModelInterface`. `resolve_model(interface_cls)` reads
`interface_cls.model`, accepts either a Django model class or an app-label string
accepted by `django.apps.apps.get_model()` such as `"app_label.ModelName"` or
`settings.AUTH_USER_MODEL`, stores the resolved class on `interface_cls._model`
and `interface_cls.model`, and enables the soft-delete capability by default
when the model exposes `is_active`. `None`, empty strings, unresolved lazy
references, malformed strings, and non-model objects are invalid. Missing model
configuration raises `MissingModelConfigurationError`; invalid strings or
non-model references raise `InvalidModelReferenceError`.

`ensure_history(model, interface_cls=None)` registers database-aware
django-simple-history for an untracked model and includes local many-to-many
field names. A pre-existing tracker is accepted for the default alias. For a
non-default interface alias, its generated history model must have
GeneralManager's database-aware marker; otherwise
`UnsafeHistoryConfigurationError(model_name, interface_name, alias)` is raised.
The exception message is
`<model> must use DatabaseAwareHistoricalRecords before <interface> configures non-default database alias '<alias>'.`.
The records class and exception currently live in implementation modules and are
not stable exports from `general_manager.interface`; this is a compatibility
limitation of 0.63.1, not a promise of those direct import paths. Other
registration errors from django-simple-history propagate.
`apply_rules(interface_cls, model)` appends `interface_cls.Meta.rules` after any
existing `model._meta.rules` and replaces `model.full_clean` with the
GeneralManager rules-aware implementation. If no interface rules are declared,
the method is a no-op. `ensure_history()` is idempotent once simple-history
metadata exists; `apply_rules()` is not a deduplication boundary, so repeated
calls append the interface rules again and repatch `full_clean()`.

`pre_create(name=..., attrs=..., interface=...)` resolves the model, ensures
history, applies rules, creates the concrete interface subclass, resets cached
field descriptors, writes `attrs["_interface_type"]`, `attrs["Interface"]`, and
`attrs["Factory"]`, and returns `(attrs, concrete_interface, model)`. A `Factory`
class supplied on the manager attrs takes precedence over the interface
`Factory`; public attributes from that definition and its nested `Meta` are
copied into the generated `AutoFactory`, while `Meta.model` is always set to the
resolved model. "Public attributes" means directly declared non-dunder
attributes except `Meta`; inherited attributes are not copied, and descriptors,
callables, and annotations present in the prototype `__dict__` are copied as
ordinary values. `pre_create()` repeats resolution, history, rule, and factory
work each time it is called according to those helper contracts.
`post_create(new_class=..., interface_class=..., model=...)` wires the generated
manager class onto the interface and Django model, assigns `new_class.objects`,
and when soft delete is enabled also exposes `new_class.all_objects` from ORM
support with `only_active=False`. If the legacy model does not already expose an
unfiltered `all_objects`, GeneralManager falls back to Django's
`_default_manager`; that fallback mirrors the legacy model's default manager and
is not guaranteed to include inactive rows when the legacy default manager
filters them. A `None` model is a no-op for lifecycle compatibility. Errors from
history registration, rule application, factory construction, class creation,
manager wiring, ORM support lookup, and observability hooks propagate unchanged.

::: general_manager.interface.capabilities.existing_model.resolution

::: general_manager.interface.utils.errors.InvalidFieldValueError

::: general_manager.interface.utils.errors.InvalidFieldTypeError

::: general_manager.interface.utils.errors.UnknownFieldError

::: general_manager.interface.utils.errors.DuplicateFieldNameError

::: general_manager.interface.utils.errors.MissingActivationSupportError

::: general_manager.interface.utils.errors.MissingReadOnlyDataError

::: general_manager.interface.utils.errors.MissingUniqueFieldError

::: general_manager.interface.utils.errors.ReadOnlyRelationLookupError

::: general_manager.interface.utils.errors.InvalidReadOnlyDataFormatError

::: general_manager.interface.utils.errors.InvalidReadOnlyDataTypeError

::: general_manager.interface.utils.errors.MissingReadOnlyBindingError

::: general_manager.interface.utils.errors.MissingModelConfigurationError

::: general_manager.interface.utils.errors.UnsafeHistoryConfigurationError

::: general_manager.interface.utils.errors.InvalidModelReferenceError

`general_manager.interface.utils.errors` contains public exception classes
shared by the interface implementations. Writable ORM create/update paths raise
`UnknownFieldError` for unknown payload fields, `InvalidFieldValueError` when
field assignment raises `ValueError`, and `InvalidFieldTypeError` when
assignment raises `TypeError`. Descriptor generation raises
`DuplicateFieldNameError` when generated attribute names collide, and soft
delete raises `MissingActivationSupportError` when an active/inactive model
does not expose `is_active`.

Read-only synchronization raises `MissingReadOnlyBindingError` before lifecycle
binding, `MissingReadOnlyDataError` when the parent manager does not provide
`_data`, `InvalidReadOnlyDataTypeError` when `_data` is neither a JSON string
nor a list, `InvalidReadOnlyDataFormatError` for malformed decoded payloads,
many-to-many values, or missing required unique fields,
`MissingUniqueFieldError` when row identity cannot be determined, and
`ReadOnlyRelationLookupError` when a relation lookup dictionary resolves zero
or multiple rows. Existing-model resolution raises
`MissingModelConfigurationError` for a missing `model` declaration and
`InvalidModelReferenceError` for unresolved or non-model declarations. These
exceptions preserve their constructor inputs only in the formatted message; they
do not expose additional public attributes.

The message formats are stable:

- `InvalidFieldValueError(field_name, value)`: `Invalid value for {field_name}: {value}.`
- `InvalidFieldTypeError(field_name, error)`: `Type error for {field_name}: {error}.`
- `UnknownFieldError(field_name, model_name)`: `{field_name} does not exist in {model_name}.`
- `DuplicateFieldNameError()`: `Field name already exists.`
- `MissingActivationSupportError(model_name)`: `{model_name} must define an 'is_active' attribute.`
- `MissingReadOnlyDataError(interface_name)`: `ReadOnlyInterface '{interface_name}' must define a '_data' attribute.`
- `MissingUniqueFieldError(interface_name)`: `ReadOnlyInterface '{interface_name}' must declare at least one unique field.`
- `ReadOnlyRelationLookupError(interface_name, field_name, matches, lookup)`: `ReadOnlyInterface '{interface_name}' could not resolve relation '{field_name}' (expected 1 match, found {matches}) for lookup {lookup!r}.`
- `InvalidReadOnlyDataFormatError()`: `_data JSON must decode to a list of dictionaries.`
- `InvalidReadOnlyDataTypeError()`: `_data must be a JSON string or a list of dictionaries.`
- `MissingReadOnlyBindingError(interface_name)`: `ReadOnlyInterface '{interface_name}' must be bound to a manager and model before syncing.`
- `MissingModelConfigurationError(interface_name)`: `{interface_name} must define a 'model' attribute.`
- `InvalidModelReferenceError(reference)`: `Invalid model reference '{reference}'.`

::: general_manager.interface.utils.database_interface_protocols.SupportsHistoryQuery

::: general_manager.interface.utils.database_interface_protocols.SupportsHistory

::: general_manager.interface.utils.database_interface_protocols.SupportsActivation

::: general_manager.interface.utils.database_interface_protocols.SupportsWrite

The database-interface protocols are structural helper types used by ORM
capabilities. `SupportsHistoryQuery` models the small django-simple-history query
surface GeneralManager calls: `as_of(...)`, `using(...)`, `filter(...)`, and
`last()`. Date cutoffs, lookup values, primary keys, and Django save return
values are typed as `object` because they are forwarded to Django or
django-simple-history without local interpretation. `SupportsHistory`,
`SupportsActivation`, and `SupportsWrite` are runtime-checkable protocols for
history managers, `is_active` soft-delete support, and writable model behavior.

## Request Interfaces

Application authors normally use `RequestField`, `RequestFilter`,
`RequestQueryOperation`, `RequestMutationOperation`, `RequestTransportConfig`,
and the built-in auth/transport helpers. Transport authors usually implement
`SharedRequestTransport.send()` or the hook protocols below.

`RequestQueryOperation` is an alias of `RequestOperation`. Prefer the alias in
read/query declarations for readability; use `RequestMutationOperation` for
create/update/delete declarations.

`RequestQueryPlan` is an alias of `RequestPlan`. It exists for readability and
backward compatibility with earlier query-only request interfaces.

`RequestAction` is the literal action vocabulary used in plans and local
predicates: `all`, `create`, `delete`, `detail`, `exclude`, `filter`, and
`update`.

`RequestLocation` is the literal request-fragment location vocabulary:
`query`, `headers`, `path`, and `body`.

`RequestPayload` is `Mapping[str, object]`; `RequestHeaders` is
`Mapping[str, str]`; `RequestResponse` is one payload mapping or a list of
payload mappings.

`RequestInterface.input_fields` is a `dict[str, Input[type[object]]]`. Request
interfaces usually receive that mapping from the inherited lifecycle machinery;
custom subclasses that override it should provide real `Input` descriptors, not
plain metadata objects.

Request interface subclasses copy request configuration from `Interface.Meta` to
same-named class attributes during subclass creation. Public `Meta` names are
`filters`, `query_operations`, `default_query_operation`, `transport`,
`transport_config`, `auth_provider`, `retry_policy`, `create_operation`,
`update_operation`, `delete_operation`, `create_serializer`, `update_serializer`,
`response_serializer`, and `rules`. Omitted values use the class defaults:
empty filter and query-operation mappings, `default_query_operation="list"`, no
transport/auth/retry/serializer hooks, no mutation operations, and an empty rule
list.

`RequestSerializer` receives one resolved value and returns the serialized
value. `RequestValidator` receives one lookup value and should raise an
exception when the value is invalid. `RequestResponseNormalizer` receives a raw
transport response plus interface, operation, and plan, and must return a
`RequestQueryResult`.

`RequestOperation(name, path, ...)` requires `name` and `path`. Optional
constructor fields are `method="GET"`, `collection=False`, `filters={}`,
`metadata={}`, `static_query_params={}`, `static_headers={}`, `static_body=None`,
and `timeout=None`. For request interfaces, pass `filters=None` when a declared
operation should inherit the interface-level filter mapping; an explicit mapping,
including `{}`, is operation-specific. Operations do not own serializer or
response-normalizer hooks: mutation serializers such as `create_serializer` live
on `Interface.Meta`, and response normalization lives on `RequestTransportConfig`.

Request capability internals use the same public types. Validation requires
fields, a `detail` operation, callable serializers/auth hooks, valid retry
policy settings, valid filter keys, and non-overlapping operation-local filters.
Retry policies require `max_attempts >= 1`, non-negative base backoff, positive
multiplier, bounded jitter, optional max backoff no lower than base backoff, and
idempotency header/factory configured together.
Query capabilities compile object-valued lookup maps into immutable
`RequestQueryPlan` instances, track a `request_query` dependency, and defer
execution to `RequestBucket`. Operation-local filters take precedence for their
operation; inherited interface filters apply only when an operation declares
`filters=None`. Unsupported local-only filters, excludes without remote support
or fallback, invalid fragment locations, and conflicting duplicate fragment keys
raise the documented request planning errors unchanged from the active query
capability; `RequestInterface.query_operation()` does not wrap them. Attribute
reads execute the
`detail` plan only when no payload cache is present, require exactly one returned
item, cache that mapping on the interface instance, and emit
`request.read.detail`. Query execution emits `request.query.execute`; lifecycle
cloning emits `request.pre_create` and `_parent_class` assignment emits
`request.post_create`. Create/update/delete accept
`creator_id` and `history_comment` for manager API compatibility but do not send
those values to the remote service.
`RequestInterface.execute_request_plan()` treats only `create`, `update`, and
`delete` as mutation actions; every other action string resolves a query
operation using `plan.operation_name`. Missing mutation operations raise
`NotImplementedError`, undeclared non-default query operations raise
`UnknownRequestOperationError`, and auth, retry, metrics, trace, transport, and
normalization errors propagate according to the configured transport and
normalizer implementations.

::: general_manager.interface.requests.RequestField

::: general_manager.interface.requests.RequestFilter

::: general_manager.interface.requests.RequestFilterBinding

::: general_manager.interface.requests.RequestPlanFragment

::: general_manager.interface.requests.RequestOperation

::: general_manager.interface.requests.RequestMutationOperation

::: general_manager.interface.requests.RequestPlan

::: general_manager.interface.requests.RequestQueryResult

::: general_manager.interface.requests.SharedRequestTransport

::: general_manager.interface.requests.UrllibRequestTransport

::: general_manager.interface.requests.RequestTransportConfig

::: general_manager.interface.requests.RequestRetryPolicy

::: general_manager.interface.requests.RequestTransportRequest

::: general_manager.interface.requests.RequestTransportResponse

::: general_manager.interface.requests.RequestAuthProvider

::: general_manager.interface.requests.BearerTokenAuthProvider

::: general_manager.interface.requests.HeaderApiKeyAuthProvider

::: general_manager.interface.requests.QueryApiKeyAuthProvider

::: general_manager.interface.requests.BasicAuthProvider

::: general_manager.interface.requests.FieldMappingSerializer

::: general_manager.interface.requests.RequestMetricsBackend

::: general_manager.interface.requests.NoopRequestMetricsBackend

::: general_manager.interface.requests.RequestTraceBackend

::: general_manager.interface.requests.NoopRequestTraceBackend

## Request Errors

::: general_manager.interface.requests.RequestRemoteError

::: general_manager.interface.requests.RequestTransportError

::: general_manager.interface.requests.RequestTransportStatusError

::: general_manager.interface.requests.RequestAuthenticationError

::: general_manager.interface.requests.RequestAuthorizationError

::: general_manager.interface.requests.RequestNotFoundError

::: general_manager.interface.requests.RequestConflictError

::: general_manager.interface.requests.RequestRateLimitedError

::: general_manager.interface.requests.RequestSchemaError

::: general_manager.interface.requests.RequestPlanConflictError

::: general_manager.interface.requests.MissingRequestPayloadFieldError

::: general_manager.interface.requests.RequestServerError

## Interface Infrastructure

::: general_manager.interface.infrastructure.startup_hooks.register_startup_hook

::: general_manager.interface.infrastructure.startup_hooks.iter_interface_startup_hooks

::: general_manager.interface.infrastructure.startup_hooks.registered_startup_hooks

::: general_manager.interface.infrastructure.startup_hooks.registered_startup_hook_entries

::: general_manager.interface.infrastructure.startup_hooks.clear_startup_hooks

::: general_manager.interface.infrastructure.startup_hooks.order_interfaces_by_dependency

::: general_manager.interface.infrastructure.system_checks.register_system_check

::: general_manager.interface.infrastructure.system_checks.iter_interface_system_checks

::: general_manager.interface.infrastructure.system_checks.registered_system_checks

::: general_manager.interface.infrastructure.system_checks.clear_system_checks
