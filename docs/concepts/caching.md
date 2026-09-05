# Caching and Dependency Tracking

GeneralManager keeps cached data in sync by recording read dependencies and invalidating matching cache entries when data changes. The dependency model is conservative enough to avoid stale results, but narrow enough to avoid evicting unrelated caches.

## Dependency Tracker

When a manager, bucket, or cached function resolves data, it records dependencies
in `DependencyTracker`. The tracker stores tuples of
`(manager_name, operation, identifier)`. The public `Dependency` type is
`tuple[str, Literal["filter", "exclude", "identification", "request_query",
"all"], str]`. Any code wrapped in a `with DependencyTracker()` context receives
the set of dependencies touched during that read. Calling
`DependencyTracker.track(...)` outside an active tracker is a no-op after
validating the supplied tuple values. `manager_name`, `operation`, and
`identifier` must be strings; unsupported operations raise `ValueError`.
Concrete invalid-input exception subclasses and messages are internal details;
callers should catch `TypeError` for malformed tuple values and `ValueError` for
unsupported operations.

Nested tracker scopes get separate sets, and dependencies tracked in a nested
scope are also recorded in every enclosing scope. Duplicate dependency tuples
collapse because collectors are sets. Returned collector sets remain usable
snapshots after the context exits; clearing thread-local tracking state does not
mutate sets already returned from `with DependencyTracker() as dependencies`.
`reset_thread_local_storage()` is safe with or without an active context. If it
is called inside a context, later `track(...)` calls are ignored until a new
context is entered and the eventual context exit is a no-op. Tracking state is
thread-local only, not async task-local, so interleaved async work on the same
thread shares the active tracker state.

CRUD methods (`create`, `update`, `delete`) emit invalidation signals. The dependency index compares the recorded dependencies against the before/after state of the changed manager and removes only the affected cache keys.
During each `@data_change` mutation, GeneralManager opens a dependency-cache
publish barrier before the mutation and closes it afterwards. Nested mutations
keep the barrier active until the outermost mutation exits. Invalidated GraphQL
warm-up cache keys collected by signal handlers are drained and enqueued only
after that outermost barrier is closed, so warm-up work never starts while a
data change is still active.

## ORM transaction lifecycle consumers

For ORM-backed mutations, GeneralManager also provides an outermost,
database-alias-scoped lifecycle envelope around the existing data-change
signals. `data_change_transaction_started` runs after GeneralManager enters its
own `atomic()` block or savepoint and before `pre_data_change`.
`data_change_transaction_finishing` runs after successful post-change receivers
but before that block or savepoint exits. `data_change_transaction_finished`
runs after the exit with `outcome="committed"` or `outcome="rolled_back"`.
The three lifecycle signals share a mutable `transaction_context` that exposes
the alias, whether the caller already owned an atomic block, a deduplicated set
of changed class names, consumer metadata, and accumulated phase timings.

The lifecycle is deliberately not an outer caller-transaction commit signal.
If `transaction_context.caller_in_atomic_block` is true, a `"committed"`
finished outcome means only the GeneralManager savepoint completed. Consumers
that need work to run after the caller's durable commit must register it with
`transaction.on_commit()` from the started receiver. If that outer transaction
rolls back, Django does not call the callback. Any consumer that holds a lease
or other external coordination resource must therefore use a bounded lease and
expiry-based cleanup for the rollback path.

