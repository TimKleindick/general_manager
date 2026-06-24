# Core API

::: general_manager.manager.general_manager.GeneralManager

`GeneralManager(*args, **kwargs)` constructs the configured `Interface`, stores
its `identification` mapping, and tracks an identification dependency. Public
constructor inputs are the interface inputs for that manager; constructor
validation and input-specific errors come from the interface. `identification`
returns the stored mapping, and `str(manager)`/`repr(manager)` render
`ClassName(**identification)`. Iterating a manager yields declared attributes and
GraphQL/property values after checking that the manager state is still valid.
Callable entries in `_attributes` are invoked with `self._interface`; the
synthetic `history` property and generated relation descriptors are skipped.
Managers start valid after construction or trusted hydration.
`_reload_interface_state()` reconstructs `Interface(**identification)`, marks
the manager valid, and clears the invalidation reason. `_invalidate_manager_state(reason)`
marks the manager invalid and stores the reason. Reading or mutating a manager
after `delete()` raises `InvalidManagerStateError`.

`GeneralManager.create(creator_id=None, history_comment=None,
ignore_permission=False, **fields)` checks create permission unless
`ignore_permission` is true, delegates to `Interface.create(...)`, logs the
created identification, and returns a new manager constructed from that
identification. `manager.update(...)` checks that the manager is still valid,
checks update permission unless skipped, delegates to the interface, reloads the
backing interface in place, preserves request payload cache when available, and
returns the same manager instance. `manager.delete(...)` checks delete
permission unless skipped, delegates to the interface, invalidates the manager
for later field reads, and returns `None`.

`GeneralManager.filter(**lookups)` and `exclude(**lookups)` forward lookup
expressions to the interface and return `Bucket[Self]`. Lookup values may be
manager instances or lists/tuples containing manager instances; those values are
replaced with their scalar `identification["id"]` value for normal single-id
managers, or with a copied identification mapping for composite identifiers,
before the interface call. Scalar normalization applies only when the
identification mapping has exactly the one key `"id"`. Empty mappings, single-key
non-`"id"` mappings, and multi-key mappings are forwarded as copied
identification mappings. Normalization is shallow: only top-level lookup values
and direct list/tuple items are inspected. Nested containers and non-manager
values are forwarded unchanged. Calls without manager values delegate the
original lookup mapping. These methods return the interface result typed as
`Bucket[Self]` and do not wrap identification access, mapping-copy, interface,
or bucket errors.
`GeneralManager.get(**lookups)` is `filter(**lookups).get()` and preserves the
bucket's single-item behavior. `GeneralManager.all()` returns
`Interface.filter()` as a bucket of the concrete manager class.

`manager.history` delegates to the configured history capability's
`get_history_queryset_for_manager(Interface, manager)` and returns that queryset
object. Missing history capability support raises `HistoryNotSupportedError`.
Late descriptor fallback calls
`GeneralManagerMeta.ensure_attributes_initialized(cls, name)`; when it returns
true, the attribute is read normally, and otherwise `AttributeError(name)` is
raised.

`GeneralManager.__or__(other)` combines a manager instance with either a
compatible `Bucket` or another manager instance of the exact same class. Bucket
operands handle the union themselves; same-class manager operands call
`filter(id__in=[left.identification, right.identification])`. Other operands
raise `UnsupportedUnionOperandError`.

`GeneralManager._from_trusted_orm_instance(row, *, search_date=None)` is an
internal trusted hydration hook for framework-owned ORM rows. It bypasses public
input validation and should only be used with Django rows already loaded by the
owning ORM interface. Managers that use the base constructor hydrate by calling
the interface's trusted ORM hook directly, store the hydrated interface and its
identification, mark state valid, and track the identification dependency.
Managers with a custom `__init__` are
reconstructed with `cls(row.pk)` or `cls(row.pk, search_date=search_date)` so
their custom construction contract still runs. ORM interfaces normalize
`search_date` while building the trusted interface or constructor path.
Interfaces without a trusted hydration hook raise
`TrustedOrmHydrationNotSupportedError`.

`UnsupportedUnionOperandError(type_)` renders
`Unsupported type for union: {type_}.`.
`TrustedOrmHydrationNotSupportedError(interface_name)` renders
`{interface_name} does not support trusted ORM hydration.`.

::: general_manager.manager.general_manager.UnsupportedUnionOperandError

::: general_manager.manager.general_manager.TrustedOrmHydrationNotSupportedError

::: general_manager.utils.public_api.MissingExportError

Package-level imports such as `from general_manager import GeneralManager` and
`from general_manager.interface import DatabaseInterface` are resolved lazily
from the public API registry. A package `__getattr__(name)` returns the resolved
export object, caches it in that package module, and raises `MissingExportError`
for names not listed in that package's `__all__`. `dir(package)` includes both
currently loaded module globals and registered lazy exports. Successful lazy
resolutions are debug-logged with module, export, target module, and target
attribute context. Missing public exports are warning-logged with module and
export context.

