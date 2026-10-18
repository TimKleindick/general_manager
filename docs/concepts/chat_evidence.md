# Chat evidence and verified calculations

Evidence makes the distinction between discovering a schema, reading data and
answering the user's question explicit. A successful observation becomes an
immutable record with task ownership, call identity and provenance. The store
links only compatible records to declared requirements. Synthesis receives
selected eligible records from resolved work, not every tool message that ever
appeared in a conversation.

## Observation, linking and completion

Schema, path, query and calculation records have different meanings. An unlinked
schema observation can guide discovery without satisfying a schema requirement.
An empty successful query remains a real observation. Query pages and corrected
queries remain immutable even when a requirement already has a result. Final
`complete` explicitly selects evidence; validators recompute scope, freshness,
links and coverage. A guessed evidence name or raw tool success cannot replace
those checks.

Tool history retains ordered paired calls/results and actionable validation
feedback. Scheduler-only arguments such as `requirement_id` and category bindings
remain in that operational history but do not enter backend/cache identities.
`last_tool_round` reports whether evidence or links changed; it grants no new
completion authority and imposes no generic no-progress cutoff.

## Category lookups and business queries

Category feedback distinguishes a partial lookup (`gather_complete_lookup`), a
complete lookup ready for a bound business query (`bind_business_query`), partial
business data (`gather_complete_business_query`) and complete observed business
data (`check_requirements_and_complete`). The executor still selects observed
values, gathers missing output/filter definitions and executes the query.

A later complete business observation supersedes an earlier partial window in
feedback only for the same manager, exposed root, exact field projection,
filters, non-pagination arguments and before/after schema witnesses. Native page
arguments are identified through resolver roles. Different populations keep
their own missing work. A later partial result requires gathering again. Stored
evidence does not change, and all other requirements/calculations remain binding.

## Calculation bindings

