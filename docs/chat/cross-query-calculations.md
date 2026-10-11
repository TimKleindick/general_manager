# Cross-query calculations

Planned chat preserves the existing same-query population rule unless a
calculation explicitly selects `cross_query`. The first supported composition
is `difference` of exactly two ordered, direct, complete, ungrouped query `sum`
results. The operand IDs and source requirements are fixed at binding admission.

Each source must prove its own complete nonempty root population, unique typed
record IDs, original numeric leaves, and current task links. Both queries must
have exactly one generated entity equality filter and a `gte`/`lt` half-open
date interval. Exposed singular relations must reach the same manager's explicit
constructor identity. Constructor aliases bind through the shared interface's
declared scalar primary-key metadata to the actual public output field. A
business field named `id` cannot replace a differently named primary key;
hidden, ambiguous or unsupported primary keys receive no identity declaration.
The cross-query operation still requires its explicit selected `id` paths and
does not infer alternative paths from these declarations.
Captured filter and value contracts also bind native identity flags and the
common ID conversion helper. Changing that metadata, the helper's executable
code or its ID scalar binding invalidates the captured authority.
Identity attributes with their own constructor input also bind that input's
native type, cast and normalization. Replacing an input, its executable methods
or normalization configuration revokes old predicate and category proofs.
Only standard integer/string inputs without custom normalizers or domains
establish constructor equality; ordinary GraphQL queries retain their normal
casting behavior.
The selected input must exist and match the identity attribute's declared native
integer/string type. A missing input or an integer/string type disagreement
cannot establish equality from matching GraphQL `ID` wire text.
Backend field conversions without a standard declared identity input also
retain ordinary query execution, but cannot supply an equality predicate proof.
An internal `Input` wrapper alone does not replace that manager declaration.
This restriction applies to the actual selected converter, through the common
filter path, and does not infer authority from ORM field names or target types.
For an attested `ID` output, canonical decimal wire strings retain the declared
integer constructor identity without float conversion, including IDs above
2^53. String constructors retain exact ID text. This transport adaptation does
not normalize padded integer strings or ordinary `Int`/`String` fields; original
query evidence stays unchanged.
Field names, descriptions, matching record values and
foreign-key spelling do not prove filter semantics. Generated common-interface
filter contracts provide that authority. Use a nested relation identity filter
when a flat foreign-key filter has no explicit relation binding.
The root list must also use the common `create_root_list_resolver`; a custom
resolver that reuses generated filter inputs does not establish population meaning.

Source and entity managers require exact current schema evidence. Full schemas,
or selected detail views containing the output and filter input definitions,
provide the needed witnesses. An overview alone omits input field definitions.
New planned queries capture public schema digests before and after execution.
An unstable or absent witness is ineligible. Cache reuse retains the original
witness and cannot attest old query data against a new contract.

Numeric fields need an application-declared `FieldUnitContract.quantity` with
its actual sibling unit field, or `FieldUnitContract.fixed_quantity(unit="count",
label="pieces")`. The shared interface validates the declaration against an
existing numeric attribute, and GraphQL publishes its actual executable field.
Unknown units, undeclared strings, factors, mixed units and different scales are
rejected. A fixed declaration resolves the composition's unit without rewriting
the predecessor sum's stored unknown unit.

The resulting scope keeps the two query IDs, canonical calls, qualified leaves,
original scopes, selectors and schema provenance separately. Admission,
calculation, later derived-value reuse and completion reconstruct the proof.
`population_complete` verifies both independent scoped populations. Public
schema snapshots do not establish a transactional business-data snapshot.
Unexpected query payload metadata, including historical data snapshot claims,
is outside this bounded proof and is rejected.

This version does not support empty scalar sums, extra filters or excludes,
group alignment, ratio/multiplication, converted predecessors or chained
cross-query predecessors. Legacy bindings and their serialized mappings retain
their original behavior.