`general_manager.models` is the root Django models module for this app. It
re-exports `SearchIndexState`, `WorkflowEventRecord`, `WorkflowOutbox`,
`WorkflowExecutionRecord`, and `WorkflowDeliveryAttempt` from their canonical
search/workflow modules for Django discovery and stable root-module imports.
The module defines no helper callables, accepts no application input, returns no
application output, and does not wrap import or Django app-registry errors.
Detailed field and model behavior is documented in the Search and Workflow API
pages.

::: general_manager.apps.GeneralmanagerConfig

`GeneralmanagerConfig.ready()` is the Django startup entry point for the
package. It installs the management-command startup-hook runner and registers
system checks before importing optional `<app>.managers` modules and
initializing pending manager classes. Installing the runner does not execute
registered startup hooks immediately; the patched management-command runner
executes them before supported commands run. After manager initialization,
`ready()` registers remote APIs, configures audit logging, search, workflow,
search reconciliation, and GraphQL warm-up schedules, and builds the GraphQL
schema only when `AUTOCREATE_GRAPHQL` is enabled. Startup failures from manager
imports, initialization, settings-backed configurators, remote API wiring, or
GraphQL bootstrap propagate through Django's app-loading path.

The static methods on `GeneralmanagerConfig` are compatibility wrappers around
`general_manager.bootstrap` helpers. They keep older imports and tests working:
`install_startup_hook_runner()`, `register_system_checks()`,
`initialize_general_manager_classes(...)`, `check_permission_class(...)`,
`handle_graph_ql(...)`, `handle_remote_api(...)`, `add_graphql_url(schema)`, and
`_ensure_asgi_subscription_route(graphql_url)`. `add_graphql_url(schema)`
requires a Graphene schema and raises `MissingRootUrlconfError` when Django has
no `ROOT_URLCONF`. Other wrapper errors propagate from the bootstrap helper they
call. `register_system_checks()` is process-idempotent by module-qualified
interface class identity, so repeated app setup skips already-registered hooks
without suppressing different interface classes that share the same bare class
name.

::: general_manager.manager.meta.GeneralManagerMeta

`GeneralManagerMeta` is the metaclass behind every `GeneralManager` subclass.
When a class body directly declares an `Interface` key in `attrs`, the metaclass
validates that it is an `InterfaceBase` subclass, calls
`interface.handle_interface()` on that class object, then calls the returned
lifecycle pre-creation hook with `(name, attrs, interface)` and expects
`(attrs, interface_cls, model)` back. Inherited `Interface` attributes are not
treated as declarations by this class-creation path; subclasses that should be
registered managers must declare their own `Interface` class body entry.
`InterfaceBase` itself passes the subclass check, but its default lifecycle path
raises `NotImplementedError` unless a lifecycle capability or override supplies
hooks.
`InterfaceBase.handle_interface()` is a classmethod; concrete interfaces may
inherit its capability-driven implementation or override it. It must return
`(pre_creation, post_creation)` callables. `pre_creation(name, attrs, interface)`
returns `(attrs, interface_cls, model)`: `attrs` is a `dict[str, object]`
namespace passed to `type.__new__`, `interface_cls` is a `type[InterfaceBase]`
used for post-creation and capability selection, and `model` is a Django
`Model` subclass or `None`. The metaclass does not separately assign
`new_class.Interface = interface_cls`; the returned `attrs` mapping must contain
the final `"Interface"` entry when the created class should expose that
interface. `post_creation(new_class, interface_cls, model)` returns `None`.
`model` is lifecycle pass-through owned by the interface capability; the
metaclass does not store or validate it except by passing it to `post_creation`.
Return values are not type-validated beyond tuple unpacking and the later calls
that consume them. After class creation the metaclass calls the post hook,
builds capability selection from `ManifestCapabilityBuilder.build(interface_cls)`,
stores it with `interface_cls.set_capability_selection(selection)` for later
capability-handler lookup, and appends the new class to `all_classes` and
`pending_attribute_initialization`. Capability selection is the interface
capability manifest chosen for the returned interface class. Invalid interface
declarations raise `InvalidInterfaceTypeError`. Malformed hook call signatures,
invalid class namespace values passed to `type.__new__`, invalid returned
interface classes consumed by capability setup, and descriptor/class creation
operations may raise `TypeError`; malformed lifecycle hook return unpacking may
raise `ValueError`; unsupported lifecycle hooks raise `NotImplementedError`;
other lifecycle hook errors, capability-selection errors, and selection-storage
errors propagate from their source. Classes without an `Interface` are still
created normally but are not added to the manager registries. When
`AUTOCREATE_GRAPHQL` is enabled,
`pending_graphql_interfaces` append happens after class creation and, for
interface-backed managers, after pre-hook, post-hook, capability selection,
`pending_attribute_initialization`, and `all_classes` registration. Every class
created by the metaclass is appended, including plain classes without an
`Interface`; downstream GraphQL bootstrap owns later filtering or failure
behavior. If class creation or any interface-backed setup step raises before the
settings check, `pending_graphql_interfaces` is not appended.
`read_only_classes` is a sibling registry populated by the read-only
lifecycle capability, not by `GeneralManagerMeta.__new__`; read-only lifecycle
creation appends generated read-only manager classes there and normal tests or
bootstrap cleanup may replace the list when restoring process state.

