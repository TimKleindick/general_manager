# Cache API

::: general_manager.cache.cache_decorator.cached

`cached(func=None, timeout=None, cache_backend=django_cache,
record_fn=record_dependencies, *, cache="run")` wraps a callable with one of
four cache strategies. It supports both `@cached` and `@cached(...)` forms and
uses `make_cache_key(function, args, kwargs)` for every cache key.

Use the default `cache="run"` for per-request or per-calculation memoization.
The decorator opens a temporary `CalculationRunContext` when none is active, so
separate calls outside a shared context do not reuse values:

```python
from general_manager.cache.cache_decorator import cached
from general_manager.cache.run_context import CalculationRunContext

@cached
def expensive_total(project_id: int) -> int:
    return Project(id=project_id).derivative_list.count()

with CalculationRunContext():
    first = expensive_total(10)
    second = expensive_total(10)  # Same run-local value.
```

Use `cache="dependency"` when the result should persist across runs and be
invalidated by tracked manager dependencies. On a miss, the wrapped callable runs
inside a `DependencyTracker`; model arguments are also collected after the call.
The value and dependency metadata are published together. If a data-change
generation moves during computation or publication is blocked by an active data
change, the fresh result is returned but no dependency cache entry is published.
Concurrent workers for the same key coordinate with a compute lease; waiters
reuse a published hit when available.

```python
@cached(cache="dependency")
def project_forecast(project_id: int) -> dict[str, int]:
    project = Project(id=project_id)
    return {"rows": project.derivative_list.count()}
```

Use `cache="timeout"` for backend-managed expiry without dependency tracking.
`timeout` is required for this scope and rejected for every other scope. The
decorator validates only whether `timeout` is present; exact timeout ranges and
special values are delegated to the configured backend. Use `cache="none"` to
preserve the decorator shape while disabling cache reads and writes.

Invalid cache names raise `UnsupportedCacheScopeError`. Missing or unexpected
timeouts raise `CacheTimeoutConfigurationError`. Backend `get`/`set` errors,
wrapped-callable errors, dependency tracking errors, compute-lease errors, and
custom `record_fn` errors propagate. `CachePublishAborted` is handled by
returning the freshly computed result without storing a dependency cache entry.

::: general_manager.cache.cache_decorator.CacheBackend

`CacheBackend` is the minimal protocol accepted by `cached`: `get(key,
default)` must return a cached object or the exact `default` value when absent,
and `set(key, value, timeout=None)` stores any backend-serializable Python
object. Return values from `set()` are ignored. Dependency and timeout scopes use
the backend; run and none scopes do not.

::: general_manager.cache.dependency_cache.DependencyCacheEntry

::: general_manager.cache.dependency_cache.DependencyCacheHit

::: general_manager.cache.dependency_cache.make_dependency_cache_entry

::: general_manager.cache.dependency_cache.read_dependency_cache_hit

::: general_manager.cache.dependency_cache.read_many_dependency_cache_hits

::: general_manager.cache.dependency_cache.replay_dependency_cache_hit

The dependency-cache helpers are low-level support for dependency-scoped
`cached` values and GraphQL dependency-cache prefetching. Most application code
should use `@cached(cache="dependency")` or `@graph_ql_property(cache="dependency")`
instead of calling these helpers directly.

`DependencyCacheEntry` is the versioned payload stored at the main cache key for
new dependency-cache entries. `DependencyCacheHit` is the in-memory hit shape
returned by readers; its `value` is the cached function result and
`dependencies` are replayed into active `DependencyTracker` scopes before the
caller returns that value. `make_dependency_cache_entry(value, dependencies)`
freezes the dependency iterable into the current persisted payload format.

`read_dependency_cache_hit(cache_backend, cache_key, sentinel=...)` first reads
the main key. A current-version `DependencyCacheEntry` returns a
`DependencyCacheHit`. A future-version entry is treated as a miss and returns
the supplied sentinel. Plain values are treated as legacy split entries: the
value remains the cached result and `{cache_key}:deps` is read for dependency
metadata, defaulting to an empty dependency set when missing. Falsey cached
values such as `None`, `False`, `0`, `[]`, and `{}` are valid hits as long as
the backend distinguishes absence by returning the exact sentinel/default object.
Legacy dependency payloads must be iterable dependency tuples; a missing
dependency key and other falsey dependency payloads become an empty dependency
set, while truthy non-iterable dependency payloads raise through normal
`frozenset(...)` conversion.

