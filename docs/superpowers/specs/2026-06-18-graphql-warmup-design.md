# GraphQL Property Warm-Up

## Problem

Dependency-scoped `@graph_ql_property` values are warmed lazily today. A normal
GraphQL query computes a cold selected property through the existing cached
wrapper, records dependencies, and stores the dependency-cache entry. List
queries also bulk-read already-hot dependency-cache entries for selected fields
to avoid one cache read per item.

That request-path behavior does not proactively prepare expensive values before
clients ask for them. It also does not refresh timeout-based values before they
expire. Issue #194 calls out the operational risk: warm-up affects startup
behavior, cache state, background work, and cost, so it must be explicit,
bounded in lifecycle, observable, and opt in.

## Goals

- Provide framework-owned warm-up for selected GraphQL properties at startup.
- Re-warm dependency-cache entries after dependency invalidation when the
  framework knows how to reconstruct the entry.
- Keep timeout-cached warm entries fresh by refreshing them shortly before
  their configured timeout expires.
- Make the feature opt in at both the property level and the application
  settings level.
- Expose the same internals through public functions and management commands so
  applications can use their own scheduler instead of the built-in Celery/Beat
  integration.

## Scope

Warm-up applies only to `@graph_ql_property` descriptors with `warm_up=True`.
The only valid warm-up cache scopes are `cache="dependency"` and
`cache="timeout"`.

```python
@graph_ql_property(cache="dependency", warm_up=True)
def score(self) -> int:
    ...

@graph_ql_property(cache="timeout", timeout=300, warm_up=True)
def expensive_label(self) -> str:
    ...
```

Invalid declarations fail when the descriptor is created:

- `warm_up=True` with `cache="run"`.
- `warm_up=True` with `cache="none"`.
- `cache="timeout"` without a `timeout`.
- `timeout` with any cache scope other than `cache="timeout"`.

The first implementation uses `manager_class.all()` as the warm-up source for
every manager class that has at least one warmable property. For each instance,
warm-up executes each opted-in property with `getattr(instance, property_name)`.
That is the critical operation: property access reuses the existing cached
wrapper, compute lease, dependency tracking, and cache publication behavior.

There is no hard framework item limit. Warm-up executes in batches and logs a
warning when a manager crosses the configured warning threshold.

## Non-Goals

- Changing request-path GraphQL resolution semantics.
- Replacing the existing dependency-cache implementation.
- Adding a database-backed warm-up registry in the first version.
- Re-warming timeout-cache entries after data invalidation. Timeout entries are
  freshness-by-time, not dependency-indexed.
- Supporting custom warm-up scopes beyond `.all()` in the first version.

## Public API

`graph_ql_property` gains `cache="timeout"`, `timeout=...`, and `warm_up=...`
support:

```python
def graph_ql_property(
    func=None,
    *,
    sortable=False,
    filterable=False,
    query_annotation=None,
    cache="run",
    timeout=None,
    warm_up=False,
):
    ...
```

The framework exposes public functions for applications and management commands
to call directly:

- `warm_up_graphql_properties(manager_classes=None, property_names=None)`
- `warm_up_graphql_recipe(cache_key)`
- `refresh_due_graphql_warmup_recipes(limit=None)`
- `enqueue_graphql_warmup(manager_classes=None)`
- `enqueue_graphql_recipe_warmup(cache_keys)`

The first implementation places these functions in
`general_manager.api.graphql_warmup`. Re-exporting them from higher-level
modules is a separate public API decision.

## Settings

Settings use `GENERAL_MANAGER` keys with the existing legacy top-level fallback
style.