`ensure_attributes_initialized(manager_class, attribute_name=None)` installs
descriptor-backed fields from `manager_class.Interface.get_attributes()`.
Supplying `attribute_name` limits the success condition to that declared field:
unknown names, managers without an `Interface`, interfaces without
`get_attributes`, and interfaces whose read attributes raise
`NotImplementedError` return `False`. Other errors from `get_attributes()`
propagate. Probing an unknown public name may call `get_attributes()`, but it
does not cache attributes or install descriptors unless the name is declared.
Successful initialization stores the `dict[str, object]` interface attribute
mapping on the class-level `manager_class._attributes`, creates descriptors for
all declared fields, and removes the class from
`pending_attribute_initialization` when present. Mapping key order is preserved
when descriptors are installed. Empty mappings count as successful
initialization when no specific `attribute_name` was requested. Non-string keys
are not validated before descriptor installation; if `setattr()` rejects a key,
the original exception propagates, `_attributes` may already be cached,
descriptors from earlier keys may already be installed, and pending-initializer
removal is skipped because execution stops before that step. A later
`ensure_attributes_initialized(manager_class)` call sees the cached
`_attributes` mapping and retries descriptor creation for all keys; a targeted
call for a declared key also retries descriptor creation for all keys before
returning `True`. Manager instances also use `instance._attributes` for
resolved values; the shared attribute name is intentional compatibility
behavior.

Generated descriptors return `Interface.get_field_type(name)` when read on the
class. When read on an instance, they first call
`ensure_manager_is_valid(instance, name)`, then read
`instance._attributes[name]`. Callable attribute values are invoked with
`instance._interface` and are always treated as deferred evaluators; literal
callables must be wrapped in a non-callable container or exposed by a custom
descriptor path. Callable failures are wrapped in `AttributeEvaluationError`
whose message starts with `Error calling attribute {name}:`, with the original
exception chained as `__cause__`. Missing stored keys raise
`MissingAttributeError`, but missing `instance._attributes` or
`instance._interface` raises normal `AttributeError`. Invalidated managers raise
`InvalidManagerStateError`, and class-level field type lookup errors propagate
from the interface. Missing `_manager_state_valid` is treated as valid; a
missing invalidation reason falls back to `manager state is invalid`. Invalid
attribute reads render
`Cannot access attribute {attribute_name!r} on invalidated {ManagerName}: {reason}.`;
whole-manager checks render
`Cannot access invalidated {ManagerName}: {reason}.`.

`GeneralManagerMeta.__getattribute__` performs lazy descriptor installation
before class attribute reads whose names do not start with `_`, excluding
`Interface`, so a declared field can override inherited manager methods such as
`create`. `__getattr__` performs the same lazy installation for genuinely
missing class attributes and raises `AttributeError(name)` when the name is not
an interface-backed field. Descriptor installation overwrites existing
attributes with the same names, including explicit class attributes and
inherited methods. Generated descriptors implement only `__get__`: assigning the
same name on the class replaces the descriptor, assigning on an instance follows
normal non-data-descriptor shadowing rules, and descriptor reads do not cache
resolved values. Duplicate names are processed in order, so later duplicates
overwrite earlier descriptors. Non-string names or attribute iterables that fail
partway through propagate their original exception and may leave descriptors for
earlier names installed. A present but malformed `_interface` is passed to
callable attribute values unchanged; errors raised by that callable are wrapped
as `AttributeEvaluationError`. Normal `GeneralManager` construction creates
`instance._interface` from `self.Interface(*args, **kwargs)`, and trusted ORM
hydration creates it through the interface's trusted hydration hook. The
descriptor does not check that the current `instance._interface` is an instance
of `new_class.Interface`; it passes the value currently stored on the instance.

The class registries are process-local mutable lists used by bootstrap, search,
GraphQL, tests, and read-only lifecycle wiring. Application code should treat
them as framework-owned; replacement or clearing is tolerated for tests and
advanced bootstrap integrations that restore process state afterward. Class
creation appends without deduplication. Descriptor initialization holds one
metaclass-level lock around checking/caching `_attributes`, calling
`get_attributes()`, installing descriptors, and removing the class from
`pending_attribute_initialization`; under that lock, `get_attributes()` is called
at most once for a class whose attributes are successfully cached. Other
registry mutation is not locked. Concurrent external clearing or replacement of
the registry lists while classes are being created or descriptors are being
initialized has no stronger guarantee than normal Python list assignment and
mutation semantics.

