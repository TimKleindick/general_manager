# Dependency Graph

Interfaces emit dependency information whenever they access or modify data. The dependency graph ties together managers, cached computations, and external subscribers.

## Change signals

Supported manager writes are wrapped by the `data_change` decorator in
`general_manager.cache.signals`. It emits `pre_data_change` and
`post_data_change`; `data_change` itself is not a signal. Receivers get the
sender manager class and operation context, including the action and
identification. The framework uses these events for cache invalidation.

For application jobs that must run after a durable database commit, use the
[workflow event guide](../../howto/workflow_events.md) and its transaction
boundary. A post-change signal can still run inside an outer transaction.

## Recording dependencies

When an interface resolves related data (for example, fetching a bucket of child managers), it calls `DependencyTracker.track()`. Cached functions using `@cached(cache="dependency")` persist these dependencies so that mutations can invalidate the correct cache entries.

Run-cached results live only inside `CalculationRunContext` and do not create
persistent dependency-index entries of their own. Their captured reads can
still contribute to a dependency-cached parent's invalidation metadata.
Dependency-aware properties opt in with
`@graph_ql_property(cache="dependency")`. Start a fresh run when a workflow
needs to observe newly loaded data; see
[run-context boundaries](../caching.md#run-context-storage).

## Graph traversal

`general_manager.cache.dependency_index` maintains a mapping of cache keys to dependency tuples. In a multi-process deployment, configure a shared Django default cache backend so that background workers and web servers stay in sync. When a change occurs, the index reveals which cached keys require eviction.

## Extending the graph

- For external data, define both how reads are tracked and how writes notify invalidation; upstream changes are not discovered automatically. Use timeout caching when that integration is unavailable.
- Wrap expensive interfaces in context managers that collect dependencies at a higher level when you aggregate multiple managers.
- Integrate with Celery tasks: enqueue follow-up jobs using the identifiers provided by the change signal.

A well-maintained dependency graph keeps your application fast without sacrificing consistency.