```python
GENERAL_MANAGER = {
    "GRAPHQL_WARMUP_ENABLED": False,
    "GRAPHQL_WARMUP_STARTUP_ENABLED": False,
    "GRAPHQL_WARMUP_STARTUP_MODE": "enqueue",  # "enqueue" or "sync"
    "GRAPHQL_WARMUP_BEAT_ENABLED": False,
    "GRAPHQL_WARMUP_BATCH_SIZE": 100,
    "GRAPHQL_WARMUP_WARNING_ITEMS_PER_MANAGER": 1000,
    "GRAPHQL_WARMUP_TIMEOUT_REFRESH_RATIO": 0.8,
    "GRAPHQL_WARMUP_REWARM_AFTER_INVALIDATION": True,
}
```

`GRAPHQL_WARMUP_ENABLED` gates all framework-owned automatic behavior.
Management commands and public functions will honor it by default, with
an explicit force option only if a command needs one.
The block above shows defaults. Applications must set
`GRAPHQL_WARMUP_ENABLED=True` before startup warm-up, Beat refresh, or
invalidation re-warm settings can perform framework-owned work.
`GRAPHQL_WARMUP_TIMEOUT_REFRESH_RATIO` accepts values in the inclusive range
`[0, 1]`; out-of-range values are clamped so refreshes cannot be scheduled
before the previous warm time or after the configured timeout window.

## Warm-Up Execution

Startup warm-up discovery scans registered manager classes for warmable
GraphQL properties. A property is warmable when:

- it is a `GraphQLProperty`;
- `prop.warm_up` is true;
- `prop.cache` is `"dependency"` or `"timeout"`.

For each manager class, warm-up iterates `manager_class.all()`. It processes
instances in batches controlled by `GRAPHQL_WARMUP_BATCH_SIZE`. Each batch runs
inside a `CalculationRunContext`, so dependency-cache misses are buffered and
published through the existing batch-publication path at the end of the batch.

For every instance/property pair, warm-up executes:

```python
getattr(instance, property_name)
```

If evaluation succeeds, the framework records the instance/property pair as a
candidate recipe. After the batch context exits successfully and dependency
publications have been flushed, it writes or updates the warm-up recipe for each
successful candidate. If evaluation fails, it logs manager, property,
identification, and exception information, then continues with the remaining
warm-up work.

Warm-up will not call private resolver internals to bypass the descriptor.
The descriptor path is the source of truth for cache keys, dependency tracking,
compute leases, and cache publication.

## Recipe Registry

The first implementation uses a cache-backed recipe registry. A recipe stores
the information required to reconstruct a warmed cache entry without scanning
`.all()` again.

```python
{
    "version": 1,
    "cache_key": "...",
    "manager_path": "myapp.managers.ProjectSummary",
    "property_name": "score",
    "identification": {"project": {"id": 1}, "date": "2026-06-18"},
    "cache": "dependency",
    "timeout": None,
    "refresh_at": None,
}
```

For timeout entries, `timeout` is the configured timeout in seconds and
`refresh_at` is the next pre-expiry refresh time. For dependency entries,
`refresh_at` is `None`.

The registry maintains:

- a per-cache-key recipe payload;
- an index of all recipe keys;
- an index of timeout recipe keys due for refresh.

The registry is best-effort and migration-free. If the cache is flushed, recipes
are lost. Startup or manual warm-up rebuilds them. This tradeoff keeps the first
version small and leaves room for a future database-backed registry if durable
refresh state becomes necessary.

## Dependency Invalidation Re-Warm

Dependency invalidation remains correctness-first:

1. Delete stale cache entries immediately.
2. Remove invalidated keys from the dependency index.
3. Collect invalidated cache keys.
4. Release the dependency-index lock.
5. Release the data-change publish barrier.
6. Look up warm-up recipes for the invalidated keys.
7. Enqueue recipe re-warm tasks for recipe-backed dependency entries.

Re-warm must not run while the dependency-index lock is held or while the
data-change publish barrier is active. The implementation will add a small
post-data-change drain point so invalidation can collect cache keys during the
signal receiver and enqueue re-warm only after `end_dependency_data_change()`.

