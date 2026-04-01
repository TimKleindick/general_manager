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

creates two successive dependency entries, because the lookups were applied in separate calls.

## Request-backed buckets

Request-backed buckets are currently the exception. They still use eager `request_query` dependency tracking at request-plan construction time. Their invalidation model is separate from the deferred ORM bucket behavior described above.

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
- Prefer grouping logically inseparable lookup clauses into one `filter()` or `exclude()` call when you want them invalidated as one composite dependency.
- Treat request-backed bucket caching separately from ORM-backed bucket caching when debugging invalidation behavior.
- Regularly test cache invalidation by running workflows that update managers and verifying that cached results change accordingly.

For low-latency APIs, combine the cached decorator with bucket-level prefetching and GraphQL data loaders.