`read_many_dependency_cache_hits(cache_backend, cache_keys)` collapses duplicate
keys while preserving first-seen order. Backends with `get_many()` use one bulk
read for main payloads and one additional bulk read for legacy dependency keys
when legacy entries are present. If a legacy main value is present but its
dependency key is absent from the second bulk read, the hit is returned with an
empty dependency set. Backends without `get_many()` fall back to single-key
reads. Missing main keys and future-version entries are omitted from the
returned mapping. Backend read errors and malformed legacy dependency payloads
propagate.

`replay_dependency_cache_hit(hit)` forwards each dependency tuple to
`DependencyTracker.track()`. Malformed dependency tuple values raise `TypeError`,
and unsupported dependency operations raise `ValueError`, matching the tracker
contract.

::: general_manager.cache.dependency_publish.CachePublishAborted

::: general_manager.cache.dependency_publish.CacheComputeLease

::: general_manager.cache.dependency_publish.PendingDependencyCachePublication

::: general_manager.cache.dependency_publish.acquire_compute_lease

::: general_manager.cache.dependency_publish.release_compute_lease

::: general_manager.cache.dependency_publish.wait_for_cached_dependency_hit

::: general_manager.cache.dependency_publish.publish_dependency_cache_entries

::: general_manager.cache.dependency_publish.publish_dependency_cache_entry

The dependency-publish helpers coordinate dependency-cache misses between
workers and protect cache writes from overlapping data changes. They are
framework support functions; application code should normally use
`@cached(cache="dependency")`.

`acquire_compute_lease(cache_key, timeout=...)` writes a random token to the
coordination cache and returns `CacheComputeLease` when this worker owns the
computation. It returns `None` if another worker already owns the key.
`release_compute_lease(lease)` deletes the lease only when the coordination
cache still contains the same token, so an expired-and-reacquired lease is not
removed accidentally.

`wait_for_cached_dependency_hit(cache_backend, cache_key, timeout_seconds=...,
sentinel=...)` polls with exponential backoff until `read_dependency_cache_hit`
returns a compatible hit or the timeout expires. It returns `DependencyCacheHit`
for published values, including falsey cached results, and returns the exact
sentinel object on timeout. Backend read errors and sleep/clock errors
propagate.

`PendingDependencyCachePublication` is the buffered miss shape used by
`CalculationRunContext`: it carries the cache key, computed result, frozen
dependencies, backend, timeout, generation observed before computation, and
compute lease. The lease is carried for the caller that owns the computation;
the publish helpers do not validate lease ownership and do not release leases.
`publish_dependency_cache_entries(entries)` publishes a batch of current pending
entries. It checks for an active data-change barrier before filtering stale
entries; an active barrier aborts the whole batch. When no barrier is active,
entries from stale generations are skipped. Non-empty dependency sets are
recorded before any cache value becomes visible. If a generation change or
barrier begins after dependency metadata is recorded but before values are
written, `CachePublishAborted` is raised and values are not stored. Backends with
`set_many()` are grouped by backend instance and timeout; failed keys returned by
`set_many()` are retried with individual `set()` calls.

`publish_dependency_cache_entry(...)` performs the same guarded publish for one
value. It checks the generation/barrier before dependency recording and again
before the cache write. A custom `record_many_fn` runs while the dependency-index
lock is held and must not acquire that lock recursively. Empty dependency sets
skip dependency-index recording but still publish the value when the generation
is current. Unlike batch publishing, a stale generation for this one value raises
`CachePublishAborted`. Lock, dependency-index, custom recorder, and cache backend
errors propagate.

::: general_manager.cache.dependency_index.Dependency

`Dependency` is the public tuple shape captured by `DependencyTracker`:
`tuple[str, Literal["filter", "exclude", "identification", "request_query",
"all"], str]`. The tuple slots are the GeneralManager class name that owns the
tracked read, the dependency action, and the serialized identifier payload for
that action.

::: general_manager.cache.dependency_index.serialize_dependency_identifier

::: general_manager.cache.dependency_index.parse_dependency_identifier