`calculate()` is numeric arithmetic. `calculate_evidence()` additionally checks
source-backed requirements, paths, scopes and lineage. A numerical coincidence
cannot establish compatible units or source populations. Supported operations
and complete binding shapes are listed in the [API reference](../api/chat.md#planned-task-and-validation-types).


An explicit calculation requirement with `binding=null` is valid pending work.
It must acquire an exact binding through `bind_calculation` before calculation
or completion. The executor context includes `deferred_calculations` and
`deferred_calculation_context_version=gm.deferred-calculations/1` while such work
exists.

Each entry identifies the declared requirement and operation, the required
binding action, and earlier same-task query/calculation requirements with their
actually linked evidence IDs. These are possible source requirements, not
chosen or automatically compatible sources. `binding_validated=false` gives no
field, population, group, unit or completion authority. Full original evidence
remains in `task_evidence`; the executor must inspect it, gather missing reads,
choose the appropriate binding and pass the unchanged validator.

After binding, the executor still must request the declared calculation and
complete using eligible linked evidence covering every requirement. The runtime
does not auto-bind or calculate, select quantity fields or units, or prevent a
legitimate block. Requirements without an explicit binding obligation retain their compatible
behavior. This default context addition applies to planned Python, HTTP and
WebSocket paths through the shared scheduler and makes no new business claim.



An invalid binding stays unassigned. Feedback can list actual linked query IDs
and observed scalar paths so the executor can correct its query or binding;
collections are not flattened into scalar operands and feedback chooses no
source. Ordinary queried scalars need no aggregate unless a declared requirement
asks for one.

## Complete grouped sums


An executor can calculate every observed group of an already bound direct query
sum requirement without enumerating its operand paths. It must select the
requirement and the exact query evidence explicitly:

```json
{
  "action": "calculate_groups",
  "requests": [
    {"requirement_id": "annual_actuals", "query_evidence_id": "task:query:2"},
    {"requirement_id": "annual_plans", "query_evidence_id": "task:query:3"}
  ]
}
```

Each requirement appears once and retains its immutable binding. Its sole
source must be an earlier query requirement of the same task, and the selected
query must already be linked to that source. The runtime chooses no query,
field, unit or binding. Deferred bindings require an explicit successful
`bind_calculation` first. An ordinary queried scalar only needs an aggregate
when a declared requirement requests one.

The root `data` array must be nonempty, have `complete=true` and `has_more=false`,
and have an integer `total_count` equal to the number of rows. Group keys use
the existing declared scalar fields and explicit UTC-year transformations.
Groups keep first-observed order, and their generated operands keep original
row order. Each row contributes exactly once to its observed group. Typed
group values retain the existing JSON identity rules, including null values.

Generated ordinary `calculate` actions pass the existing calculator and scope
validator. They preserve each group's query identity, numeric leaf paths and
explicit unit. Unknown units remain unknown; incompatible units inside one
group fail. All groups of all requests are staged together. An invalid final
group, a failing later request or an expired deadline commits no calculations.
Distinct declared requirements bound the request count, while complete observed
rows bound the number of groups. This action does not introduce parallel backend
execution; independent tool calls in one provider round retain their supplied
execution order.

Only direct `sum` reductions are supported. Empty queries, unbound or derived
requirements, multiple sources, conversion and cross-query bindings are
rejected. Other reductions remain available through their existing actions.
`calculate_batch` retains its explicit operands and existing atomic behavior.

The executor still selects final evidence through `complete`. Every required
group must be covered, and selected calculations and source scope are
recomputed rather than trusted. Synthesis receives the selected results and
their verified sources. It renders fulfilled components that the user requested,
distinguishes actuals, existing plans and forecasts, and applies relevant supplied
definitions without asking an already resolved method again. Missing values and
unresolved business conventions remain unresolved.



## Declaring units and conversion authority


Applications may declare `Interface.field_unit_contracts` using frozen
`general_manager.interface.unit_contract.FieldUnitContract` values. The default is
an empty mapping on the shared InterfaceBase. This works through the same read
abstraction for Database, ReadOnly and Calculation interfaces; undeclared fields
have no unit or conversion authority. Numeric field names and descriptions do
not declare dimensions.

`FieldUnitContract.quantity(unit_field="unit", units={"widgets": "count"})`
binds a numeric attribute to a real sibling string unit attribute. `widgets` is
an exact observed row label; `count` is the existing canonical discrete-count
dimension. `FieldUnitContract.factor(source_unit="count", target_unit="g")`
declares a numeric factor in grams per count. Invalid/offset units, missing
attributes, nonnumeric values and non-string unit attributes are rejected.
These are application declarations, not values a model or user may supply.

Schema generation binds Python attribute names to actual Graphene field names
and installs only these public declarations on executable GraphQL extensions.
The exposure-filtered chat contract captures them in `unit_contract`; arbitrary
private extensions remain outside inspection. Overview/detail/full retain their
existing defaults and scope. Related-manager details require that manager's own
allowed observation. Changes to applied units or exposure invalidate old
snapshots. A changed interface declaration takes effect when the executable
schema is rebuilt. The v24 schema codec remains a separate lossless roundtrip.

The Python numeric `calculate` API adds binary `multiply` and alternating
quantity/factor `sum_products`, using Decimal coefficients. Numeric multiplication
does not confer unit authority. Source-backed `calculate_evidence` permits
`sum_products` only with an explicit conversion binding. Existing `sum` and all
old `CalculationBinding.as_mapping()` shapes stay unchanged when conversion is
absent. Public planner/executor schemas explicitly expose the new operations and
optional closed `conversion` member. Deferred `binding=null` stays deferred until
real schema/query witnesses are read and one binding is assigned.

Products are formed with enough precision for both input coefficients. The sum
then covers the complete actual product exponent span and possible addition
carries, independent of ambient Decimal precision. A small nonzero residual is
retained when larger positive and negative products cancel, in any row order.

Conversion requires one complete original query population and quantity/unit
fields at its root. The optional ConversionBinding names the observed factor
path, same-object constructor identity, and corresponding task-linked schema requirement
and evidence IDs. Every quantity, factor, unit, group and ID path must have been
selected in the canonical query. All rows of exactly one group are included once
in original order as quantity/factor pairs. Different origins may carry different
observed factors; the same origin may not carry conflicting values. Null/bool/
nonfinite values, absent or duplicate IDs, partial populations, unknown units,
foreign/unlinked schemas, stale snapshots and incompatible declared dimensions
are rejected. Source units must match exactly after canonical declaration;
automatic scale/FX lookup and offset conversion are unsupported.

Identity authority comes from the shared interface's actual single required `id`
constructor input, explicitly captured as `identity_contract`. Its declared input
type must be `int` or `str`; executable `Int`, `String` or `ID` fields must match
that contract. A GraphQL scalar type or a field named `id` alone does not prove
record identity. Composite, optional and unsupported constructor identities do
not acquire conversion authority. Every conversion proof is checked before
numeric arithmetic, including transitive recomputation of stored results.

Each manager along the relation path supplies its own current allowed schema
witness. The exact observed field declarations are checked against a fresh
capture of the executable exposed contract. Raw unit strings alone never prove
dimensions. The target unit comes from the trusted factor declaration, not a
model label. Evidence retains source hashes, snapshots, identities, original
paths and complete populations; derived results are recomputed. Numeric multiply
with unit-bearing predecessor bindings is rejected. Separate scalar-factor
broadcasting is unsupported until a separately verified common-origin contract
exists. Child evidence whose conversion witnesses cannot be safely mapped stays
reference-only under the existing adoption rules.

Open requested units use the existing analytical clarification/choice workflow
before arithmetic. The reply selects conversational scope and supplies no factor
or record authority.

## Bounded cross-query composition


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


## Limits and interpretation

The schema contract proves executable definitions, not that two reads observed
one database transaction. Missing values remain unknown, and conversions require
actual dimension/factor authority. Numeric operations and exhaustive population
checks are separate. A user choice, schema label or model explanation cannot
supply a missing factor, rate, query row or source witness.

Scripted tests can prove these mechanics and reject invalid proofs. They do not
measure whether a live model will choose the right metric, gather the right
population or explain it faithfully. See [evaluation](chat_evaluation.md).
