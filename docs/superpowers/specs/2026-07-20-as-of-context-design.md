# As-Of Context Design

**Status:** Approved design
**Date:** 2026-07-20

## Summary

GeneralManager supports point-in-time ORM reads through explicit
`search_date` arguments, but requiring every constructor, query, relation, and
calculation call to forward that argument makes operation-wide historical work
fragile. GraphQL cannot reliably propagate the value through arbitrary resolver
and calculation call chains.

Add a dedicated `as_of` execution context that supplies one normalized
historical date to every compatible read in its scope. The context is
read-only, rejects mixed snapshot dates, fails closed when an interface cannot
honor historical reads, and isolates all affected caches by date. GraphQL
exposes the behavior through a query operation directive:

```graphql
query HistoricalProjects($date: DateTime!) @asOf(date: $date) {
  projectList {
    items {
      id
      name
    }
  }
}
```

## Goals

- Select one historical date for an entire Python or GraphQL operation.
- Remove the need to pass `search_date` through transitive calculation and
  resolver call chains.
- Preserve existing explicit `search_date` APIs.
- Prevent historical/current or different-date values from being mixed.
- Reject GeneralManager mutations before permissions or persistence side
  effects while historical state is active.
- Keep current, date-A, and date-B cache entries isolated.
- Follow Python `contextvars` concurrency semantics.

## Non-Goals

- Reconstruct historical state for request-backed remote resources.
- Make arbitrary application database writes detectable or reversible.
- Add historical subscriptions or historical mutations.
- Replace `CalculationRunContext` or combine its responsibilities with
  historical state.
- Change the existing history-table cutoff/buffer behavior.

## Public Python API

Export the context from the stable API surface:

```python
from general_manager.api import as_of, current_as_of_date

with as_of("2022-01-01"):
    project = Project(1)
    projects = Project.filter(status="active")
```

Both positional and explicit keyword forms are supported:

```python
as_of("2022-01-01")
as_of(search_date="2022-01-01")
```

`as_of` accepts an ISO-8601 string, `datetime.date`, or `datetime.datetime`.
Context entry immediately converts the input to one timezone-aware `datetime`
using the same timezone policy as ORM historical lookup. Date-only inputs mean
midnight in Django's current timezone. `current_as_of_date()` returns the
canonical aware datetime or `None`.

Invalid or missing input raises `InvalidSearchDateError` before context state
changes. Supplying both positional and keyword values is invalid under normal
Python call binding.

## Context State and Nesting

A dedicated module owns a `ContextVar[datetime | None]`; historical state does
not live in `CalculationRunContext`.

Entering the first `as_of` scope sets the canonical date and retains the token.
Nested entry with an equal normalized date is allowed and is idempotent. Nested
entry with a different date raises `HistoricalContextConflictError` without
changing the outer state. Every successful entry restores its token in a
`finally` path, including when wrapped work raises.

The date follows ordinary `contextvars` behavior: copied async task contexts
inherit it, unrelated threads do not inherit it automatically, and request
scopes cannot leak state after exit.

The context composes with calculation caching but neither context owns the
other:

```python
with as_of(search_date="2022-01-01"):
    with CalculationRunContext():
        ...
```

GraphQL uses that ordering. Python callers may also run sequential `as_of`
scopes inside one longer `CalculationRunContext`; cache isolation must remain
correct in that arrangement.

## Effective-Date Resolution

All framework historical entry points use one resolution rule:

1. Normalize an explicit `search_date`, if present.
2. Read the active context date.
3. If both exist and are unequal, raise
   `HistoricalContextConflictError`.
4. Otherwise use the explicit date, the context date, or `None`, in that order.

This applies to manager construction, trusted ORM hydration, ORM query
capabilities, bucket derivation, and relation hydration. Existing explicit
calls outside a context remain valid:

```python
Project(1, search_date="2022-01-01")
Project.filter(search_date="2022-01-01")
```

Managers and buckets retain their effective date. Framework-owned field and
property resolution, GraphQL resolution, bucket materialization, and bucket
derivation validate that retained date against the active context. A live or
differently dated value consumed in a historical scope raises
`HistoricalContextConflictError`. This prevents lazy buckets or manager
instances created outside the scope from introducing current data into a
historical result.

## Interface Behavior

ORM-backed database, existing-model, and read-only interfaces receive the
effective date for instance and queryset reads. Their existing history
capability decides how to retrieve rows and retains the current historical
lookup buffer semantics.

Calculation interfaces are context-transparent. They do not receive a new
constructor argument; manager reads performed while resolving calculation
inputs, combinations, relations, or properties consult the active date. This
allows calculations to work historically without parameter plumbing.

Request-backed interfaces cannot provide a GeneralManager history snapshot and
therefore raise `HistoricalReadNotSupportedError` before transport activity.
Any other interface that cannot demonstrate historical-read support also fails
closed. ORM interfaces missing usable history support normalize the existing
capability failure to `HistoricalReadNotSupportedError`, preserving the
original exception as its cause.

Historical relation traversal must retain the source manager's effective date
when building related managers and buckets. It must never fall back to a live
related row.