Use `serialize_dependency_identifier()` when custom integrations need to create
the canonical string stored in a `Dependency` identifier slot. The paired parser
returns JSON-compatible decoded data or `None` for malformed JSON; a serialized
JSON `null` payload also decodes to `None`, so callers that need to distinguish
those cases should avoid using `null` as a dependency identifier payload. The
parser is intentionally lossy: dates, datetimes, `__getstate__()` payloads, and
unsupported-object representation markers come back as normalized JSON-compatible
structures rather than the original Python objects. The serialized string is the
current `json.dumps(..., sort_keys=True)` output of that normalized structure.
Normalization checks value categories in this order: mappings, lists/tuples,
sets, datetimes, dates, JSON scalar values, mapping-shaped `__getstate__()`, then
`repr(...)` fallback. Mapping keys are coerced with Python's exact `str(key)`
result. Dependency payloads should avoid mapping keys or set members that collide
after `str(...)`. If mapping keys normalize to the same string, normalization
keeps the last item after sorting by `str(key)`; equal sort keys keep the input
mapping's iteration order. Set members with identical string forms have
intentionally unspecified ordering, so their byte-for-byte serialized output is
not a public guarantee. Non-finite floats use Python's default JSON spellings
(`NaN`, `Infinity`, or `-Infinity`), which are accepted by Python's parser but
are not portable strict JSON; dependency identifiers containing these values are
Python-cache metadata rather than strict JSON interchange values. If
`__getstate__()` exists but returns a non-mapping, the value uses the same
`repr(...)` fallback as other unsupported objects.

`record_dependencies()` deduplicates dependency tuples and is a no-op for an
empty dependency iterable; an empty call does not clear existing metadata for
that cache key. Re-recording non-empty dependencies for an existing cache key
removes the key's previous sharded and reverse metadata before writing the new
set. A non-empty iterable whose dependencies all normalize to no shards still
replaces prior metadata with an empty reverse entry. Malformed or non-mapping
`filter` / `exclude` identifiers are ignored rather than stored. For `"all"`
dependencies, invalidation is keyed by manager name and the identifier is
retained only as opaque reverse metadata; all `"all"` dependencies for the
changed manager invalidate together regardless of identifier. Multiple `"all"`
tuples with different identifiers for the same cache key are retained as
separate reverse metadata entries until a later non-empty
`record_dependencies()` call replaces that key's metadata. The retained
identifiers preserve the original dependency tuple and have no invalidation
meaning.

`"request_query"` dependencies invalidate on any change for the same manager
name. `"identification"` dependencies invalidate when the changed manager's
current `identification` serializes exactly to the stored identifier. `"all"`
dependencies invalidate on any change for the same manager name.
`invalidate_cache_key()` deletes only the cached value, while
`remove_cache_key_from_index()` removes only dependency-index metadata. Use
`invalidate_and_remove_cache_keys()` internally when both steps must happen
together.

Filter and exclude invalidation compare serialized expected values with the
changed manager's before/after attribute values. Equality and membership
dependencies coerce JSON scalars, ISO dates/datetimes, booleans,
`{"__state__": ...}` mappings, and `{"__repr__": ...}` markers back toward the
runtime value being compared. Range operators use the runtime value's ordering
after coercion. String lookup operators (`contains`, `startswith`, `endswith`,
and `regex`) compare against `str(runtime_value)`. Supported lookup suffixes are
`gt`, `gte`, `lt`, `lte`, `in`, `contains`, `startswith`, `endswith`, and
`regex`; any other suffix is treated as part of the nested attribute path and
uses equality matching. Missing attributes resolve to `None`.

