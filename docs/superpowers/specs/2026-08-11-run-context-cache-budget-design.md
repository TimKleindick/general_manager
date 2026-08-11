# Run-context cache memory budget

## Status

Approved for implementation on 2026-08-11.

## Problem

`CalculationRunContext` retains run-scoped values in ordinary dictionaries until
the outermost context exits. Long-running calculations and concurrent requests
can therefore retain enough ORM rows, managers, indexes, and dependency-cache
hits to exhaust a worker process's memory.

The context also holds pending dependency-cache publications. Those entries own
compute leases and must be published or discarded explicitly; treating them as
ordinary evictable cache values could lose writes or leak leases.

## Goals

- Provide one opt-in setting that limits the estimated memory retained by all
  active calculation run caches in a process.
- Evict least-recently-used entries across contexts while preserving existing
  cache miss, reload, publication, nesting, historical snapshot, and context
  variable behavior.
- Avoid adding a dependency.
- Preserve today's unlimited behavior when the setting is absent or `None`.

## Non-goals

- Enforcing a hard operating-system RSS limit. Python allocator overhead,
  shared references, native allocations, and values mutated after insertion
  prevent an in-process cache from promising that.
- Limiting non-cache memory used by Django or application code.
- Evicting or otherwise changing the dependency-publication batch.
- Adding cache statistics, metrics, or a new public management API.

## Configuration

Applications opt in through one byte-valued setting:

```python
GENERAL_MANAGER = {
    "RUN_CONTEXT_CACHE_MAX_BYTES": 256 * 1024 * 1024,
}
```

The normal `get_setting()` precedence applies, so the nested setting is checked
before `GENERAL_MANAGER_RUN_CONTEXT_CACHE_MAX_BYTES` and the legacy top-level
`RUN_CONTEXT_CACHE_MAX_BYTES` setting.

- Missing or `None`: unlimited, matching current behavior.
- `0`: do not retain eviction-safe run-cache entries.
- Positive integer: shared estimated byte budget for the process.
- Negative integers, booleans, and non-integer values: raise a clear Django
  configuration error when a `CalculationRunContext` is created.

The setting is expected to remain stable after application startup. Test-time
setting overrides are applied when a new context is created and may immediately
evict entries if they lower the process-wide budget.

## Architecture

### Process-local coordinator

`general_manager.cache.run_context` will own one process-local weighted LRU
coordinator. The coordinator tracks eviction-safe entries across every live
`CalculationRunContext`, their access order, and their estimated total size.
It does not own a second strong reference to contexts: context registrations use
weak references so abandoned contexts can be collected.

A re-entrant lock protects coordinator bookkeeping and cross-context eviction.
The existing `ContextVar` continues to select the active context; the change
does not share context contents or alter async propagation.

Each tracked entry is identified by its owning context, storage namespace, and
fully scoped key. Storage namespaces distinguish general run values from
dependency-cache hits without changing public keys.

### Retained storage

The coordinator covers:

- `_values`, including ORM results, hydrated managers, model-row indexes,
  bucket indexes, arbitrary `set()` values, and `index()`/`group_by()` results;
- prefetched dependency-cache hits that do not correspond to a pending
  publication.

Pending dependency-cache publications and their same-run hits remain pinned.
They continue to be controlled by
`dependency_cache_publish_batch_size`. Once a successful flush removes a
publication from the pending map, its hit may enter the eviction pool. Discarded
publication entries likewise make their retained hits eviction-safe;
`discard_dependency_cache_state()` continues to remove all hits explicitly.

### Size estimation

Insertion computes a conservative recursive estimate for the scoped key and
value using the standard library. The estimator:

- starts with `sys.getsizeof()`;
- follows built-in mappings and containers plus instance `__dict__` and declared
  slots;
- detects cycles within one estimate;
- ignores referents such as modules, classes, functions, and code objects that
  are not owned by the cache value;