Re-warm is best-effort. Missing recipes are ignored. Failed recomputes are
logged. The next lazy request-path access, startup warm-up, or manual warm-up can
repair missing entries.

## Timeout Refresh

`cache="timeout"` warm-up uses time freshness rather than dependency
invalidation. The warm-up recipe records `refresh_at` using:

```python
refresh_at = warmed_at + timeout * GRAPHQL_WARMUP_TIMEOUT_REFRESH_RATIO
```

The ratio defaults to `0.8`, so a 300-second entry is refreshed around 240
seconds after it was warmed. Valid values are in the inclusive range `[0, 1]`;
out-of-range values are clamped for the same reason as the settings parser.
A periodic refresh task scans due timeout recipes, acquires a per-recipe lock,
reconstructs the manager instance, executes the property, stores the refreshed
cache entry through the normal timeout cache path, and updates `refresh_at`.

If refresh fails, the task logs the failure and leaves the previous cache entry
to expire normally. A later refresh attempt or lazy request-path access can
recompute it.

## Scheduling

The framework owns optional scheduling while leaving escape hatches for
applications.

Built-in scheduling:

- Startup hook: when `GRAPHQL_WARMUP_ENABLED` and
  `GRAPHQL_WARMUP_STARTUP_ENABLED` are true.
- Startup mode: `enqueue` by default, `sync` only when explicitly configured.
- Celery task queue: `graphql.warmup`.
- Beat schedule: when `GRAPHQL_WARMUP_BEAT_ENABLED` is true.

App-owned scheduling:

- Call public warm-up functions directly.
- Run management commands from another scheduler.
- Disable built-in Beat while keeping recipes and tasks available.

Management commands:

```bash
python manage.py graphql_warmup
python manage.py graphql_warmup --manager ProjectSummary
python manage.py graphql_warmup_refresh_due --limit 1000
```

## Observability And Failure Handling

Warm-up logs include manager class, property name, count or batch number,
cache scope, and whether work ran synchronously or through a task.

Warnings:

- a manager crosses `GRAPHQL_WARMUP_WARNING_ITEMS_PER_MANAGER`;
- Celery/Beat scheduling is requested but Celery is unavailable;
- startup synchronous mode is enabled.

Failures:

- property evaluation errors are logged with identification and continue;
- recipe import or reconstruction errors remove or ignore that recipe;
- enqueue errors are logged and do not block mutations;
- timeout refresh failures do not delete the still-valid old timeout entry.

## Compatibility

Existing `@graph_ql_property` declarations keep their current behavior. The
default cache scope remains `cache="run"` and `warm_up` defaults to false.

Existing dependency-cached GraphQL properties still warm lazily on request-path
access. The new feature only adds proactive startup, re-warm, and timeout
refresh behavior for explicit opt-ins.

The addition of `cache="timeout"` to GraphQL properties mirrors the existing
lower-level `@cached(cache="timeout", timeout=...)` support.

## Testing

Implementation should be test-driven:

1. Add unit tests for descriptor validation:
   `warm_up=True` rejects `run` and `none`; timeout cache requires `timeout`;
   timeout is rejected for other scopes.
2. Add unit tests showing `graph_ql_property(cache="timeout", timeout=...)`
   uses the lower-level timeout cache path.
3. Add unit tests for recipe registry set/get/index/due behavior.
4. Add a warm-up execution test proving `.all()` is enumerated and the property
   is executed for each entry.
5. Add an integration test showing startup/manual warm-up creates dependency
   cache entries and recipes.
6. Add an integration or unit-level invalidation test showing invalidated keys
   are collected and recipe re-warm is enqueued only after the publish barrier
   is released.
7. Add timeout refresh tests showing due recipes recompute before expiry and
   update `refresh_at`.
8. Add settings tests showing no startup warm-up or Beat schedule is configured
   by default.

Relevant verification commands include focused unit tests for the new modules,
the existing GraphQL dependency-cache prefetch tests, and the caching integration
tests.