Filter dependencies invalidate when either the old or new value matches because
the changed object may enter or leave the cached result. Exclude dependencies
invalidate when match status changes because that changes whether the object is
excluded from the cached result. ISO strings are not self-describing: an
ISO-looking stored string compares as a date/datetime when the runtime value is a
date/datetime, and as a string when the runtime value is a string. Date runtime
values only accept strings parsed by `date.fromisoformat()`; datetime-shaped
strings do not match date runtime values. Date values do not match datetime
runtime values. Datetime strings are parsed with `datetime.fromisoformat()` after
replacing a trailing `Z` with `+00:00` and replacing the first space separator
with `T`; timezone-aware parsed values have timezone information removed when the
runtime value is naive, and naive parsed values receive the runtime value's
timezone when it is aware. Boolean runtime values accept booleans, any integer
via Python truthiness, and the strings `true`, `1`, `yes`, `y`, `t`, `false`,
`0`, `no`, `n`, and `f` case-insensitively after trimming whitespace. Other
runtime values attempt `type(runtime_value)(stored_value)` coercion, so numeric
strings and non-finite floats follow that runtime type's constructor behavior.
Range operators use runtime ordering after coercion; failed coercion is a
non-match, while ordering exceptions from the runtime value propagate.
`{"__state__": ...}` mappings are compared by constructing the runtime value's
type from keyword state, with a positional `(magnitude, unit)` fallback;
constructor failures do not match. `{"__repr__": ...}` markers compare only with
`repr(runtime_value)`.

Lookup paths are `__`-separated attribute names resolved with `getattr()`; there
is no escaping syntax, dict/list traversal, or callable invocation beyond normal
property access. A final path segment matching a supported suffix is always
parsed as that lookup operator; for example, `field__in` is membership lookup,
not equality on `field.in`. `contains` means
`stored_pattern in str(runtime_value)`, `startswith` means
`str(runtime_value).startswith(stored_pattern)`, `endswith` means
`str(runtime_value).endswith(stored_pattern)`, `in` means the runtime value
matches at least one item in the stored JSON list, and `regex` uses
`re.search(stored_pattern, str(runtime_value))` without flags. Invalid regex
patterns do not match. For string operators, non-string expected values are
coerced with `str(expected_value)` after JSON parsing. For `in`, a stored
expected value that is not a JSON list is a non-match. Only parse, constructor
`TypeError`/`ValueError`, and regex compilation failures are converted to
non-matches; exceptions raised by attribute properties, `str(runtime_value)`,
`repr(runtime_value)`, or comparison operators propagate to the invalidation
caller.

An empty filter or exclude mapping (`{}`) is not evaluated as a normal composite
predicate; it records an all-records dependency for that manager and invalidates
on any change for that manager. Multi-lookup identifiers are composite
dependencies within their single action: a `"filter"` identifier matches only
when every lookup in that identifier matches, and an `"exclude"` identifier also
computes match status by requiring every lookup in that identifier to match. The
action then controls invalidation: filters invalidate when old or new composite
status is true, while excludes invalidate when old and new composite status
differ. Malformed expected values inside an otherwise valid mapping are treated
as non-matches for that lookup, not as stored dependency errors. Constructor
coercion calls the runtime value's type; constructors with side effects should
not be used for dependency values.

### Sharded Dependency Helpers

`general_manager.cache.dependency_shards` exposes the low-level shard store used
by the dependency index. Most applications should call
`record_dependencies()`, `invalidate_cache_key()`, or
`remove_cache_key_from_index()` instead, but integrations that need to inspect
or migrate dependency metadata can use these helpers directly.

Dependency tuples have the shape `(manager_name, action, identifier)`, where
`action` is `"filter"`, `"exclude"`, `"identification"`, `"request_query"`, or
`"all"`. Composite dependencies are filter/exclude dependencies whose
identifier serializes multiple lookup paths. Simple dependencies are all other
dependency tuples retained in reverse metadata. `ReverseDependencyMembership`
instances are considered valid reverse payloads by type; dependency tuple
members inside an otherwise valid instance are returned unchanged rather than
sanitized.

::: general_manager.cache.dependency_shards.ReverseDependencyMembership

::: general_manager.cache.dependency_shards.reverse_membership_key

::: general_manager.cache.dependency_shards.exact_lookup_shard_key

::: general_manager.cache.dependency_shards.scan_lookup_shard_key

::: general_manager.cache.dependency_shards.composite_lookup_shard_key

::: general_manager.cache.dependency_shards.all_records_shard_key

::: general_manager.cache.dependency_shards.request_query_shard_key

::: general_manager.cache.dependency_shards.lookup_registry_key

::: general_manager.cache.dependency_shards.cache_set_members

::: general_manager.cache.dependency_shards.clear_legacy_dependency_index

::: general_manager.cache.dependency_shards.record_cache_dependencies

::: general_manager.cache.dependency_shards.record_many_cache_dependencies

