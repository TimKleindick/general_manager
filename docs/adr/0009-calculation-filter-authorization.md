# ADR 0009: Filter calculation inputs before row authorization

## Status

Accepted

## Context

Generated GraphQL calculation-list resolvers currently obtain the complete base
bucket, apply read authorization, and only then parse, normalize, and apply
GraphQL `filter` and `exclude` arguments. A `CalculationBucket` therefore
enumerates and authorizes its full calculation domain even when an input
predicate would reduce that domain substantially.

The calculation bucket already distinguishes declared inputs from computed
GraphQL properties when materializing combinations. The resolver does not use
that distinction before authorization, which is the remaining cause of issue
#455.

The change builds on PR #456. Its exact authorized-subset reconstruction is a
prerequisite and remains unchanged.

## Decision

### Normalized query plan

`build_query_parameter_plan()` parses the existing inputs and relation-filter
normalization in `graphql_resolvers.py` into the immutable
`QueryParameterPlan`. `apply_query_parameter_plan()` retains the existing
operation order:

1. normalized positive predicates from `filter`;
2. normalized positive predicates from `exclude`, applied as exclusions;
3. relation-derived exclusions accumulated from both inputs.

For calculation lists, plan construction validates every normalized lookup root
against the union of declared inputs and exposed GraphQL properties before any
bucket is authorized. Unknown lookup roots retain the existing
`UnknownInputFieldError` behavior. The existing rejection of a relation `none`
expression inside GraphQL `exclude` also remains unchanged. Graphene and the
existing normalizer continue to perform input-shape, scalar, and relation-ID
normalization before this validation.

`apply_query_parameters()` builds this plan and delegates to
`apply_query_parameter_plan()` so existing direct callers keep their current
behavior. Generated list resolvers construct the plan once, so normalization
occurs only once per resolver invocation.

### Safe predicate partition

Only calculation lists receive reordered predicates. A predicate is a safe
calculation-input constraint when the first segment of its normalized lookup
key names a declared `CalculationInterface.input_fields` entry. This includes
nested lookups for manager-valued inputs such as `subject__id`.

All other valid predicates are deferred. These include filters and exclusions
on computed GraphQL properties. The partition preserves whether each mapping
is a filter or exclusion and preserves the current operation order.

`partition_calculation_query_plan()` performs validation and partitioning. It
is resolver-local and side-effect free: it does not instantiate managers,
enumerate possible values, or access computed properties.

The resolver data flow becomes:

```text
base CalculationBucket
-> parse and normalize the complete GraphQL filter/exclude input
-> apply declared-input filters and exclusions
-> permission prefilters and per-instance authorization
-> apply deferred computed-property filters and exclusions
-> grouping
-> sorting
-> totalCount
-> pagination
```

For database, request, and custom buckets, the resolver continues to authorize
before applying the complete query-parameter plan.

### Security invariants

- Computed GraphQL properties are never evaluated before per-instance
  authorization.
- Permission predicates continue to run against every candidate remaining
  after pure calculation-input constraints.
- No grouping, sorting, counting, or pagination moves before authorization.
- The complete GraphQL input is parsed and normalized before authorization, so
  invalid deferred predicates do not cause partial authorization work followed
  by a late validation error.
- PR #456's authorized identification set remains attached to derived
  `CalculationBucket` instances, preventing post-authorization predicates from
  reintroducing denied calculations.

### Performance invariants

- A selective input filter reduces the number of calculation managers passed
  to `can_read_instance()` from the full domain to the filtered domain.
- Computed-property filters do not reduce authorization work because applying
  them early would violate the security boundary.
- Query input normalization happens once per resolver invocation.
- No elapsed-time threshold is used for regression coverage; tests assert
  deterministic candidate and property-evaluation counts.

## Verification

Add focused resolver and integration coverage on top of PR #456:

- Create multiple subjects and periods, apply an exact subject input filter,
  and assert that `can_read_instance()` sees only combinations for that
  subject.
- Combine an input filter with a computed-property filter whose accessor
  records evaluations. Assert that authorization happens only for the
  input-filtered domain and that the computed property is evaluated only for
  authorized instances.
- Cover calculation input exclusions before authorization.
- Assert that `totalCount` and pagination describe the fully authorized,
  deferred-filtered result.
- Preserve unit coverage for ordinary, non-calculation buckets and direct
  `apply_query_parameters()` callers.

The narrow integration test runs first, followed by GraphQL helper tests, Ruff,
MyPy for the package, and the full test suite when practical.

## Consequences

The optimization is intentionally limited to generated calculation lists. It
does not change PR #456's `Bucket.with_instances()` contract, permission-plan
construction, authorization logging, or any concrete bucket implementation.
Computed-property filtering, grouping, sorting, counting, and pagination remain
after authorization. Other bucket backends retain their existing ordering.
