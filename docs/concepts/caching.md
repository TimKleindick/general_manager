# Caching and Dependency Tracking

GeneralManager keeps cached data in sync by recording dependencies during read operations and invalidating them when data changes. Understanding the mechanism helps you build fast, correct APIs.

## Dependency tracker

When a manager or bucket resolves data, it records the operation in `DependencyTracker`. The tracker stores tuples of `(manager_name, operation, identifier)`. Any code wrapped in a `with DependencyTracker()` context receives a set of dependencies it touched.

CRUD methods (`create`, `update`, `deactivate`) emit cache invalidation signals that match these tuples. When a match occurs, in-memory caches and persistent dependency indices can expire stale entries.

## Caching helper

Use the `@general_manager.cache.cacheDecorator.cached` decorator to memoise expensive functions while automatically tracking dependencies:

```python
from general_manager.cache.cacheDecorator import cached

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