An empty attribute mapping means the interface has no descriptor-backed fields
to install. That still counts as initialized for bulk startup work because the
class no longer needs pending descriptor processing; targeted initialization for
a named field still returns `False` when the name is absent.

::: general_manager.manager.group_manager.GroupManager

`GroupManager` is the per-group object yielded by `GroupBucket`. Attribute
access first returns group-by key values, then lazily aggregates values from the
group's underlying bucket and caches the result. Cached aggregates are not
invalidated if the underlying bucket or group-key mapping is mutated later; they
are stored in the private `_grouped_data` dictionary under the requested
attribute name.
Iteration yields keys from `manager_class.Interface.get_attributes()` first in
that mapping's order, then `GraphQLProperty` values declared directly on the
manager class in class-`__dict__` order; duplicate names are not filtered.

`id`, empty buckets, and all-`None` inputs aggregate to `None`. Bucket and
manager values are combined with `|`, lists are concatenated, dicts are merged
with later values overwriting earlier keys, strings are de-duplicated in
encounter order and joined by `", "`, booleans use `any()` before numeric
handling, numeric and `Measurement` values are summed, and date/time values use
`max()`. The aggregation branch is chosen from interface metadata or a concrete
`GraphQLProperty` return annotation, not from every runtime value, so mixed
runtime values follow the selected branch and may raise from that operation.
GraphQL property annotations use the first `typing.get_args()` entry when
present, otherwise the annotation object itself; unsupported non-class
annotations raise `MissingGroupAttributeError`. `hash(group)` recursively
freezes manager instances, mappings, lists, tuples, and sets before hashing.
Mapping entries are sorted by their frozen key/value tuples, sets become
`frozenset` values, and the final hash is suitable only for unchanged
in-process group state, not as a persistent cross-process identifier. Missing
metadata or unsupported return annotations raise `MissingGroupAttributeError`;
errors from bucket iteration, attribute access, unioning, summing, merging, or
comparison propagate unchanged.

::: general_manager.manager.input.Input

`Input(type, possible_values=None, depends_on=None, *, required=True,
min_value=None, max_value=None, validator=None, normalizer=None)` is the public
descriptor used on calculation and custom interfaces. It stores the expected
Python class, optional static or callable allowed values, scalar bounds,
dependency names, and optional validation/normalization callbacks. Construction
does not check that static `possible_values` match `type`; value casting and
interface validation perform the runtime checks. Unless this section says a
detail belongs to an owning interface, the behavior described here is the public
contract for the exported `Input` and domain classes. Supported `type` values are
Python class objects that `issubclass(type, GeneralManager)` and
`isinstance(value, type)` can inspect. Manager-typed inputs declare a
`GeneralManager` subclass; manager instances are instances of that declared
manager class.

`Input.cast(value, identification=None, *, cache_context=None)` converts raw
values to the configured type and then normalizes them. `None` is returned
unchanged. `date` and `datetime` inputs accept native values and ISO strings;
`date` inputs convert native `datetime` values to `.date()`, while `datetime`
inputs convert native `date` values with `datetime.combine(value,
datetime.min.time())`. ISO strings are parsed by Python's
`date.fromisoformat()` or `datetime.fromisoformat()` exactly, including their
normal timezone-aware datetime behavior. Manager-typed inputs accept either a
mapping used as constructor keywords or a single value passed as `id`; existing
manager instances skip construction but still pass through normalization. The id
path constructs `manager_type(id=value)` and does not perform a query by itself.
Mappings, including mappings with an `id` key, are passed as
`manager_type(**mapping)`; invalid keywords and manager constructors that do not
accept `id` fail through the constructor's normal exception.
For every other type, already-matching values pass through normalization and
non-matching values are converted with `type(value)`. Constructor errors
propagate unchanged. `Measurement` inputs accept the strings documented for
`Measurement.from_string()`.
`cast()` does not apply scalar bounds, possible-value membership checks, or the
validator callback; interface validation calls `validate_bounds()`, membership
validation, and `validate_with_callable()` separately. Conversion `ValueError`s,
constructor/callback `TypeError`s, and missing dependency `KeyError`s propagate.
`identification` is the current input-value mapping keyed by dependency input
name; `Input` only reads the dependency names declared on that input.