::: general_manager.cache.dependency_shards.remove_cache_key_from_shards

::: general_manager.cache.dependency_shards.candidate_cache_keys_for_lookup

::: general_manager.cache.dependency_shards.request_query_cache_keys

::: general_manager.cache.dependency_shards.all_records_cache_keys

::: general_manager.cache.dependency_shards.tracked_lookup_names

::: general_manager.cache.dependency_shards.reverse_memberships

The shard helpers store cache keys in exact, scan, composite, request-query, and
all-records sets. `record_many_cache_dependencies()` deduplicates cache keys and
dependencies in memory, clears the legacy full-index cache key once per batch,
removes any previous reverse membership for each cache key, and then writes
batched shard and reverse metadata. Empty dependency iterables are no-ops.
Repeated entries for the same cache key are merged as a Python set, so duplicate
dependency tuples collapse without preserving input order; an empty repeated
entry does not cancel a non-empty repeated entry for the same cache key. The
public type contract requires dependency 3-tuples with a string manager name, one
of the typed actions, and a string identifier. Callers that violate that tuple
shape or type contract can receive normal Python unpacking or type errors.
Within the contract, unsupported actions, malformed `filter` / `exclude` JSON,
non-mapping filter/exclude identifiers, and otherwise unshardable dependencies
write empty reverse metadata when the cache key has at least one supplied
dependency, preserving the fact that newer metadata replaced older metadata.
`record_cache_dependencies()` is the one-cache-key wrapper around that same
behavior. The write order is legacy-index cleanup, previous-shard removal, new
shard and lookup-registry writes, then reverse metadata writes.

`candidate_cache_keys_for_lookup()` is intentionally conservative. It returns
cache keys from exact old/new value shards, scan-operator shards, composite
lookup shards, and all-records shards; callers still need to evaluate reverse
metadata before deleting cached values. Passing `VALUE_NOT_PROVIDED` for an old
or new value suppresses that exact-value lookup; passing `None` is a real
dependency value and is hashed. Runtime callers are expected to pass
`"filter"` or `"exclude"` for `action`; the helper relies on the type contract
and does not add runtime validation. Exact old/new candidate lookups use only
equality (`"eq"`) shards for the base lookup name. Scan candidates are read for
every operator in `SCAN_OPERATORS` using both `{lookup}__{operator}` and the base
lookup name returned by `lookup_spec_from_key()`, which strips supported
operator suffixes such as `status__gte` to the `status` attribute path.

`cache_set_members()` accepts only cached `set`, `frozenset`, `list`, or `tuple`
payloads. Missing values, `None`, and other payload types produce an empty set;
non-string members inside accepted collections are dropped member-by-member.
Helpers that read shard sets inherit this behavior, so malformed filter and
exclude lookup registries are ignored independently. `reverse_memberships()`
first reads the reverse registry through `cache_set_members()`, then returns
only cached values that are actual `ReverseDependencyMembership` instances.

Shard-key builders interpolate string inputs exactly as provided; they do not
escape empty strings, colons, or other unusual characters. `reverse_membership_key()`
hashes the application cache key before embedding it. `exact_lookup_shard_key()`
hashes only the dependency value through `stable_value_hash()`, whose
normalization rules are the same as `serialize_dependency_identifier()`:
mappings, sequences, sets, dates, datetimes, JSON scalar values,
mapping-shaped `__getstate__()`, and `repr(...)` fallback are normalized
deterministically before hashing. Lookup names stored in the filter/exclude
registries are normalized attribute paths with supported operator suffixes
stripped.

`clear_legacy_dependency_index()` understands the old top-level `"all"`,
`"request_query"`, `"filter"`, and `"exclude"` sections. `"all"` stores manager
sections of cache-key collections; `"request_query"` stores manager/query
sections of cache-key collections; filter/exclude sections store manager lookup
maps and may include `"__cache_dependencies__"` cache-key maps for composite
dependencies. Malformed branches, non-set-like member collections, and
non-string referenced cache keys are skipped member-by-member where possible.
The helper deletes the legacy index key and returns the legacy cache keys it
submitted to `cache.delete()`. The helpers are not atomic across multiple cache
keys or shards. They do not raise deliberate package-specific exceptions;
errors and partial-write behavior from the configured Django cache backend or
value serialization propagate to the caller.

