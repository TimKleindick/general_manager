# Caching and Dependency Tracking

GeneralManager keeps cached data in sync by recording read dependencies and invalidating matching cache entries when data changes. The dependency model is conservative enough to avoid stale results, but narrow enough to avoid evicting unrelated caches.

## Dependency Tracker

When a manager, bucket, or cached function resolves data, it records dependencies in `DependencyTracker`. The tracker stores tuples of `(manager_name, operation, identifier)`. Any code wrapped in a `with DependencyTracker()` context receives the set of dependencies touched during that read.

CRUD methods (`create`, `update`, `delete`) emit invalidation signals. The dependency index compares the recorded dependencies against the before/after state of the changed manager and removes only the affected cache keys.

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

@cached()
def project_forecast(project_id: int) -> dict[str, float]:
    project = Project(id=project_id)
    return {
        "budget": project.total_capex.value,
        "derivatives": project.derivative_list.count(),
    }
```

The default `scope="run"` stores values in `CalculationRunContext`. Values are
discarded when the run ends and do not participate in dependency invalidation.

Use dependency scope when a value should be reused across runs and invalidated
when tracked managers change:

```python
@cached(scope="dependency")
def project_forecast(project_id: int) -> dict[str, float]:
    ...
```

When the wrapped dependency-scoped function runs, it records every manager it
touches. Subsequent calls reuse the cached value until a tracked dependency
changes.

Dependency-scoped cache entries are published through a guarded write path:

- a mutation generation is read before computation starts
- data-changing operations raise the generation and hold a publish barrier while invalidation runs
- the dependency index and combined value/dependency payload are written under the dependency-index lock
- dependency metadata is stored with the cached value, so a visible value is already reachable by later invalidation
- if the generation changed or the publish barrier is active, the fresh function result is returned to the caller but is not stored

This means a dependency-scoped value is only shared after GeneralManager can
prove that no data mutation overlapped the computation and publish step.

Concurrent workers for the same dependency-scoped cache key coordinate with a
short-lived compute lease. The worker that acquires the lease performs the
function body and publishes the value. Other workers wait for that value to
appear and then reuse it instead of repeating the same CPU work. If the
computing worker fails before publishing, the lease expires and a later worker
can retry the computation.

Use timeout scope when a value should be cached in the configured cache backend
for a fixed duration without dependency tracking:

```python
@cached(scope="timeout", timeout=300)  # Cache for 5 minutes
def project_forecast(project_id: int) -> dict[str, float]:
    ...
```

`timeout` is required for `scope="timeout"` and is not accepted on the other
scopes. The cache entry expires after the given duration and is not invalidated
through the dependency index.

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

ORM-backed managers use this explicit run context to deduplicate repeated row
materialization for the same manager identity. The optimization is active only
inside an existing `CalculationRunContext`; constructing managers outside a run
context continues to read from the database normally. Negative lookups are not
cached, and ORM update/delete paths clear affected row entries in the active run
context after successful mutations.

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
and GraphQL data loaders.
