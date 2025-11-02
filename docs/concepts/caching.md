# Caching and Dependency Tracking

GeneralManager keeps cached data in sync by recording dependencies during read operations and invalidating them when data changes. Understanding the mechanism helps you build fast, correct APIs.

## Dependency tracker

When a manager or bucket resolves data, it records the operation in `DependencyTracker`. The tracker stores tuples of `(manager_name, operation, identifier)`. Any code wrapped in a `with DependencyTracker()` context receives a set of dependencies it touched.

CRUD methods (`create`, `update`, `delete`) emit cache invalidation signals that match these tuples. When a match occurs, in-memory caches and persistent dependency indices can expire stale entries.

### Composite filters and excludes

Multiple lookups that are applied in a single `filter()` or `exclude()` call are treated as a single dependency. The dependency index records the complete filter payload and only invalidates the cache when **all** stored conditions match the before/after state. This prevents unnecessary evictions when one attribute leaves the result set but the remaining filters would still exclude the record.

Successive filter/exclude calls still register individually. If you need to keep them together, group the lookups in one call so the dependency index can evaluate the combination.

## Caching helper

Use the `@general_manager.cache.cache_decorator.cached` decorator to memoise expensive functions while automatically tracking dependencies:

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

When the wrapped function runs, it records every manager it touches. Subsequent calls reuse the cached value until a tracked dependency changes.

You can also specify a timeout in seconds:

```python
@cached(timeout=300)  # Cache for 5 minutes
def project_forecast(project_id: int) -> dict[str, float]:
    ...
```
When `timeout` is set, the cache entry expires after the given duration no matter if the tracked dependencies change.


## Recommended practices

- Configure a shared cache backend (Redis or Memcached) in production so dependency signals reach all processes.
- Keep cache keys deterministic by relying on the built-in `make_cache_key` helper.
- Avoid caching code paths that bypass permission checks. The cached decorator records dependencies, not the caller identity.
- Regularly test cache invalidation by running workflows that update managers and verifying that cached results change accordingly.

For low-latency APIs, combine the cached decorator with bucket-level prefetching and GraphQL data loaders.
