# GraphQL ID Filter Consistency

## Problem

GeneralManager exposes root object lookup arguments named `id` as GraphQL
`ID`, but generated filter inputs derive the same field from its Python type.
For ORM-backed managers, that maps `id` to GraphQL `Int`.

This prevents a client from reusing one `$id: ID!` variable for both a detail
lookup and an equality filter, including equality filters nested below a
relation.

## Scope

The change applies to generated GraphQL filter input fields named `id`:

- `id` uses GraphQL `ID`.
- `id_Exact` uses GraphQL `ID`.
- `id_In` uses a list of GraphQL `ID` values.
- Ordered comparisons such as `id_Gt`, `id_Gte`, `id_Lt`, and `id_Lte`
  continue to use the scalar derived from the manager attribute type.

The behavior applies wherever a manager filter type is reused, including
top-level list filters and nested direct or collection relation filters.
Other integer fields retain their current GraphQL types.

## Design

Filter generation will identify the base field name `id` before selecting the
Graphene type for equality-style filter variants. It will use `graphene.ID`
for the base, exact, and membership variants without changing the manager's
runtime identifier type.

Filter normalization will cast values supplied for `id` and `id__exact`
through the manager interface's declared `id` input field. It will cast each
member of `id__in` the same way. This mirrors root lookup construction, where
GraphQL `ID` input is normalized to the manager's declared runtime identifier
type before use.

Ordered comparison variants will continue through the existing field mapping.
This preserves numeric filtering for integer-backed identifiers while keeping
GraphQL `ID` semantics for operations that treat an identifier as an opaque
key.

No new configuration or dependency is introduced.

## Compatibility

Root detail lookup arguments remain GraphQL `ID`, so existing detail-query
clients are unaffected. The generated schema changes `id`, `id_Exact`, and
`id_In` filter inputs from `Int` to `ID`, which resolves the inconsistency
reported in issue #247.

GraphQL accepts integer JSON values for `ID` variables and provides ID input
values to resolvers as strings. Explicit normalization preserves the existing
backend-facing value type for integer-backed and other custom identifiers.

Ordered ID comparisons remain numeric and therefore continue accepting `Int`
variables.

## Testing

Development follows a red-green-refactor cycle:

1. Add an integration regression test that reuses one `$id: ID!` variable in a
   root detail lookup and a nested relation equality filter.
2. Run that test and confirm it fails with the current `ID!` versus `Int`
   validation error.
3. Add focused schema assertions for `id`, `id_Exact`, `id_In`, and an ordered
   comparison such as `id_Gt`.
4. Add focused normalization tests showing scalar and list ID values are cast
   through the manager's declared input field.
5. Implement the smallest filter-generation and normalization changes that
   make the tests pass.
6. Run the relation-filter tests, relevant GraphQL unit tests, formatting,
   linting, type checking, and the full test suite.

## Non-Goals

- Changing root lookup arguments from `ID` to `Int`.
- Removing ordered comparisons for integer-backed identifiers.
- Changing non-ID integer fields.
- Generalizing identifier metadata beyond the existing field name convention.