::: general_manager.cache.cache_tracker.DependencyTracker

::: general_manager.cache.run_context.CalculationRunContext

`CalculationRunContext(dependency_cache_publish_batch_size=1000)` is a
context-manager-scoped in-memory cache for one request, graph, bulk operation,
or background task. Values less than or equal to zero for
`dependency_cache_publish_batch_size` are accepted and make every buffered
dependency-cache publication flush immediately. `__enter__()` activates the
context in the current context-variable scope and returns the context. On clean
`__exit__()`, buffered dependency-cache publications are flushed; on exceptional
exit, they are discarded. Both paths reset the active context token and clear
run-local values, prefetched dependency hits, and pending publications, even
when flushing or lease release raises. Calling `__exit__()` on an instance that
was not entered is a no-op. Re-entering the same context instance is supported:
inner exits restore the current active context without flushing or clearing
state, and the outermost exit owns publication cleanup and storage clearing.
Flush, discard, lease-release, cache backend, and context-variable reset errors
propagate unchanged, except guarded `CachePublishAborted` batch-publish results
are debug-logged and swallowed before leases are released.
Active context lookup follows Python `contextvars` semantics: nested
`CalculationRunContext` instances replace the active context for their nested
block and restore the previous context on exit, async task propagation follows
normal context-variable behavior, and unrelated threads do not automatically
share the active context.

`get_or_set(key, loader)` stores the first successful loader result under the
hashable key and returns that same object for later calls with the same key.
Loader exceptions propagate and do not store a value. `key`,
`source_signature`, `key_spec`, and direct storage keys are opaque hashable
values owned by the caller. `loader`, `index_by`, and `group_by` callables are
synchronous; coroutine objects returned by those callables are treated as normal
values. `get(key, default=None)` returns the stored object or the exact default
object when absent. `set(key, value)`, `discard_prefix(prefix)`,
`set_dependency_cache_hits(...)`, dependency-publication mutators, ORM bucket
mutators, and clear methods return `None`. `has(key)` and `key in context`
return booleans. `discard_prefix(prefix)` removes tuple keys whose leading items
match the supplied tuple.

`index(key=..., loader=..., index_by=...)` stores a dictionary under
`("index", key)` and keeps the last row when multiple rows produce the same
index key, determined by loader iteration order. `group_by(...)` stores lists
under `("group_by", key)` in loader iteration order, and `index_many(...)` is an
alias for `group_by(...)`. Loader and key-function exceptions propagate and do
not store a partial index/group.

`set_dependency_cache_hits(...)` merges prefetched dependency-cache hits into
the active run. Provided keys replace existing hits for the same cache key, and
omitted existing keys remain available.
`buffer_dependency_cache_publication(entry)` accepts a
`PendingDependencyCachePublication` with the public dataclass fields
`cache_key`, `result`, `dependencies`, `cache_backend`, `timeout`,
`started_generation`, and `lease`. The `lease` is a `CacheComputeLease` with
`key` and `token`; both values are created by dependency-publish helpers, not by
the run context. Buffering makes a computed miss visible as a run hit
immediately, replacing any prefetched hit for the same cache key. It also
replaces prior buffered entries for the same cache key, releases a replaced
lease when the lease token differs, and flushes when the configured batch size
is reached. `flush_dependency_cache_publications()`,
`discard_dependency_cache_publications()`, and `discard_dependency_cache_state()`
own the pending-publication lifecycle. Flush and discard copy pending entries,
clear the pending map before publication or release, and then release leases; a
failed publish or release therefore propagates but does not leave those pending
entries buffered for a later retry by the same run context.
`entry` is a `PendingDependencyCachePublication` produced by the dependency-cache
compute path; the context reads its `cache_key`, `result`, `dependencies`, and
`lease` fields but does not derive cache keys or lease tokens. Dependency tuples
use the public `Dependency` shape from `dependency_index`. Concrete exception
classes from cache backends, dependency-index publishing, lease release, or
custom backend implementations are not normalized by this module, so callers see
the source exception type. The only run-context-specific publication exception
handling is that `CachePublishAborted` from batch publishing is swallowed after a
debug log; callers that need to catch backend-specific failures must catch the
backend or dependency-publish exception types used by their configured backend.

