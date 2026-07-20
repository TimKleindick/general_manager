# GraphQL Manager Input Filters

## Problem

GraphQL filter input generation recognizes a manager-typed calculation input as
a relation, but omits it because calculation input metadata does not declare
`relation_kind` or `filter_lookup`. Consequently, a calculation manager such as
`ProjectCommercial` cannot expose a nested GraphQL filter that maps to
`ProjectCommercial.filter(project__id=1)`.

## Public behavior

For a manager with `project = Input(Project)`, the generated GraphQL list query
accepts the existing nested direct-relation syntax:

```graphql
projectCommercialList(filter: {project: {id: 1}}) {
  items { id }
}
```

The resolver normalizes this input to the Python lookup `project__id=1` before
passing it to the manager bucket. Scalar calculation inputs and explicitly
described ORM relations retain their current behavior.

## Design

Add a small GraphQL-only relation metadata resolver. It first honors explicit
`relation_kind` and `filter_lookup` values from `get_attribute_types()`. When
those values are absent, it checks whether the field is present in
`Interface.input_fields` and whether that input's declared type resolves to a
registered `GeneralManager`. Such a field is treated as a direct relation whose
filter lookup defaults to the field name.

Use this same resolver in both schema generation and filter normalization so
the generated nested input and the flattened Python lookup cannot disagree.
Do not alter calculation interface metadata: its documented five-key metadata
shape remains unchanged. Do not add flat GraphQL fields containing Django-style
double-underscore paths.

## Compatibility and errors

Explicit relation metadata remains authoritative, including custom
`filter_lookup` values. Fields that are neither explicitly described relations
nor manager-typed interface inputs remain unaffected. Existing relation-depth
limits apply to inferred relations. Existing GraphQL validation rejects invalid
nested fields before resolver execution, and existing input casting and bucket
errors continue to propagate unchanged.

## Verification

Add an integration regression test defining a calculation manager with a target
date input and a manager-typed project input. The test must first demonstrate
that the current schema rejects the nested project filter, then pass after the
fix and assert that only calculations for the selected project are returned.
Also cover the inference helper narrowly enough to verify that explicit relation
metadata wins over inferred defaults. Run the focused GraphQL tests, followed by
Ruff, mypy, and the broader test suite as warranted by runtime.

Update the computed-data GraphQL documentation with the nested filter example.
