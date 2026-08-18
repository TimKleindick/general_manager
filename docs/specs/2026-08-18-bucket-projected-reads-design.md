# Materialized Projected Reads for All Bucket Types

## Context

Issue [#465](https://github.com/TimKleindick/general_manager/issues/465)
proposes projected reads for reporting, export, dashboard, and calculation
workloads that need a small set of manager attributes. Today those workloads
must iterate `GeneralManager` instances even when they need no domain behavior.
That construction is unnecessary when a backend already has normalized source
values available, and bypassing the bucket API to avoid it loses framework
semantics.

The feature applies to the complete bucket abstraction:

- `DatabaseBucket` wraps ORM queries.
- `CalculationBucket` generates input combinations.
- `RequestBucket` materializes remote payloads.
- `GroupBucket` exposes aggregated `GroupManager` rows.
- Third-party and application buckets implement the public `Bucket` contract.

The goal is one backend-neutral materialized projection API with safe native
optimizations where existing source data can reproduce normal manager attribute
values exactly.

## Goals

- Add `values()` and `values_list()` to every bucket type.
- Return detached, fully materialized Python snapshots in bucket iteration
  order.
- Restrict fields to a discoverable public namespace.
- Preserve attribute values, exceptions, historical checks, authorization
  boundaries, dependency tracking, and active-run invalidation semantics.
- Avoid manager construction for safe database fields, calculation inputs, and
  raw request fields.
- Reuse equivalent projections inside an active `CalculationRunContext` without
  exposing mutable cached state.
- Give custom buckets correct portable behavior without requiring new methods.

## Non-goals

- Lazy or chainable projection result objects.
- Implicit projection of every field.
- Partial native optimization when one requested field requires manager access.
- Native projection of `GraphQLProperty` values.
- Native projection of ORM relations or arbitrary custom derived descriptors.
- A public cache row-limit option.
- `values_index_many()` or other projected indexing helpers.
- Authorization of an unrestricted source bucket.

## Public API

`Bucket` gains two concrete methods:

```python
def values(self, *fields: str) -> tuple[dict[str, object], ...]: ...

@overload
def values_list(
    self,
    *fields: str,
    flat: Literal[False] = False,
) -> tuple[tuple[object, ...], ...]: ...

@overload
def values_list(
    self,
    *fields: str,
    flat: Literal[True],
) -> tuple[object, ...]: ...
```

The methods are concrete so existing custom bucket implementations inherit the
portable manager-iteration behavior. The `Literal` overloads distinguish tuple
rows from flat scalar results for type checkers.

The returned shapes are:

```python
bucket.values("date", "revenue")
# ({"date": date(...), "revenue": Measurement(...)}, ...)

bucket.values_list("date", "revenue")
# ((date(...), Measurement(...)), ...)

bucket.values_list("date", flat=True)
# (date(...), ...)
```

The outer collection is always a tuple. Tuple-style `values_list()` rows are
tuples. Each `values()` call creates new ordinary row dictionaries, so adding,
removing, or replacing keys cannot affect a later same-run projection result.
Projection is a shallow snapshot: attribute values themselves are not deep
copied. Mutable domain values therefore retain the same identity and mutation
semantics as ordinary manager attribute access.

`GroupBucket` is registered as a virtual `Bucket` subclass rather than
inheriting `Bucket`. It therefore defines both public methods directly and
delegates to the same internal projection engine. Its projection rows are the
existing `GroupManager` rows; projection never flattens group members.

## Projection Field Namespace

A valid projection field is either:

- a name returned by `manager_class.Interface.get_attributes()`; or
- a `GraphQLProperty` name declared on the manager class.

Backend column names, request payload paths, arbitrary manager properties, and
private attributes are not accepted. At least one explicit field is required.
Adding a new manager attribute does not silently alter an existing projection.

The ordered field tuple is part of the result contract and cache identity.
Duplicate field names are rejected rather than collapsed.

## Validation and Public Errors

Validation completes before historical checks, cache access, or bucket
evaluation. The order is:

1. No fields: raise `EmptyProjectionFieldsError`.
2. A non-string field: raise `TypeError`.
3. Duplicate fields: raise `DuplicateProjectionFieldError`.
4. A name outside the public field namespace: raise
   `UnknownProjectionFieldError`.
5. A non-boolean `flat` value: raise `TypeError`.
6. `flat=True` with other than one field: raise
   `FlatProjectionFieldCountError`.

The four semantic exception classes are exported through the stable bucket
public API. Constructor details and message text remain diagnostic rather than
stable inspection contracts.

Once validation succeeds, exceptions raised by normalizers, descriptors,
properties, ORM conversion, request schema validation, or historical checks
propagate unchanged. The portable path does not translate an `AttributeError`
raised from inside a valid public attribute getter.

## Architecture

A new internal `general_manager.bucket.projection` module owns:

- field validation;
- the public semantic exception definitions;
- canonical projection cache orchestration;
- dependency capture and replay;
- the 10,000-row retention rule;
- conversion from canonical rows to public result shapes.

Projection helpers, field plans, cache entries, and backend hooks remain
internal. Only the two methods and semantic exceptions are public.

The engine requests canonical rows through a private bucket hook equivalent to:

```python
def _project_rows(
    self,
    fields: tuple[str, ...],
) -> tuple[tuple[object, ...], ...]: ...
```

`Bucket` implements the default hook by iterating managers and reading the
requested attributes. `DatabaseBucket`, `CalculationBucket`, and
`RequestBucket` override it with an all-or-nothing native plan. If any requested
field is not safe for that backend's native plan, the override delegates the
entire projection to the portable implementation.

`GroupBucket` delegates its public methods to the shared engine and supplies a
portable hook over its `GroupManager` rows.

## Evaluation Flow

Each public call performs the following steps:

1. Validate fields and `flat` arguments.
2. Run the bucket's historical compatibility guard when one exists.
3. Read the current `CalculationRunContext`; do not create a temporary context.
4. Derive the source signature and look up the canonical rows when a context is
   active.
5. On a cache hit, replay the stored dependency set.
6. On a miss, evaluate the selected projection hook inside a
   `DependencyTracker`.
7. Store canonical rows and frozen dependencies only when a context is active
   and the result contains at most 10,000 rows.
8. Convert canonical rows into fresh dictionaries, tuple rows, or a flat scalar
   tuple.

All three public result modes share one canonical cache entry for the same
source signature and ordered fields. Output mode is not part of the cache key
because it changes only the public view of the same values.

Calls outside an active run evaluate normally and do not store projection cache
entries. Results above 10,000 rows still succeed; they are simply not retained
for reuse. The projection limit is intentionally separate from the existing
1,000-row ORM snapshot and bucket-index defaults because projections retain only
requested values and target bulk workloads. Configured
`RUN_CONTEXT_CACHE_MAX_BYTES` admission and eviction continues to provide the
process-level memory budget when enabled.

## Source Signatures

Projection reuses the existing bucket source-signature model:

- `DatabaseBucket` uses its conservative query signature and falls back to
  object identity when the query is unsafe to identify structurally.
- `CalculationBucket` uses its stable plan signature.
- `RequestBucket` uses its request-plan signature and falls back to object
  identity for manager-only materialized data.
- Base/custom buckets use class, manager class, and object identity.
- `GroupBucket` uses conservative object identity in the initial release.

Equivalent database, calculation, and request plans can therefore share an
active-run projection. Custom and grouped buckets reuse only repeated calls on
the same object unless a future backend provides an explicit stable signature.

## Canonical Cache Entry and Invalidation

Add a projection namespace to `CalculationRunContext`. Each entry stores:

- tuple-structured canonical rows;
- the frozen dependencies captured while producing them.

The cache key includes the projection namespace, source signature, and ordered
field tuple. The outer result and canonical row containers are tuples, but
contained attribute values are not recursively frozen or copied. Cache reads
replay dependencies before returning the rows. Cache writes participate in the
existing run-context memory accounting.

Add `clear_bucket_projections()` to discard the projection namespace. Database
mutation hooks that currently clear ORM bucket snapshots and bucket indexes
also clear all projection entries in the active run. Coarse invalidation is
intentional: projection dependencies may cross manager and backend boundaries,
and retaining a narrower entry after an in-run database mutation is unsafe.

Remote changes cannot currently invalidate request data locally. A materialized
request projection follows the existing request-bucket snapshot semantics for
the life of that object/run.

## Portable Projection

The portable hook iterates the source rows in their normal order and resolves
each requested field with ordinary attribute access. This path establishes the
reference semantics for values, exceptions, dependencies, and historical
behavior.

It covers:

- custom bucket implementations;
- `GroupBucket` and its `GroupManager` aggregates;
- every `GraphQLProperty`;
- ORM relations and unsafe custom descriptors;
- manager-only request buckets;
- any mixed request containing at least one non-native field.

Native backend tests compare against this reference behavior.

## DatabaseBucket Native Projection

The database path is eligible only when every requested field is one of:

- a concrete non-relation ORM scalar whose projected value matches the public
  interface accessor; or
- a logical `MeasurementField` with both backing columns available.

The native query selects the requested concrete columns plus the primary key.
The primary key is internal and is not included in the public result unless the
caller requested the corresponding public attribute.

For each returned row the path:

- reconstructs a `Measurement` from value and unit columns using the same
  conversion and stored-unit fallback semantics as the descriptor;
- normalizes file/image values to the same public string representation;
- calls `_track_effective_dependencies()` once for the bucket evaluation;
- explicitly calls the manager class's identification-dependency hook for the
  row;
- preserves queryset ordering, slicing, database alias, annotations, filters,
  excludes, and search-date state.

Relations, collection fields, generic relations, derived/custom descriptors,
and `GraphQLProperty` fields make the entire call portable. Native planning does
not silently return foreign-key IDs where manager access would return manager
objects.

The existing `_ensure_as_of_compatible()` guard runs before cache access.
Historical querysets use their selected historical model/rows; a projection may
not substitute live data or change the bucket's effective instant.

## CalculationBucket Native Projection

The calculation path is eligible only when every requested field is a declared
calculation input. It begins from the same generated identification combinations
used by bucket iteration and retains:

- input and property filters/excludes;
- dependent possible-value ordering;
- allowed-identification subsets;
- configured sorting and reversal;
- the bucket's bound historical instant.

Projected input values use the same `Input.cast()` normalization and declared
dependency resolution as calculation interface attribute access. Manager-valued
inputs explicitly replay their manager identification dependencies, including
when a resolved input value is reused.

Any `GraphQLProperty` or other non-input field makes the entire call portable.
The native path may avoid manager construction, but it may not return raw
combination values when normal input access would cast or normalize them.

## RequestBucket Native Projection

Refactor request materialization into two internal stages:

1. Raw materialization executes the request plan, applies local predicates,
   enforces local-pagination constraints, stores raw payloads, and records count
   metadata without constructing managers.
2. Manager materialization builds and payload-caches managers from the stored
   raw payloads when ordinary iteration or fallback projection needs them.

The native path is available when raw payloads exist or can be obtained from a
lazy request plan. It resolves:

- input fields from `extract_identification(payload)`; and
- declared request fields through `resolve_payload_value(payload, field_name)`.

This preserves payload source paths, defaults, required-field failures, and
normalizers. Each row's extracted identification is tracked explicitly when
manager construction is skipped.

Manager-only buckets created by slicing, union, `none()`, or
`with_instances()` use the portable path unless matching raw payloads are still
available for every retained item. The initial implementation need not add raw
payload preservation to those transformations.

`ensure_as_of_read_supported()` runs before raw projection or manager
materialization. A projection executes a lazy request plan no more often than
ordinary bucket materialization.

## GroupBucket Projection

`GroupBucket` projects the already-materialized `GroupManager` rows in their
current order. Grouping-key fields return their exact group values. Other
exposed fields use existing `GroupManager` aggregation and dependency behavior.

Sorting or slicing a `GroupBucket` changes the projected row order exactly as it
changes iteration order. Projection does not regroup data, change the basis
bucket, or flatten group members. Historical compatibility continues to
delegate to the basis bucket before cache access and evaluation.

## Dependency Semantics

Portable projection naturally captures the dependencies created by manager
construction and attribute access. Native implementations must reproduce every
dependency that portable evaluation would create for the same fields and rows,
including:

- database filter, exclude, and sort dependencies;
- each returned manager identification;
- manager-valued calculation inputs;
- dependencies raised while resolving input domains, normalizers, or other
  native values.

Cache hits replay the frozen source dependency set into the surrounding
`DependencyTracker`. Result shaping creates no new dependencies.

Projection is an active-run snapshot just like existing run-scoped bucket
indexes. External changes are not expected to become visible without a new run,
except that framework-managed database mutations inside the run explicitly
clear the projection namespace.

## Authorization Boundary

Buckets are not independently permission-aware. GraphQL and other callers apply
authorization before terminal bucket operations. Projection evaluates exactly
the supplied bucket and does not reconstruct a broader query or source plan.

An authorized subset therefore remains restricted. Calling projection on an
unrestricted bucket has the same authorization characteristics as iterating
that unrestricted bucket. No `user`, `info`, permission object, or implicit
authorization lookup is added to the projection API.

## Compatibility

This is an additive public API. Existing bucket construction, iteration,
filtering, grouping, sorting, slicing, pickling, and equality behavior remains
unchanged. Projection results are plain tuples, dictionaries, and domain values,
so no new serialization or pickling contract is required.

No database migration, setting, or dependency is added. The 10,000-row limit is
an internal cache-retention policy, not a result limit or public API guarantee.

## Testing Strategy

Follow test-driven development, adding focused failing tests before production
changes.

### Shared engine

- Validation order and each documented error.
- Ordered field identity and duplicate rejection.
- Canonical tuple construction.
- Fresh `values()` dictionaries across calls and cache hits.
- Cross-mode cache sharing between `values()` and `values_list()`.
- Active-context-only caching and dependency replay.
- Admission at 10,000 rows and bypass at 10,001 rows.
- Distinct entries for distinct source signatures or field order.

### Base and custom buckets

- Portable values and values-list shapes.
- Source iteration order and normal attribute exceptions.
- Same-object cache reuse without cross-object reuse.

### DatabaseBucket

- Native scalar projection and ORM query counts.
- Logical `MeasurementField` reconstruction, including stored-unit fallback.
- File/image public value normalization.
- Explicit filter/exclude/sort and identification dependencies.
- Filters, excludes, annotations, sorts, slices, and database aliases.
- Historical reads and incompatible active contexts.
- All-or-nothing fallback for relations, custom descriptors, and
  `GraphQLProperty` values.
- Unsafe query signatures evaluate correctly without equivalent-plan reuse.

### CalculationBucket

- Direct normalized input projection without manager construction.
- Dependent inputs and input normalizers.
- Manager-valued input dependency tracking.
- Filters, excludes, sorting, reverse ordering, and allowed identifications.
- Historical compatibility.
- Entire-call fallback for a `GraphQLProperty` or mixed projection.

### RequestBucket

- Raw projection without manager construction.
- Source paths, defaults, required-field errors, and normalizers.
- Input-field projection from extracted identification.
- Lazy execution once, local predicates, count metadata, and pagination errors.
- Manager-only slice, union, and `with_instances()` fallback.
- Unsupported historical reads.
- Entire-call fallback for a `GraphQLProperty` or mixed projection.

### GroupBucket

- Grouping keys and aggregated fields.
- Current group ordering after sort and slice.
- Same-object cache reuse and fresh dictionaries.
- Historical compatibility delegated to the basis bucket.

### Invalidation and public API

- Database mutation clears projection entries in an active run.
- Projection entries participate in run-context memory accounting.
- The semantic exceptions are available from documented public imports.
- Method signatures and `values_list()` overloads pass mypy.

## Documentation

Update:

- `docs/api/core.md` for methods, return shapes, errors, backend behavior, and
  the authorization boundary;
- `docs/api/cache.md` for the projection namespace and run-context helpers;
- `docs/concepts/models_entities.md` for backend-neutral projection behavior;
- `docs/concepts/caching.md` for active-run reuse, dependency replay,
  invalidation, and the 10,000-row retention policy;
- `docs/howto/cache_dependent_calculation.md` with `values()` and
  `values_list()` examples and guidance on native versus portable evaluation.

The changelog is generated by the release process and is not edited manually.

## Verification

Run the narrowest affected tests first, then broaden validation:

1. Shared projection and run-context unit tests.
2. Base, database, calculation, request, and group bucket tests.
3. Relevant database/history and request integration tests.
4. `ruff check` and `ruff format --check` for affected files.
5. `mypy` using the repository's configured command.
6. The full pytest suite when focused checks pass.

No performance benchmark is a release gate, but native-path tests must prove
that eligible projections avoid manager construction and do not issue per-row
queries.