ORM bucket helpers store result and row snapshots under internal tuple prefixes.
`get_orm_bucket_result(key)` reads the value stored under
`("orm_bucket_result", key)` and returns `None` when absent.
`set_orm_bucket_result(key, value)` stores or overwrites that value.
`get_orm_bucket_rows(key)` and `set_orm_bucket_rows(key, value)` do the same
under `("orm_bucket_row_result", key)`. `key` is an opaque hashable value chosen
by the ORM bucket layer. `clear_orm_bucket_results()` clears both namespaces.
Bucket-index helpers store `BucketIndexRunCacheEntry` values keyed by source
signature, key spec, `many`, and `max_rows`.
`set_bucket_index_result(source_signature, key_spec, many, value, dependencies,
max_rows)` stores a `BucketIndexRunCacheEntry` under
`("bucket_index", source_signature, key_spec, many, max_rows)` after freezing
the dependency iterable. `get_bucket_index_result(source_signature, key_spec,
many, max_rows)` reads the same key and returns `None` for missing or non-entry
values; cache hits replay stored source dependencies through
`DependencyTracker.track()` before returning the cached value.
`clear_bucket_indexes()` clears the bucket-index namespace.

::: general_manager.cache.run_context.current_calculation_run_context

`current_calculation_run_context()` returns the context active in the current
context-variable scope, or `None` when no calculation run context is active.

::: general_manager.cache.run_context.ensure_calculation_run_context

`ensure_calculation_run_context()` is a context manager that returns the current
active context when one exists. When none exists, it creates, enters, and later
exits a temporary `CalculationRunContext`. Use it only in `with` statements:
`with ensure_calculation_run_context() as context:`. It is a class-based context
manager, not a decorator or generator context manager; `__enter__()` yields a
`CalculationRunContext` and `__exit__()` returns `None`.

::: general_manager.cache.signals.pre_data_change

::: general_manager.cache.signals.post_data_change

::: general_manager.cache.signals.data_change

`data_change(func)` wraps GeneralManager create, update, and delete methods with
dependency-cache barrier management and Django signal dispatch. The wrapper
preserves the wrapped callable's metadata with `functools.wraps`, opens the
dependency publish barrier before the mutation, clears run-scoped ORM bucket and
bucket-index snapshots in the active `CalculationRunContext`, emits
`pre_data_change`, calls the wrapped function, emits `post_data_change`, and
then closes the barrier. Methods named `create` are treated as class-level
creates and use the first positional argument as the Django signal `sender`;
every other method name is treated as an instance mutation and uses
`instance.__class__` as `sender`. Raw `classmethod` descriptor objects are
accepted for compatibility, but normal class methods should prefer
`@classmethod` outside `@data_change`.

`pre_data_change` receives `sender`, `instance`, `action`, and the wrapped
method keyword arguments. `post_data_change` receives `sender`, `instance`,
`previous_instance`, `identification`, `action`, `old_relevant_values`, and the
wrapped method keyword arguments. For deletes or other mutations returning
`None`, `post_data_change.instance` is `None` and `previous_instance` carries the
pre-mutation object. The `identification` value prefers the returned instance's
current `identification`; if that is missing or `None`, the wrapper uses a deep
copy of the pre-mutation identification. `old_relevant_values` comes from the
pre-mutation instance's `_old_values` attribute, defaulting to `{}`, and the
wrapper removes `_old_values` from that instance after a successful post-change
signal.

Signal receiver exceptions propagate and skip later steps in the normal Python
call path. The dependency barrier is still closed in `finally`. Cleanup errors
propagate when the wrapped mutation succeeded; if the wrapped mutation already
failed, cleanup errors are logged and the original mutation exception is
re-raised. Invalidated GraphQL warm-up cache keys collected during signal
handling are drained only after the outermost active data-change barrier has
closed, so nested `@data_change` calls enqueue one final rewarm batch. Failed
mutations still drain pending rewarm keys after the outermost barrier closes,
but enqueueing runs only for completed mutations. GraphQL warm-up enqueue errors
are logged and suppressed.

::: general_manager.cache.dependency_index.record_dependencies

::: general_manager.cache.dependency_index.invalidate_cache_key

::: general_manager.cache.dependency_index.remove_cache_key_from_index
