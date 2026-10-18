# Direct schema discovery

Inspection protocol 3 retains exact exposed GraphQL root and output signatures
but advertises only their immediate type references and visible relation managers.
An overview also includes `relation_types`: exact native field type signatures
after the first hop of each declared manager relation path, such as the `items`
field of a collection Page. The first hop is already in `output_fields`. These
signatures come from the same exposure-filtered snapshot and authorize only the
declared path. They do not load whole wrapper definitions or complete a detail
requirement. Related managers remain references requiring their own overview.
No object shape is inferred from returned data.
It avoids the transitive manifest of every nested filter type. A detail selection
returns only the selected exact definitions, plus a manifest of those types and
their immediate references. Repeated detail calls discover the remaining inputs,
enums and object fields using the same manager and snapshot.

Every manifest entry comes from the same captured, exposure-filtered native
contract and is an input, object, enum, scalar, manager reference, or explicitly
unsupported type. Manager references stop expansion and require their target's
own overview and snapshot. Union support is not added. No ORM reflection or
backend-specific discovery is introduced.

Recursive grouped output types and pages introduced by native GraphQL 0.84.0
are currently unsupported by the chat contract. Their actual manager ownership
is bound by the shared GraphQL type factory. Discovery and details omit these
outputs, and structured selections reject them atomically before execution.
This also applies to exposed managers and pages wrapping grouped items: grouped
outputs must receive a modeled manager-owned contract before chat can advertise
them. Ordinary entity pages and existing `group_by` queries retain their declared
contracts. Native GraphQL grouping remains available through native queries.

| Entry point | Omitted view | Explicit detail |
| --- | --- | --- |
| Public `get_manager_schema` tool | Protocol 3 overview | Exact observed overview/detail names and snapshot |
| `execute_chat_tool` Python dispatcher | Protocol 3 overview | Same strict public selector rules |
| `inspect_manager_schema` Python API | Protocol 3 overview | Exact visible names and snapshot |
| `tools.get_manager_schema` Python helper | Legacy complete contract | Selective view only with explicit `view` |
| `graphql_contract.manager_schema` Python helper | Complete supported native contract | No selective selector |
| `schema_index.get_manager_schema_summary` Python helper | Complete supported native contract | No selective selector |

Tagged overview/detail/full responses carry `inspection_version=3`. Snapshot
preimages migrate from projection version 2 to 3, so persisted old detail tokens
cannot authorize this new discovery protocol. The GraphQL read contract remains
version 2; supported native definitions and untagged full Python defaults retain
their existing meaning. Unsupported recursive grouped outputs are omitted from
both public discovery and complete Python contracts.
Selectors fail atomically. The read contract is compiled once after GraphQL
schema construction and unit metadata attachment, before the executable schema
is published. Compilation captures all exposure bindings and validates the shared
public type graph through the existing GraphQL contract encoders. Tool lookups
export detached manager projections from this immutable generation; mutating an
export cannot change another observation. Related managers remain bounded
references even though their permitted definitions share the compiled graph.

Ordinary scalar predicates do not invoke the ID-converter selector; identifier
predicates retain the declared input and native-type checks. All getter fields
for one owner use the source signature captured by that schema generation.

Production schema definitions, exposure flags, enums, defaults and executable
bindings are immutable for the process lifetime. A deployment changing those
definitions must rebuild the executable schema. In tests, `GraphQL.reset_registry()`
clears the compiled contracts before fresh manager registration and schema
construction. A test deliberately changing an already installed native schema
must call `schema_index.clear_schema_index_cache()` at that explicit rebuild
boundary before requesting new observations. Tool calls never reset the cache.
There is no partial compilation success or full-schema fallback.

Each successful compilation adds its generation to the snapshot preimage. An
explicit rebuild invalidates previous detail tokens and schema evidence even if
the public definitions are equal. Previous snapshot tokens therefore require a
new overview and matching detail observations. Workers exchanging tokens must
construct the same schemas in the same order; ordinary production workers each
construct one schema at startup. In-place definition mutation without rebuilding
is unsupported and does not publish new metadata. Request data and authorization
decisions are never stored in the compiled contract.

During one compilation, manager projections reuse the field selection of each
named type from the private captured GraphQL graph. Each manager still applies
its own relation-reference boundary and builds detached public values. The
field-selection map belongs only to that compilation; a rebuild checks the new
captured definitions again. Defaults, enums, exposure and snapshot values retain
their existing semantics. This reduces repeated definition work without caching
request data or authorization decisions. Timing improvements require separate
unprofiled measurements under the original limits.

Internal relation-path checks read the immutable compiled index directly and use
one generation for both endpoint membership and the path walk. This avoids
copying the entire public index for each candidate's anchor-distance check.
Public `compact_index()` and `build_schema_index()` results remain fresh mutable
dictionaries and lists. Returned paths are also detached lists. Exposure rules,
schema snapshots and per-request GraphQL authorization are unchanged.

Structured schema requirements retain their manager, view, selected definition
set and snapshot bindings. Unspecified legacy requirements still require full
inspection. A detail manifest advertises loadability; it does not mean an
unselected type was returned or that a requirement is complete.

New planned selective work may explicitly declare `view=detail`, `types=[]`,
`snapshot=current` for a named manager. This is pending work, not an empty
selection that any overview or full observation can satisfy. The executor first
discovers the required output, filter and next-hop enum/input type names through
that manager's current overview and targeted detail observations. Discovery may
remain unlinked and grants no completion or calculation authority. Once the
needed type set is known, `bind_schema` assigns its exact observed names and
concrete snapshot once. The executor then reads or repeats the selected details
with the matching `requirement_id`; every bound type must actually be covered.
The binding action performs no tool call and supplies no evidence itself.

Only current observations owned by the same task and manager authorize this
binding. A relation's manager reference does not authorize the target manager's
details. Hidden or unknown names, stale snapshots, changes to an assigned scope,
and rebinding legacy full or fixed requirements are rejected. Subsequent schema
changes still invalidate evidence; binding never bypasses freshness. The public
inspection tool continues to reject empty detail selectors. This pending form
belongs only to the planned requirement lifecycle, shared by Python, HTTP and
WebSocket execution. Once required query and calculation witnesses are complete,
perform the declared calculations and complete instead of loading a full schema
because a selective response has `schema_complete=false`.

This is a selective projection, not a full-schema roundtrip. Validation compares
selected definitions exactly and measures complete discovery → details → actual
query sequences, including all serialized request sizes. Lossless transport
codec gm.schema-data/3 remains separate and preserves complete eligible inputs.

Chat queries and confirmed mutations use the configured GraphQL middleware
builder and class-instantiation rules. HTTP execution receives the actual request
object, bound to the same user as the chat scope. WebSocket execution receives the
current user and ASGI scope attributes through `ScopeChatContext`. GraphQL retains
instance and field authorization on every execution. Schema reuse does not cache
permission decisions or replace chat's input, evidence or write-confirmation checks.

An omitted Python query context uses a writable `ScopeChatContext(user=None)`;
it remains anonymous and runs the same configured middleware. Mutation callers
still need an authenticated context before confirmation or execution. When Django
debug middleware is enabled, a chat execution removes only SQL instrumentation it
created, including on GraphQL errors. Existing instrumentation owned by an outer
execution and the actual HTTP request identity are preserved.
