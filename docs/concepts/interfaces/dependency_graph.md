# Dependency Graph

Interfaces emit dependency information whenever they access or modify data. The dependency graph ties together managers, cached computations, and external subscribers.

## Change signals

CRUD operations on `GeneralManager` instances emit the `data_change` signal defined in `general_manager.cache.signals`. Subscribers receive the manager name, the type of operation, and the identification of the affected record. Use this to invalidate downstream caches or trigger asynchronous jobs.

## Recording dependencies

When an interface resolves related data (for example, fetching a bucket of child managers), it calls `DependencyTracker.track()`. Cached functions using `@cached` persist these dependencies so that mutations can invalidate the correct cache entries.

## Graph traversal

`general_manager.cache.dependency_index` maintains a mapping of cache keys to dependency tuples. In a multi-process deployment, store this index in a shared backend (Redis or PostgreSQL) so that background workers and web servers stay in sync. When a change occurs, the index reveals which cached keys require eviction.

## Extending the graph

- Emit custom dependency events when you access non-Manager data (for example, external APIs) so that you can invalidate caches proactively.
- Wrap expensive interfaces in context managers that collect dependencies at a higher level when you aggregate multiple managers.
- Integrate with Celery tasks: enqueue follow-up jobs using the identifiers provided by the change signal.

A well-maintained dependency graph keeps your application fast without sacrificing consistency.