Callable `possible_values`, validators, and normalizers receive only declared
dependency values. Dependency names are supplied explicitly through `depends_on`
or inferred from non-variadic callback parameters. `resolve_possible_values()`
returns static values unchanged, invokes providers for the current dependency
context, and uses run-scoped caching only when a cache context and active
calculation run are present. Cache keys include the owner class, input field
name, and frozen declared dependency values; there is no cross-run invalidation.
Unhashable dependency values skip the run cache. Static possible values are
returned as the original object, not copied or materialized. The cache lives
only for the active `CalculationRunContext`; leaving that run ends the cache
lifetime.

Callback invocation follows each callback's signature. Dependency names are
inferred from callable `possible_values` in `Input(...)`, from `start`/`end`
callbacks in `Input.date_range(...)`, and from callable `query` in
`Input.from_manager_query(...)`. Validator and normalizer callbacks do not infer
dependencies by themselves; pass `depends_on` when they need other input values.
Names such as `value` or `domain` are not reserved during inference. Dependency
values are available as both positional values in declared dependency order and
named keyword values. Positional-only parameters consume positional values;
positional-or-keyword parameters consume positional values first and then named
values; keyword-only parameters consume named values; `*args` receives remaining
positional values; `**kwargs` receives remaining named values. Defaults are left
for Python to apply when no dependency value is supplied for that parameter.
Normalizers also receive the converted value before dependency values and may
accept a `domain` keyword containing the resolved possible values or `None`. For
example, with `depends_on=["project"]`, a validator `lambda value, project: ...`
gets the candidate first and the project second; a normalizer
`lambda value, project, domain=None: ...` gets the converted value, project, and
resolved possible-values object. Avoid naming dependencies `value` or `domain`
for normalizers unless you intentionally want Python-style positional binding to
win over the same keyword name.

`normalize(value, ...)` first applies static `InputDomain` normalization. Dynamic
possible values are resolved only when a custom normalizer exists; if the
resolved object is an `InputDomain`, dynamic-domain normalization then runs
before the custom normalizer. The `domain` keyword receives the resolved
possible-values object itself, including non-domain iterables or buckets, or
`None` when there are no possible values. A callable possible-values provider
that returns an `InputDomain` does not normalize values during `cast()` unless a
custom normalizer is present; this is intentional compatibility behavior.

`validate_bounds(value)` returns `True` for `None` only when `required=False`.
For non-`None` values it compares the value directly with configured scalar
bounds, so incompatible mixed types raise ordinary Python comparison errors.
`validate_with_callable(value, identification=None)` skips `None`, treats a
validator result of `None` as success, and propagates validator exceptions.
Validators receive the candidate value first, followed by declared dependency
values from `identification`. On `Input` itself, `validate_bounds(None)` is the
requiredness check; owning interfaces may combine it with their own input-shape
validation before calling the rest of the pipeline.

`Input.date_range(...)`, `Input.monthly_date(...)`, and
`Input.yearly_date(...)` build date inputs backed by `DateRangeDomain`;
`Input.from_manager_query(...)` builds manager inputs whose possible values come
from `.all()` or `.filter(**query)`. These helpers are stable public API.
`Input` does not define a public membership-validation helper for plain
iterables, buckets, or callable results; that validation belongs to the owning
interface. Static possible values that do not match the declared type therefore
fail only when an owning interface validates membership or a configured
normalizer/domain operation rejects them.

::: general_manager.manager.input.InputDomain

`InputDomain` is the base class for structured possible-value domains. It
provides `contains(value)`, `normalize(value)`, `metadata()`, and iteration.
The base class is intentionally not eagerly iterable and raises
`DomainIterationError`; use concrete domains or custom subclasses for finite
choices. The base `normalize()` is identity, base `metadata()` returns only
`{"kind": kind}`, `contains(value)` catches `TypeError` from membership and
returns `False`, and direct `value in domain` calls `__contains__` and can
propagate `DomainIterationError` from the base iterator or iteration errors from
subclasses.

::: general_manager.manager.input.NumericRangeDomain

`NumericRangeDomain(min_value, max_value, step=1)` represents an inclusive
finite numeric range for `int`, `float`, or `Decimal` values. It raises
`InvalidNumericRangeError` when `step <= 0` or `min_value > max_value`. Float
membership uses `max(1e-12, abs(step) * 1e-9)` tolerance, and decimal membership
uses `max(Decimal("1e-12"), abs(Decimal(str(step))) * Decimal("1e-9"))`, so
stepped ranges such as `0.0` to `0.3` by `0.1` behave predictably. Values inside
the bounds but not on the step sequence are rejected. If any bound, step, or
candidate is a `Decimal`, checks use decimal arithmetic and iteration yields
`Decimal` values. Otherwise, if any bound, step, or candidate is a `float`,
checks use float arithmetic and iteration yields floats. Pure integer ranges
yield integers. `bool` is not a documented numeric input type even though Python
may treat it as an integer at runtime; callers should not rely on bool-specific
behavior as part of this API.

::: general_manager.manager.input.DateRangeDomain

