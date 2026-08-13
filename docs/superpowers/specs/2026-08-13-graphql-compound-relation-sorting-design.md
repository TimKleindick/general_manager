# GraphQL Compound and Manager-Relation Sorting Design

## Context

Generated GraphQL list fields currently expose `sortBy` as one enum value and
`reverse` as one direction flag. Schema generation omits manager-valued fields,
even though buckets already support compound sort keys and the underlying ORM
can order by foreign-key columns and related scalar fields.

This change will let GraphQL clients apply multiple sort keys and sort through a
direct manager relation. It applies to generated top-level list fields and
generated relation-list fields. Global search sorting is a separate API and is
out of scope.

## Public API

Generated list fields will change `sortBy` from a nullable enum to a nullable
list of that enum. The existing `reverse` argument remains a single flag and
applies to every supplied key.

```graphql
query {
  taxCalculationList(
    sortBy: [calculated_tax, employee__name]
    reverse: false
  ) {
    items {
      calculatedTax
      employee {
        name
      }
    }
  }
}
```

GraphQL's list input coercion continues to accept an inline singleton such as
`sortBy: name`. Typed variables must migrate from the enum type to a list of the
enum type; changing the argument type cannot preserve validation of variables
declared with the old scalar enum type.

Sort keys are applied in list order. An omitted or null `sortBy`, and an empty
list, leave the bucket's order unchanged. `reverse: true` with no sort keys has
no effect.

## Manager-relation options

For each direct, manager-valued field, schema generation will add:

- the relation field itself, such as `employee`, which sorts by the related
  manager's identifier; and
- one flattened option for each scalar interface field directly exposed by the
  related manager, such as `employee__name` and `employee__id`.

The double underscore is both valid in a GraphQL enum name and consistent with
GeneralManager's existing lookup-path convention. The relation option
`employee` is normalized to the path `employee__id`, making its identifier
semantics explicit and consistent for ORM-backed and calculated managers.

Only one direct relation hop is expanded. Nested manager relations and
collection relations are excluded so schema generation remains bounded and
does not assign ambiguous ordering semantics to a multi-valued relation.
Computed `@graph_ql_property` values on the related manager are also excluded
from flattened options in this change because an ORM parent query cannot order
by an arbitrary Python property on its related object. Root-manager properties
retain their current `sortable=True` behavior.

## Schema generation

`GraphQL._sort_by_options()` will produce enum member names separately from
their normalized bucket keys:

- root scalar field `name` maps to `name`;
- direct manager field `employee` maps to `employee__id`;
- related scalar field `employee__name` maps to `employee__name`.

Both top-level and generated relation-list fields will wrap the returned enum in
`graphene.List`. The same enum-building path will be used in both locations so
their available options cannot drift.

Existing rules remain intact: root interface scalar fields are sortable, and a
root GraphQL property is included only when it declares `sortable=True`.

## Resolver and bucket flow

The resolver sorting helper will normalize a single coerced enum or a list of
coerced enums into an ordered tuple of string values. It will pass that tuple to
`Bucket.sort()` once, preserving compound-key precedence. It must not chain
separate `sort()` calls because later sorts could replace database ordering or
invert precedence.

Database buckets already pass tuple keys to Django's `order_by`. The relation
paths use Django's `__` syntax directly. Their existing property validation and
Python-property fallback remain unchanged for root properties.

Calculation buckets will accept `__` paths in addition to their existing dotted
paths by translating `__` to `.` when constructing `operator.attrgetter`
instances. Existing programmatic calls that use dotted paths remain valid.

Request-backed buckets will use the same nested attribute-path resolution when
sorting materialized manager objects. Direct manager and related-scalar enum
values are therefore executable across database, calculation, request-backed,
and grouped list results rather than being conditionally omitted by backend.

Grouped results retain the current behavior: sorting runs after grouping and
before pagination. The group bucket will use the same nested attribute-path
resolution as calculation buckets so relation options do not fail merely
because `groupBy` is also present.

## Errors and compatibility

Graphene continues to reject unknown sort enum values before resolver
execution. Bucket comparison errors, invalid ORM ordering errors, and property
evaluation errors continue to propagate through the existing error paths.

The public behavior change is limited to the `sortBy` argument type and the new
enum members. Inline single-key queries remain valid through GraphQL list
coercion. Clients using typed `sortBy` variables must change the variable type
and value to a list. No new dependency or setting is required.

## Tests

Tests will cover:

1. Schema introspection exposes `sortBy` as a list and includes manager,
   related-scalar, and existing root options.
2. An existing inline singleton `sortBy: name` query still works.
3. Multiple root keys produce stable primary and tie-break ordering.
4. A manager option orders by the related identifier.
5. A flattened related scalar option orders ORM-backed results.
6. Compound root and related keys work for a calculation-backed list.
7. Compound root and related keys work for a request-backed list.
8. `reverse` applies to all compound keys.
9. Empty, null, and omitted sort lists preserve existing order.
10. Collection relations, nested relation hops, and related computed properties
   are absent from the generated enum.
11. Generated relation-list fields accept the same compound options as
    top-level fields.
12. Grouping and pagination retain compound sort order.

Documentation for generated GraphQL list queries will be updated with the new
list syntax, manager-field semantics, direction behavior, and typed-variable
migration note.