Use `register_data_change_class(sender.__name__, database_alias)` from a
`post_data_change` receiver when a consumer needs the set of classes changed in
one framework-owned envelope. The helper deduplicates names and returns `True`
when the requested alias has a matching live framework-owned envelope. It can
register only against the requested `database_alias`; it never searches or
registers against another alias. It returns `False` when the requested alias is
absent or its live envelope is caller-owned. The API reference gives the exact
signal payloads, consumer wiring, and phase-timing meanings:
[ORM data-change transaction lifecycle](../api/cache.md#orm-data-change-transaction-lifecycle).

## Bucket Dependency Semantics

For ORM-backed buckets, dependency tracking happens when the bucket is actually evaluated, not when the bucket object is first constructed.

That means a chain such as:

```python
bucket = Project.all().filter(name="Test").exclude(status="archived")
count = bucket.count()
```

records the effective narrowed query when `count()` runs. If the intermediate `Project.all()` bucket is never evaluated on its own, it does not create an extra broad dependency.

This deferred tracking applies to terminal bucket operations such as:

- iteration
- `count()`
- `first()` / `last()`
- `get()`
- `len(bucket)`
- scalar indexing such as `bucket[0]`
- membership checks such as `manager in bucket`

Membership checks on ORM-backed buckets use a targeted `exists()` lookup for
the checked primary key instead of materialising every row ID.

Empty result sets still record dependencies. A cached `count() == 0` must invalidate when a later create or update makes the query match.

### Bucket transformations

Bucket transformations preserve the narrowed dependency state. This includes:

- chained `filter()` / `exclude()`
- `all()` on an already narrowed bucket
- slicing
- `sort()`
- grouping and calculation buckets that are backed by ORM bucket evaluation

As a result, `Project.all().filter(name="Test").sort("number")` invalidates when the filtered result changes, not when an unrelated project changes.

### Composite filters and excludes

Multiple lookups passed in a single `filter()` or `exclude()` call are treated as one composite dependency. The dependency index stores the full payload and invalidates only when the combined condition changes from the cache's point of view.

For example:

```python
Project.filter(name="Test", status="active")
```

is tracked as one composite dependency. By contrast:

```python
Project.filter(name="Test").filter(status="active")
```

produces the same effective dependency entry for ORM-backed buckets. Chained `filter()` calls are merged into the bucket's final `self.filters` state before the bucket is evaluated, so the dependency index sees the combined payload from the final narrowed bucket rather than one entry per intermediate unevaluated bucket.

The same rule applies to chained `exclude()` calls through `self.excludes`: the dependency entry reflects the evaluated bucket state, not every intermediate builder step.

## Request-backed buckets

Request-backed buckets are currently the exception. They still use eager `request_query` dependency tracking at request-plan construction time. Their invalidation model is separate from the deferred ORM bucket behavior described above.

## Caching helper

Use the `@general_manager.cache.cache_decorator.cached` decorator to memoise
expensive helpers for the active request, calculation graph, bulk operation, or
background run:

```python
from general_manager.cache.cache_decorator import cached

@cached
def project_forecast(project_id: int) -> dict[str, float]:
    project = Project(id=project_id)
    return {
        "budget": project.total_capex.value,
        "derivatives": project.derivative_list.count(),
    }
```

The default `cache="run"` stores values in `CalculationRunContext`. Values are
discarded when the run ends and do not participate in dependency invalidation.
`cache="none"` calls the wrapped function every time and does not read or write
the configured cache backend.

Use dependency caching when a value should be reused across runs and invalidated
when tracked managers change:

```python
@cached(cache="dependency")
def project_forecast(project_id: int) -> dict[str, float]:
    ...
```

When the wrapped dependency-scoped function runs, it records every manager it
touches. Subsequent calls reuse the cached value until a tracked dependency
changes.

Dependency-scoped cache entries are published through a guarded write path:

- a mutation generation is read before computation starts
- data-changing operations raise the generation and hold a publish barrier while invalidation runs
- inside a `CalculationRunContext`, computed misses are buffered and exposed to later calls in the same run
- custom dependency `record_fn` callbacks preserve immediate publication instead of using the run buffer
- buffered entries publish at run exit or at a controlled guardrail flush point
- the dependency index and combined value/dependency payloads are written under the dependency-index lock
- dependency metadata is stored before cached values become visible, so a visible value is already reachable by later invalidation
- if the generation changed or the publish barrier is active, the fresh function result is returned to the caller but is not stored

This means a dependency-scoped value is only shared after GeneralManager can
prove that no data mutation overlapped the computation and publish step. Values
computed during the current run remain available to that run even when guarded
publication is skipped, until a data change begins in that run.

Concurrent workers for the same dependency-scoped cache key coordinate with a
short-lived compute lease. The worker that acquires the lease performs the
function body and publishes the value. Other workers wait for that value to
appear and then reuse it instead of repeating the same CPU work. If the
computing worker fails before publishing, the lease expires and a later worker
can retry the computation.

Use timeout caching when a value should be cached in the configured cache backend
for a fixed duration without dependency tracking:

```python
@cached(cache="timeout", timeout=300)  # Cache for 5 minutes
def project_forecast(project_id: int) -> dict[str, float]:
    ...
```

`timeout` is required for `cache="timeout"` and is not accepted on the other
cache modes. The cache entry expires after the given duration and is not invalidated
through the dependency index.

Custom cache backends passed to `cached` only need the two methods used by the
selected persistent scopes: `get(key, default)` returns a cached object or the
exact default sentinel when absent, and `set(key, value, timeout=None)` stores a
backend-serializable object. Timeout caching can use such a backend. Dependency
caching is restricted to Django's configured default cache identity so its
invalidation index can be resolved consistently. Run and none scopes do not
call the backend. `cached` accepts synchronous callables only; async functions,
async callable objects, and runtime awaitable results are rejected.

## Run context storage

`CalculationRunContext` exposes lightweight storage for one request,
calculation graph, bulk operation, or background run. Use it for working sets
that should not enter the dependency index:

```python
from general_manager.cache.run_context import CalculationRunContext

with CalculationRunContext() as context:
    context.set(("project", project_id), project)
    project = context.get(("project", project_id))
```

Use `get_or_set(key, loader)` to load a value once, `has(key)` or `key in
context` to check storage, `index(key=..., loader=..., index_by=...)` for
one-row-per-key lookups, and `group_by(...)` or `index_many(...)` when multiple
rows share the same key. Use `discard_prefix(prefix)` when code that owns a
structured key namespace needs to invalidate a group of run-scoped values.
Loader exceptions from `get_or_set()` propagate and do not cache a value, so a
later call can retry. `index()` keeps the last row for duplicate index keys in
loader iteration order; `group_by()` and `index_many()` preserve loader
iteration order for groups and rows. Loader and key-function exceptions from
these indexing helpers propagate without storing a value.

Entering `CalculationRunContext` activates it in a context variable. Clean exits
flush buffered dependency-cache publications, exceptional exits discard them,
and both paths clear run-local values, prefetched dependency hits, and pending
publications, even when publication or lease release raises. Re-entering the
same context instance is supported: inner exits keep the run-local state alive,
and the outermost exit performs cleanup. Calling `__exit__()` on a context that
was not entered is a no-op. `ensure_calculation_run_context()` reuses an
existing active context and only creates/exits a temporary one when none is
active.

For long-lived or concurrent runs, applications can configure the optional
`GENERAL_MANAGER["RUN_CONTEXT_CACHE_MAX_BYTES"]` setting to give eviction-safe
run-cache entries a process-local estimated-memory budget. Enabling the cap uses
shallow admission signals and coarse aggregate calibration: the first and
sparse later entries for each storage family are bounded deep samples, while
ordinary admissions reuse their calibrated estimate. The estimate targets 5%
accuracy for representative aggregates, not adversarial object graphs or RSS,
and eviction keeps a 5% modeled reserve.

Read recency is inactive below pressure, where insertion order remains in
effect. Near the cap it becomes a batched approximate LRU, avoiding global
coordination on every hit. Pending dependency-cache publications remain pinned.
Plan fleet capacity by multiplying the configured value by the number of worker
processes: each Gunicorn, Celery, or other Python worker has its own budget. The
estimate is taken when a value is inserted, so post-insert caller mutation,
native allocations, and other process memory remain outside it. The mechanism
collects no telemetry and is not a hard worker-memory or RSS limit; applications
that need a hard worker ceiling should also configure deployment-level memory
limits and worker recycling.

Callable `Input.possible_values` providers are also cached automatically inside
an active `CalculationRunContext` when the caller can identify the owning manager
class and input name. The cache key uses the manager class, the input name, and
only the input's declared `depends_on` values. Static domains and static
iterables are returned directly and are not copied into the run cache.

### Bucket projection reuse

`Bucket.values()` and `Bucket.values_list()` are active-run snapshots. Outside
an existing `CalculationRunContext`, each call evaluates the bucket normally
and does not retain a projection for another call. Inside one run, dictionary,
tuple-row, and flat-scalar modes share one canonical tuple-of-tuples entry for
the same bucket source signature and ordered field tuple. Public `values()`
dictionaries and outer result tuples are freshly materialized on every call;
tuple-style rows are immutable canonical tuples and may be reused on a cache
hit, so inner-tuple identity is not part of the contract. Their attribute
values stay shallow and keep normal identity and mutation semantics.

The projection miss records the full dependency set produced by the selected
native or portable evaluation. A later hit replays that set into the active
`DependencyTracker` before returning, so reuse does not weaken invalidation.
Database mutations clear all projection entries in the active run together
with ORM snapshots and bucket indexes, both at the mutation boundary and after
the operation. This coarse clearing is deliberate: dependencies may cross
manager and backend boundaries. Request projections retain the existing
materialized request semantics because remote changes cannot currently clear
local run state.

Projection results up to 10,000 rows may be retained; a result with 10,001 or
more rows still returns successfully but is not admitted to the run cache. This
projection-specific retention rule is separate from the ORM snapshot and
bucket-index guardrails. Admitted entries share the optional
`RUN_CONTEXT_CACHE_MAX_BYTES` process-local byte budget with other run values,
including insertion-time estimation, reserve-aware approximate LRU eviction,
and pending-publication pinning. The byte budget can evict an admitted
projection under pressure and is not a hard process-memory limit.

ORM-backed managers use this explicit run context to deduplicate repeated row
materialization for the same manager identity. The optimization is active only
inside an existing `CalculationRunContext`; constructing managers outside a run
context continues to read from the database normally. Negative lookups are not
cached, and ORM update/delete paths clear affected row entries in the active run
context after successful mutations.

ORM-backed `DatabaseBucket` terminal operations also reuse conservative
run-scoped snapshots inside the active context. Iteration materializes a bounded
row snapshot and hydrates managers through GeneralManager's private trusted ORM
path, so rows that were already loaded by the queryset are not fetched again by
primary key. The same evaluation stores primary keys for length, `count()`,
`first()`, `last()`, scalar indexing, primary-key `get()`, and membership checks
after a snapshot exists. The cache is intentionally conservative: unsupported
query shapes and large buckets bypass reuse, count-only paths do not force full
materialization, dependencies are still tracked on every terminal operation, and
any framework mutation clears bucket snapshots in the active run before the data
changes.

The trusted ORM hydration path is private framework infrastructure. It is safe
only for Django model or historical rows returned by GeneralManager-owned ORM
querysets. Public construction, GraphQL mutations, imports, factories, and other
user-controlled payloads still use the regular manager constructor and full
interface input validation. Managers or ORM interfaces that override `__init__`
also use the regular constructor so local initialization is preserved. Trusted
hydration falls back to primary-key construction when a row comes from a
different model, when a `search_date` bucket is holding a live row instead of a
historical/as-of row, or when the instance has deferred fields. Querysets with
`prefetch_related()` or deferred fields bypass run-scoped bucket snapshots
because those query plans can change loaded row state without changing the main
SQL signature.

## Manual dependency-index helpers

Most application code should rely on CRUD signals and dependency-scoped
`cached` calls. For integration code and tests, the cache module also exposes
lower-level dependency-index helpers:

- `record_dependencies(cache_key, dependencies)` stores the dependency set for an already-computed cache entry.
- `invalidate_cache_key(cache_key)` invalidates one cache key without recalculating dependency matches.
- `remove_cache_key_from_index(cache_key)` removes dependency-index metadata for a key that should no longer participate in invalidation.

These helpers are intentionally lower level than `cached`. Use them when building a custom cache backend or verifying invalidation behavior, not as the default application caching API.

## Recommended practices

- Configure a shared cache backend (Redis or Memcached) in production so dependency signals and timeout-scoped cache entries reach all processes.
- Keep cache keys deterministic by relying on the built-in `make_cache_key` helper.
- Avoid dependency-scoped caching for code paths that bypass permission checks. The cached decorator records dependencies, not the caller identity.
- Prefer grouping logically inseparable lookup clauses into one `filter()` or `exclude()` call when you want them invalidated as one composite dependency.
- Treat request-backed bucket caching separately from ORM-backed bucket caching when debugging invalidation behavior.
- Regularly test cache invalidation by running workflows that update managers and verifying that cached results change accordingly.

For low-latency APIs, combine run-scoped caching with bucket-level prefetching
and GraphQL data loaders. Generated GraphQL list resolvers also perform a
targeted dependency-cache prefetch for ungrouped result pages: when the selected
`items` fields include `@graph_ql_property(cache="dependency")` properties and
the caller may read those fields, the resolver bulk-reads the dependency cache
for the returned page and stores hits in the active calculation run context.
Selection detection looks under the generated page's `items` field and is
syntactic: it follows field names and fragment spreads, not aliases, and it does
not evaluate GraphQL directives or fragment type conditions. If there is no
active calculation run context, the resolver skips the bulk cache read and the
normal property resolver path handles the selected fields.
Grouped results are not materialized as item lists, so dependency-cache prefetch
is skipped for grouped list responses. Missing cache entries are left for the
normal property resolver path to compute.