`DateRangeDomain(start, end, *, frequency="day", step=1)` represents an
inclusive finite date range. Supported frequencies are `day`, `week_end`,
`month_start`, `month_end`, `quarter_end`, `year_start`, and `year_end`.
Invalid bounds, non-positive steps, and unsupported frequencies raise
`InvalidDateRangeError`. Membership checks convert `datetime` values to their
date component, normalize non-daily candidates to the configured anchor, and
then require that anchored date to appear in the generated range. `week_end`
anchors to Sunday. Month and year steps advance by calendar months or years from
the current anchored date; quarter steps advance by three-month intervals.
Iteration starts at `normalize(start)` and stops when the next generated date
would be greater than the raw `end`, so unaligned starts may anchor before or
after the supplied start date and unaligned ends act as upper bounds. Examples:
`month_start` normalizes `2024-02-15` to `2024-02-01`; `month_end` normalizes it
to `2024-02-29`; `quarter_end` normalizes it to `2024-03-31`; `year_start`
normalizes it to `2024-01-01`; `year_end` normalizes it to `2024-12-31`.

::: general_manager.bucket.base_bucket.Bucket

::: general_manager.bucket.database_bucket.DatabaseBucket

`DatabaseBucket` is the ORM-backed collection returned by database and
existing-model managers. It keeps Django queryset laziness for builder methods
such as `filter()`, `exclude()`, `all()`, `sort()`, and slicing, then wraps model
rows as the configured manager class when terminal operations evaluate the
query. The constructor accepts a Django `QuerySet`, the manager class, optional
filter/exclude snapshots, optional `search_date`, sort metadata, and the
run-scoped cache flag. Those lookup snapshots are copied into bucket-owned
dictionaries, so mutating the original mappings after construction does not
change the bucket.

Common cookbook patterns:

```python
active = Project.filter(status="active")
first = active.sort("name").first()
total = active.count()

if first is not None and first in active:
    visible_names = [project.name for project in active[0:10]]
```

`count()` and `len(bucket)` return the queryset count unless a safe run-scoped
snapshot already exists. `first()`, `last()`, scalar indexing, iteration, and
membership checks record the effective dependency before reading rows; membership
uses the candidate manager's `identification["id"]` or a model instance's
primary key and returns `False` for unsaved objects. Python-only
`@graph_ql_property` filters and sorts are supported only when the property is
marked filterable or sortable; non-filterable properties raise
`NonFilterablePropertyError`, non-sortable properties raise
`NonSortablePropertyError`, invalid query annotations raise
`InvalidQueryAnnotationTypeError`, and rejected ORM filter/order expressions are
wrapped as `QuerysetFilteringError` or `QuerysetOrderingError`.

Run-scoped reuse is conservative. Database buckets bypass snapshot reuse for
select-for-update, combined, distinct, prefetch-related, deferred-field, invalid,
or oversized queryset shapes. When a safe row snapshot exists, terminal helpers
reuse trusted ORM rows; otherwise they can reuse cached primary keys or evaluate
the queryset normally. Historical buckets pass `search_date` into constructed
managers and only trust ORM rows that expose history state. Pickled bucket
snapshots store primary keys in queryset order, reject duplicate primary keys,
and restore by filtering those primary keys through the original model/database
alias while preserving the snapshot order. `filter()` and `exclude()` copy their
stored lookup snapshots before appending new lookups, and `get()` only answers
cached snapshots for single-key `pk` or `id` lookups. Python-only property sorts
materialize candidate rows, sort them in memory, then preserve the materialized
order with a Django `Case` annotation. Combining database buckets with `|`
requires the same bucket type, manager class, and `search_date`; union buckets
disable run-scoped result reuse. `none()` keeps the same manager, filter/exclude
snapshots, search date, sort metadata, and cache flag while replacing the
queryset with an empty queryset.

::: general_manager.bucket.group_bucket.GroupBucket

`GroupBucket` is the grouped view returned by `bucket.group_by(...)`. It
materializes one `GroupManager` per distinct tuple of group-by values from the
source bucket. Group keys must be strings listed by the manager interface's
attributes; non-string keys raise `InvalidGroupByKeyTypeError`, and unknown keys
raise `UnknownGroupByKeyError`. Group values are frozen into hashable identities
so manager-valued keys compare by manager class plus sorted identification
items, lists and tuples compare by element identities, sets compare as
frozensets, mapping values compare by recursively frozen key/value pairs sorted
by `repr`, and other values use their raw hashable value. Groups are emitted in
`str(group_by_value)` sort order. Equality ignores that order and compares the
set of groups plus manager class and grouping-key tuple. Pickle reconstruction
uses `(GroupBucket, (manager_class, group_by_keys, basis_data))`, rebuilding
groups from the stored basis bucket.

