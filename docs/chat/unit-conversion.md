# Explicit dimension contracts and arithmetic

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
or record authority. Synthetic fixture migration explicitly declares existing
Shipment quantity labels, Project factor dimensions and ReadOnly Material density
dimensions through this same mapping. Native Database conversion and ReadOnly
dimension discovery/query are tested separately; the latter does not claim a
complete ReadOnly conversion workflow. Seed values, Gold and historical scores
are unchanged; this is an additive contract migration requiring independent
review. Native capability tests do not measure model quality.