- stops once its running total exceeds the configured budget.

Each entry is charged a fixed minimum accounting size that represents mapping
and LRU bookkeeping. This makes the byte budget implicitly bound entry count,
so a second user-facing entry limit is unnecessary.

Shared objects may be charged to more than one entry. This conservative
over-counting is preferable to silently exceeding the configured estimate.
Caller-owned mutable values can grow after insertion without notification, so
the contract is explicitly an insertion-time estimate. Internal cache
structures that GeneralManager mutates in place will be reweighed after those
mutations.

## Data flow and eviction

### Reads

Successful reads from tracked storage move the entry to the most-recently-used
end of the process-wide order. Misses preserve existing default and loader
behavior. `get()`, `has()`, `__contains__()`, `get_or_set()`, and specialized
helpers all pass through this behavior.

### Writes

Writing an entry follows this sequence:

1. Scope the key using the active historical fingerprint.
2. Estimate the scoped key and value.
3. If the estimate exceeds the entire budget, remove any older entry for the
   same key, return normally, and do not retain the new value.
4. Otherwise store or replace the value, update its accounting, and mark it
   most recently used.
5. Evict least-recently-used entries across all contexts until the estimated
   process total is within the configured budget.

`get_or_set()` still returns a successfully loaded value even when it is too
large to retain. A subsequent call is then a normal miss and invokes the loader
again. `set()` continues to return `None`.

### Removal and cleanup

Explicit prefix clears, dependency-state clears, and context exit unregister
removed entries and subtract their weights. Coordinator-driven eviction removes
the entry from its owning context without recursively updating the coordinator.
Weak-reference cleanup removes bookkeeping for a context that becomes
unreachable unexpectedly.

Nested re-entry of one context continues to retain values until its outermost
exit. Nested distinct contexts compete in the same global LRU and restore their
active `ContextVar` tokens exactly as today.

## Error handling and observability

- Configuration errors are raised before a newly created context can be used.
- Size estimation must not invoke arbitrary application methods. Unsupported
  objects receive their shallow size rather than making cache storage fail.
- An estimator failure falls back to a conservative shallow charge and does not
  change the result of the calculation.
- Eviction never publishes or discards dependency-cache entries.
- Debug logs record budget-driven eviction and values skipped for exceeding the
  full budget. Logging is not part of the correctness path.

## Compatibility

With the setting omitted, storage remains unlimited and existing behavior and
performance should remain materially unchanged. Public method signatures and
exports do not change. With a limit enabled, callers must already tolerate a
run-cache miss; eviction only makes such misses occur sooner.

The limit is per Python process, not shared across Gunicorn workers, Celery
workers, or other processes. A deployment that configures 256 MiB and runs four
workers permits up to approximately 256 MiB of tracked run-cache entries in
each worker.

## Testing

Focused unit tests will prove:

- absent and `None` settings retain current unlimited behavior;
- zero disables retention while loaders still return values;
- invalid values fail with a clear configuration error;
- insertion evicts the least-recently-used entry across two active contexts;
- a cache hit refreshes recency;
- replacement and explicit removal update accounting;
- a value larger than the whole budget is returned but not retained;
- historical snapshot keys remain isolated and independently evictable;
- prefetched dependency hits participate in eviction;
- pending publications and leases remain pinned and preserve flush/discard
  behavior;
- outermost context exit unregisters all entries;
- cycles and shared references do not break estimation;
- internally mutated ORM indexes are reweighed.

The existing `tests/unit/test_calculation_run_context.py` suite will remain the
primary regression surface. Relevant cache decorator and ORM tests will run
after the focused tests, followed by Ruff formatting/checking and mypy.

## Documentation

`docs/api/cache.md` and `docs/concepts/caching.md` will document the setting,
the per-process scope, LRU behavior, oversized-value bypass, pinned publication
state, and the distinction between an estimated cache budget and a hard RSS
limit.