For manager-valued group keys, the source bucket slice is rebuilt with relation
lookups derived from the grouped manager identification. Attribute metadata
`filter_lookup` is used when present; otherwise the group key name is used.
`filter(**lookups)` and `exclude(**lookups)` delegate to the underlying basis
bucket and then rebuild the grouped view. `get(**lookups)` returns the first
matching group after that filtering step and raises `GroupItemNotFoundError`
when no group remains; it does not enforce uniqueness. `first()` and `last()`
return a group or `None`, while `count()` and `len(bucket)` count groups.

Scalar indexing returns a `GroupManager`. Slicing unions the selected groups'
underlying basis buckets and returns a new `GroupBucket`; an empty slice raises
`EmptyGroupBucketSliceError`, and non-int/non-slice indexes raise
`InvalidGroupBucketIndexError`. `all()` returns `self`. `none()` returns a new
empty grouped bucket with the same manager class and grouping keys. Membership
checks test whether the supplied manager instance is present in the underlying
basis bucket, not whether a matching group key exists.

`sort(key, reverse=False)` sorts the current groups in memory by one attribute
name or a tuple of attribute names, returning a new grouped bucket with the same
basis bucket and sorted group list. Missing sort attributes propagate
`AttributeError`, and incomparable values propagate Python `TypeError`. The `|`
operator combines compatible grouped buckets by unioning their basis buckets and
regrouping; operands must be `GroupBucket` instances of the same concrete class,
manager class, and grouping-key tuple. Mismatches raise
`GroupBucketTypeMismatchError`, `GroupBucketManagerMismatchError`, or
`GroupBucketKeysMismatchError`.

::: general_manager.bucket.calculation_bucket.CalculationBucket

`CalculationBucket` is the lazy collection behind calculation-manager
`all()`, `filter()`, and `exclude()`. It stores raw filter/exclude definitions,
parses them against calculation inputs and derived GraphQL properties, and
materializes combinations only when iterated, counted, indexed, or converted to
text. `all()` returns an independent copy. `filter(**kwargs)` and
`exclude(**kwargs)` return new buckets with additional lookup definitions. Keys
use `field` or `field__lookup`; Python-level lookup operators are `exact`, `lt`,
`lte`, `gt`, `gte`, `contains`, `startswith`, `endswith`, and `in`. Manager-typed
inputs can be filtered by the manager object, by `<input>_id`, or by nested
manager lookups such as `project__name__startswith`. Derived GraphQL properties
can be filtered with the same Python lookup operators and do not use the
database-bucket filterable marker. Unknown filter or exclude fields raise
`UnknownInputFieldError` from `general_manager.utils.filter_parser`.

Terminal helpers mirror the other bucket types: `first()` and `last()` return a
manager or `None`, `get(**kwargs)` requires exactly one match and raises
`MissingCalculationMatchError` or `MultipleCalculationMatchError` otherwise,
`count()` and `len(bucket)` count generated combinations, scalar indexing
returns a manager instance, slicing returns a new bucket with cached
combinations, and `none()` returns an empty bucket with the same manager class,
sort key, and reverse flag while clearing raw and parsed filters/excludes.
Sorting accepts one key or a tuple of keys and can sort either raw inputs or
computed properties. Missing sort attributes raise `AttributeError`,
incomparable sort values raise `TypeError`, and computed-property exceptions
propagate unchanged when the bucket materializes. Invalid calculation interfaces raise
`InvalidCalculationInterfaceError`, incompatible bucket unions raise
`IncompatibleBucketTypeError` or `IncompatibleBucketManagerError`, cyclic input
dependencies raise `CyclicDependencyError`, and required inputs without an
iterable or bucket-backed domain raise `InvalidPossibleValuesError`.

The `|` operator is a compatibility merge for calculation buckets, not a
materialized set union. Combining two compatible `CalculationBucket` instances
keeps only filter and exclude definitions that exist with equal values on both
operands, producing a bucket for their common constraints. Passing a same-class
manager instance first converts that instance into an `id__in=[identification]`
filter bucket before applying the same common-constraint merge.

`generate_combinations()`, `topological_sort_inputs()`, `get_possible_values()`,
and `transform_properties_to_input_fields()` are framework helpers. They remain
callable for advanced integrations, but application code should usually use
`all()`, `filter()`, `exclude()`, `sort()`, and terminal helpers instead.
`SortedFilters` is an internal typed partition of parsed filters used during
combination generation and is not a user-facing extension point.

::: general_manager.bucket.request_bucket.RequestBucket

