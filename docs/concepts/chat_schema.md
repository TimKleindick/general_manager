# Chat schema discovery and exposure

Chat reads the same executable GraphQL contract used by the application. It
captures only managers admitted by `chat_exposed` and uses the shared interface
abstraction. It does not inspect ORM models to invent an alternative schema.
Exact executable names, nullability, input shapes, enum spellings and public
GraphQL defaults are authoritative.

## Discover, inspect, query

The executor uses four native read tools:

| Tool | Purpose |
| --- | --- |
| `search_managers` | Find exposed manager summaries by names, descriptions, fields and relations |
| `get_manager_schema` | Observe a compact overview, selected definitions, or an explicit full supported contract |
| `find_path` | Find an exposed directed relation path, including actual collection wrapper hops |
| `query` | Compile structured selections and typed arguments into a native GraphQL read |

A catalog may add domain descriptions, aliases, `use_when` and `distinguish_from`
metadata for managers already exposed. It improves discovery ranking and grants
no schema visibility or authorization.

## Overview and targeted details

Inspection protocol 3 starts with an overview. It contains executable root
signatures and defaults, output-field signatures, relations, relation-path type
signatures and a `type_manifest` of immediate references. It does not include the
transitive graph of every nested filter type.

A detail request supplies a nonempty unique list of exact observed type names
and the same manager's overview snapshot. It returns those definitions exactly,
plus a manifest of their immediate references. Further requests can follow
input objects and enums as needed. A detail manifest advertises what can be
loaded; it does not certify that an unselected definition was returned.

A type is an input, object, enum, scalar, manager reference or explicitly
unsupported type. A related-manager reference stops expansion. Inspect that
manager's own overview and snapshot before fetching its definitions. Knowing
one manager's relation does not authorize the target's detail view. Union,
interface and fragment support is not implied.

`view="full"` explicitly requests the supported full contract. Invalid names,
empty detail selectors, hidden definitions and stale snapshots reject atomically;
there is no partial selector success or automatic full fallback. Public enum,
default, exposure or compiled-generation changes invalidate old detail tokens.

## Python entry-point defaults

| Entry point | Omitted view |
| --- | --- |
| Public `get_manager_schema` tool | Overview |
| `execute_chat_tool("get_manager_schema", ...)` | Overview |
| `schema_inspection.inspect_manager_schema(...)` | Overview |
| `tools.get_manager_schema(...)` | Full supported contract; explicit `view` opts into selective inspection |
| `graphql_contract.manager_schema(...)` | Full supported contract |
| `schema_index.get_manager_schema_summary(...)` | Full supported contract |

Specify the view intentionally when integrating a lower-level helper. Full
compatibility helpers do not silently become overview calls. Tagged inspections
carry `inspection_version=3`, `schema_view`, `snapshot` and `schema_complete`.
The executable read contract remains version 2.

## Schema requirements and freshness

A planned schema requirement binds its manager, view, type set and snapshot.
`"current"` selects the latest successful capture in the current turn; a literal
digest still has to be current. Detail fragments can satisfy a requirement only
when every required definition is covered at that snapshot. Full can cover a
narrower requirement; detail cannot stand in for overview. An old requirement
without a structured schema scope retains full-inspection semantics.

A deferred detail requirement may declare `types=[]`, `snapshot="current"`.
This is pending planned work, not a legal empty public tool selector. The
executor discovers names from its own current observations, assigns the exact
scope once with `bind_schema`, and obtains linked observations covering it.
Binding performs no read and creates no evidence. A foreign task, relation
reference, hidden name or stale observation cannot supply that binding.

Freshness is shared across tasks. A newer capture or failed capture invalidates
older proof for that manager without changing its immutable audit records.
Captures serialize the freshness commit before signals/persistence callbacks;
a delayed older observation cannot override a newer one. A successful but
incompatible inspection can remain operational history without linking to a
requirement. Repeating it does not change its scope.

## Compiled schema lifecycle

Contract compilation follows native GraphQL schema construction and unit
metadata attachment. One immutable generation captures exposure and validates
the public type graph; tool calls receive detached projections. Reuse stores no
request data or authorization decisions. Every query still executes middleware
and resolvers in the user's context.

Treat executable definitions, exposure, enum/default representations and their
bindings as immutable for the process lifetime. Deploy a rebuilt schema when
those change. A new compiled generation invalidates old tokens even when public
values are equal. Workers sharing tokens must construct schemas in the same
order; normal workers construct one schema at startup.

Tests can use `GraphQL.reset_registry()` before fresh registration. A test that
deliberately replaces an installed native schema must clear the schema-index
cache at that explicit rebuild boundary. Clearing the cache per tool call or
mutating a live schema in place is not a supported update strategy.

## Native query semantics

Use the advertised root, exact GraphQL field names and typed input objects.
`fields` selects root row fields; the adapter adds root page wrappers. Nested
collections retain their own advertised `items`/`pageInfo` paths. A path is
directed and cannot simply be reversed. See the
[discovery/query cookbook](../examples/planned_chat_orchestration.md).

`filters` contains the root filter object's fields directly. Alternatively use
its actual named argument in `arguments`; do not supply both. Nested selections
can use strings, `{fieldName: [fields]}` or `{field, arguments, fields}`.
`Date` filters need dates; `DateTime` filters need timestamps. A calendar interval
described as UTC does not change a GraphQL scalar's type.

The query response includes `data`, `total_count`, `has_more` and `complete`.
`has_more` refers to the root. `complete` also accounts for selected nested
pages; missing nested totals do not prove coverage. Exhaustive answers need the
entire relevant population. Bounded answers can use a partial page while stating
that scope. Neither a schema snapshot nor `complete=true` proves a transactional
business-data snapshot.

Selective discovery intentionally does not round-trip the full schema. Compare
selected definitions exactly and measure whole discovery → details → query
sequences. The separate [reference-data codec](chat_reference_data.md) is lossless
for the complete inputs it is explicitly given.

## Structured input errors

A relation quantifier `none` is unsupported anywhere inside `exclude`, even if
an exposed input advertises the field. Use it inside `filter` only when that
input advertises it. The known native `UnsupportedExcludeNoneRelationFilterError`
becomes actionable `invalid_graphql_request` feedback; it does not return partial
GraphQL data or create evidence. Mixed or unknown resolver failures retain the
generic error path. Matching an exception's text cannot make it a trusted input
error.
