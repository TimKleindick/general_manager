# Architecture

GeneralManager extends Django with a declarative layer that keeps business logic close to the objects you expose. The core architecture revolves around four components: managers, interfaces, buckets, and the dependency tracker.

## Managers

A manager is a lightweight wrapper around an interface. It exposes attributes declared as type hints and proxies CRUD operations to the interface. Each instance maintains an `identification` dictionary that uniquely identifies the underlying record. The implementation lives in `general_manager.manager.general_manager.GeneralManager`.

Key properties:

- Lazy attribute resolution: attributes are evaluated when accessed, allowing caching and property descriptors such as `@graph_ql_property`.
- Uniform API: `create`, `update`, `deactivate`, `filter`, and `all` methods behave consistently across interfaces.
- Permission hook: operations delegate to the nested `Permission` class before touching the database.

## Interfaces

Interfaces (see `general_manager.interface`) implement the actual persistence or computation strategy. GeneralManager ships with database-backed, read-only, and calculation interfaces. Interfaces provide:

- Field definitions that map to Django model fields or `Input` descriptors.
- CRUD implementations, including `create`, `update`, and `deactivate`.
- Hooks for validation (`clean`), change history recording, and dependency updates.

Interfaces expose an `.identification` structure used by managers to rehydrate objects after operations.

## Buckets

Buckets (`general_manager.bucket`) behave like querysets tailored for managers. They allow filtering, slicing, sorting, combining (`|`), and grouping (`group_by`). Buckets keep type information, so type checkers know which manager they hold. Operations on buckets defer actual evaluation to the interface layer, so chaining filters is efficient.

## Dependency tracking and caching

Every data-changing operation emits signals captured by the dependency tracker (`general_manager.cache.cache_tracker.DependencyTracker`). The tracker maps attribute access to cache keys and invalidates dependent entries when mutations occur. This mechanism powers automatic cache expiry, incremental recalculations, and GraphQL query caching.

To take advantage of the tracker:

- Wrap expensive resolvers with the `@cached` decorator or the `@graph_ql_property` decorator if you want to expose them in GraphQL.
- Mark inputs and outputs clearly so dependencies can be resolved.
- Configure a shared cache backend when you run multiple worker processes.

## Lifecycle

1. The application imports manager classes during Django start-up.
2. Interfaces register models or calculation inputs.
3. Requests instantiate managers or buckets to serve data.
4. Changes emit dependency signals; cache subscribers invalidate or recompute derived data.

Understanding this flow will help you diagnose issues and design managers that scale with your domain.