`RequestBucket` is the lazy collection returned by request-backed managers.
`filter()`, `exclude()`, and `all()` preserve the configured request operation
for lazy request-plan buckets, even after iteration caches the fetched items.
Buckets built from concrete items, such as slices, unions, and `none()`, have no
request plan; their follow-up `filter()` and `exclude()` calls validate the same
request lookup rules and then operate on the contained manager instances in
memory. Missing attributes do not match materialized filters. These methods
return `RequestBucket` instances. Materialized `filter()` combines lookup keys
with AND semantics; materialized `exclude()` removes an item when any supplied
lookup matches, so a missing attribute does not exclude that item. Lookup suffix
semantics come from request filters: bare or unknown suffixes are exact matches,
supported suffixes include comparisons, `contains`, `icontains`, `in`, and
`isnull`, and incompatible comparisons return `False`.
The constructor accepts the manager class, request interface class, operation
name, optional request plan, optional filter/exclude lookup maps, optional
serialized manager items, optional raw request payloads, and an optional count
override. Lookup maps are copied into bucket-owned dictionaries. Supplying
`items` creates a concrete manager-item bucket; supplying `raw_items` rebuilds
manager instances from `extract_identification()` and installs each raw payload
as the manager interface's request payload cache.
Lazy `filter()`, `exclude()`, and `all()` compile a new request plan and can
raise the same lookup validation and planning errors as the request query
capability, including unknown or unsupported filters, unsupported exclude
lookups, required local fallbacks, fragment conflicts, and unsupported request
locations. Any method that materializes a lazy bucket, including iteration,
`len()`, `count()`, `first()`, `last()`, `get()`, indexing, slicing, membership,
sorting, equality against a concrete bucket, and concrete follow-up filters, can
propagate request execution, response-shape, permission, and validation errors
from the underlying request interface.

Concrete buckets keep the source `operation_name` as metadata even though they no
longer have a request plan. Unioning request buckets or adding a single manager
instance also produces a concrete item bucket; incompatible bucket types raise
`RequestBucketTypeMismatchError`, and request buckets for different manager
classes raise `RequestBucketManagerMismatchError`.
Only `bucket | other` is implemented here; `manager | bucket` follows the
manager's own union behavior. Union order is left items followed by right items,
and duplicates are not removed. Equality compares manager class and operation
name first; when both sides still have request plans it compares plan plus
compiled filters/excludes, otherwise it materializes and compares the ordered
sequence of each item’s `identification` mapping.
`sort(key, reverse=False)` materializes the bucket, accepts one attribute name or
a tuple of attribute names, and raises `RequestBucketSortAttributeError` when an
item lacks a requested attribute. Tuple keys sort lexicographically by the
resolved attribute values. Nested attribute paths are not parsed; each key part
is passed directly to `getattr`. Python `TypeError` propagates for incomparable
values such as mixed unrelated types.

Pickle-restored buckets keep their operation name and request plan metadata, but
unpickling does not execute a request. Iteration after unpickling uses whatever
items were serialized, and serialized raw payloads are reinstalled on rebuilt
manager instances for field reads. Follow-up `filter()`, `exclude()`, or
`all()` calls on a restored bucket with a request plan compile a new lazy
request bucket. If neither serializable manager items nor raw payloads were
stored, iteration after unpickling yields the empty serialized item set even
when request-plan metadata is present. Normal pickle failures for unserializable
manager instances propagate from Python's pickle machinery.

`get()` requires exactly one result and raises `RequestSingleItemRequiredError`
otherwise. `count()` always materializes first. After materialization the current
count override wins; lazy materialization installs the upstream `total_count`
when the response provides one, installs the local fallback item count when local
predicates are applied, and otherwise falls back to the number of materialized
items. A constructor `count_override` on a still-lazy bucket can therefore be
replaced by the count observed during request execution. Local fallback
predicates reject partial remote pages with
`RequestLocalPaginationUnsupportedError` when the upstream `total_count` does not
match the returned item count.
Slices, unions, and `none()` are concrete buckets whose count override is their
concrete item count; `all()` always returns a new bucket rather than `self`.
`__contains__` delegates to tuple membership over materialized manager objects,
so it uses normal manager equality/identity rather than request lookup
semantics.

::: general_manager.rule.rule.Rule

`BaseRuleHandler.handle(node, left, right, op, var_values, rule)` is called by
`Rule` while explaining a failed predicate. It returns `{variable: message}` and
does not re-run or decide validation. Custom handlers should set
`function_name` to a non-empty string; duplicate names replace earlier
registrations from `RULE_HANDLERS` in order. `RULE_HANDLERS` entries must be
dotted import paths to `BaseRuleHandler` subclasses; import errors,
non-subclasses, invalid `function_name` values, and constructor errors surface
during `Rule` construction.

::: general_manager.rule.handler.BaseRuleHandler

::: general_manager.rule.handler.InvalidFunctionNodeError

::: general_manager.rule.handler.InvalidLenThresholdError

::: general_manager.rule.handler.InvalidNumericThresholdError

::: general_manager.rule.handler.NonEmptyIterableError

::: general_manager.rule.handler.NumericIterableError

::: general_manager.rule.rule.InvalidRuleHandlerConfigurationError