## Mutation Policy

An active `as_of` scope is read-only. `GeneralManager.create`, `update`, and
`delete` invoke a shared guard before permission checks, persistence calls,
remote transport, or data-change notification work. The guard raises
`HistoricalMutationError`; `ignore_permission=True` does not bypass it.

Interface mutation entry points use the same guard as defense in depth for
callers that use those framework APIs directly. This guarantee covers
GeneralManager-managed mutation paths. Arbitrary application code that writes
directly through Django or another client remains outside the context's ability
to detect.

GraphQL exposes `@asOf` only on query operations, so schema validation rejects
its use on mutations and subscriptions. A mutation invoked while Python has
already activated `as_of` is still rejected by the runtime mutation guard.

## GraphQL Directive

Register the following directive with every generated GeneralManager schema:

```graphql
directive @asOf(date: DateTime!) on QUERY
```

The directive accepts a literal or a variable. The GraphQL boundary selects the
same operation GraphQL will execute, resolves its directive argument using
coerced variables, and normalizes the result through the public date
normalizer. Missing, invalid, or conflicting input produces a GraphQL error
before resolver execution.

For an operation carrying the directive, the view enters `as_of` around the
whole query and enters or reuses `CalculationRunContext` inside it. Both
contexts exit on success, validation failure, resolver failure, and response
formatting failure. Queries without the directive retain current behavior.

Each GraphQL HTTP batch item resolves and enters its own context. An item's date
must not affect another item. The directive remains visible through schema
introspection and GraphiQL.

## Cache Correctness

The canonical effective datetime, whether supplied by `as_of` or explicitly,
is a historical cache fingerprint. Every cache identity capable of storing
date-dependent work includes that fingerprint. Relevant paths include:

- cache-decorated and dependency-cache calculations;
- `CalculationRunContext` values;
- ORM query and bucket reuse;
- trusted manager hydration;
- calculation bucket results; and
- GraphQL property prefetch and warm-up identities.

The integration belongs in shared cache-key construction wherever possible so
transitive calculations cannot accidentally omit the date. Exact normalized
timestamps are distinct cache namespaces. This intentionally prioritizes
correctness over reuse for highly granular historical dates.

Existing historical dependency tracking and invalidation behavior remains in
place. A current data change may conservatively invalidate a historical entry;
incorrect reuse across snapshot dates is never permitted.

## Errors

Introduce a small public error family:

- `InvalidSearchDateError`: the requested value cannot be parsed or normalized.
- `HistoricalContextConflictError`: active, explicit, nested, manager, or bucket
  dates disagree.
- `HistoricalMutationError`: a GeneralManager mutation is attempted while
  `as_of` is active.
- `HistoricalReadNotSupportedError`: a read reaches an interface that cannot
  honor the snapshot.

GraphQL maps these failures to stable extension codes while presenting concise
messages and retaining internal exception causes only for server-side logging.

## Compatibility

No context and no explicit `search_date` means no runtime behavior change.
Explicit `search_date` calls remain supported and use the same resolver as
ambient dates. Cache-key versioning may cause a one-time cold cache after
deployment; the new directive and Python exports are otherwise additive.

Code that previously mixed a manager or bucket created at one date with an
explicitly different date will now receive a conflict when an `as_of` scope is
active. This is intentional fail-closed behavior.

## Verification

Tests must cover:

- normalization of ISO date strings, ISO datetime strings, `date`, naive
  `datetime`, and aware `datetime`;
- invalid input and state cleanup after entry or wrapped-work failure;
- same-date nesting and conflicting nesting;
- matching and conflicting explicit `search_date` values;
- historical construction, `get`, `filter`, `exclude`, `all`, relations, and
  trusted hydration;
- calculations whose direct and transitive manager dependencies are historical;
- rejection of create, update, and delete before permissions and side effects;
- `ignore_permission=True` remaining blocked;
- request-backed and unsupported reads failing before external activity;
- live, date-A, and date-B isolation in persistent and run-scoped caches;
- sequential dates inside one `CalculationRunContext`;
- async propagation, thread isolation, and exceptional cleanup;
- literal and variable `@asOf` arguments and schema introspection;
- rejection of the directive on mutations and subscriptions;
- GraphQL batch isolation and cleanup after resolver errors; and
- unchanged behavior for Python and GraphQL operations without historical
  context.

Run focused unit and integration tests first, followed by Ruff, mypy, and the
full pytest suite before publishing the implementation.

## Rejected Alternatives

### Store the date in `CalculationRunContext`

This couples historical reads to calculation caching, forces ordinary Python
historical work to create a calculation context, and makes nested calculation
contexts liable to hide or replace the date.

### Propagate `search_date` implicitly through call arguments

Middleware and calculation helpers cannot reliably cover arbitrary custom
properties and transitive call chains. This recreates the fragile plumbing the
feature is intended to remove.

### Use GraphQL request `extensions`

An extension can carry operation-wide state but is less discoverable and less
schema-driven than an operation directive. `@asOf(date:)` supports variables,
normal GraphQL validation, introspection, and aligns with KnowledgeHub's proven
scenario-context integration pattern.
