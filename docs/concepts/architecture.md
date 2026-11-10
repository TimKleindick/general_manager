# Architecture

GeneralManager lets application code work with domain objects while an
interface supplies their data. Start with the `Project` from the
[quickstart](../quickstart.md):

| Expression | Meaning |
| --- | --- |
| `Project(id=project_id)` | Identify one project through its interface; this does not create a row |
| `Project.filter(name="Apollo")` | Build a bucket describing matching projects |
| `project.name` | Read an attribute through the interface |
| `Project.create(creator_id=user.pk, name="Apollo")` | Check write permission, validate, persist, and return a manager |

For a database manager, the interface works with a Django model. For a
calculation manager, it resolves declared inputs and computed properties.
Application code still works with managers and buckets, but supported operations
depend on the interface.

## Follow one request

A generated GraphQL list begins with a bucket, applies the query controls and
read-permission policy, and resolves the selected fields from permitted managers.
A normal Python write begins with the supplied actor, checks the manager's
write policy, and delegates validation and persistence to the interface.
Successful writes can then notify cache, history, search, and workflow machinery.

Plain Python reads do not infer a user or authorize themselves. A custom view
or service owns that read boundary. See [permissions](permission/index.md)
before exposing those values to a client.

The sections below explain each part. Dependency tracking is an additional
mechanism for reuse and invalidation; it is not needed to understand the first
manager definition.

## Managers

A manager is a lightweight wrapper around an interface. It exposes attributes declared as type hints and proxies CRUD operations to the interface. Each instance maintains an `identification` dictionary that uniquely identifies the underlying record. The implementation lives in `general_manager.manager.general_manager.GeneralManager`.

Key properties:

- Lazy attribute resolution: attributes are evaluated when accessed, allowing caching and property descriptors such as `@graph_ql_property`.
- Shared API: managers expose `create`, `update`, `delete`, `filter`, and `all`;
  the interface determines which operations it supports. For example,
  calculation interfaces support reads but do not persist writes.
- Write permissions: `create`, `update`, and `delete` check the nested
  `Permission` class with the supplied `creator_id` before delegating to the
  interface, unless explicitly bypassed. Plain Python reads do not apply a user
  policy automatically. Generated GraphQL owns its object and field read checks;
  custom endpoints must authorize their own reads. See
  [where permissions run](permission/index.md#where-permissions-run).

## Interfaces

Interfaces (see `general_manager.interface`) implement the actual persistence or computation strategy. GeneralManager ships with database-backed, read-only, and calculation interfaces. Interfaces provide:

- Field definitions that map to Django model fields or `Input` descriptors.
- CRUD implementations, including `create`, `update`, and `delete`.
- Hooks for validation (`clean`), change history recording, and dependency updates.

Interfaces expose an `.identification` structure used by managers to rehydrate objects after operations.

Optional operations follow the same boundary: `GeneralManager.create_many()`
delegates to the interface's `create_many` capability. The backend handler owns
batch transactions, lifecycle and error/progress semantics. Writable ORM bundles
provide this handler; other backends can supply their own atomic implementation
or reject unsupported batches before consuming input. See the
[capability contract](interfaces/capability-first.md#optional-bounded-creation).

## Buckets

Buckets (`general_manager.bucket`) behave like querysets tailored for managers. They allow filtering, slicing, sorting, combining (`|`), and grouping (`group_by`). Buckets keep type information, so type checkers know which manager they hold. Operations on buckets defer actual evaluation to the interface layer, so chaining filters is efficient.

## Dependency tracking and caching

The dependency tracker records manager reads made during a calculation. The dependency index connects those reads to persistent cache entries; supported data-change signals trigger invalidation of affected entries. Together they let a later read recompute a derived value after its source data changes. See [caching and dependency tracking](caching.md) for cache scopes and their boundaries.

To take advantage of the tracker:

- Wrap expensive resolvers with `@cached(cache="dependency")` or `@graph_ql_property(cache="dependency")` when they should persist across runs and invalidate through the dependency index.
- Use `@cached(cache="timeout", timeout=seconds)` when time-based expiry is enough and dependency tracking would add unnecessary overhead.
- Use the default `@cached` or `@graph_ql_property` run cache for helpers that only need reuse within one request, calculation graph, bulk operation, or background run.
- Mark inputs and outputs clearly so dependencies can be resolved.
- Configure a shared cache backend when you run multiple worker processes.

## Lifecycle

1. The application imports manager classes during Django start-up.
2. Interfaces register models or calculation inputs.
3. Requests instantiate managers or buckets to serve data.
4. Changes emit dependency signals; cache subscribers invalidate or recompute derived data.

Understanding this flow will help you diagnose issues and design managers that scale with your domain.
