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

| Entry point | Omitted view | Explicit detail |
| --- | --- | --- |
| Public `get_manager_schema` tool | Protocol 3 overview | Exact observed overview/detail names and snapshot |
| `execute_chat_tool` Python dispatcher | Protocol 3 overview | Same strict public selector rules |
| `inspect_manager_schema` Python API | Protocol 3 overview | Exact visible names and snapshot |
| `tools.get_manager_schema` Python helper | Legacy complete contract | Selective view only with explicit `view` |
| `graphql_contract.manager_schema` Python helper | Complete native contract | No selective selector |
| `schema_index.get_manager_schema_summary` Python helper | Complete native contract | No selective selector |

Tagged overview/detail/full responses carry `inspection_version=3`. Snapshot
preimages migrate from projection version 2 to 3, so persisted old detail tokens
cannot authorize this new discovery protocol. The GraphQL read contract remains
version 2; full native definitions and untagged full Python defaults are unchanged.
Default, enum or exposure changes still invalidate old detail tokens. Selectors
fail atomically, and the inspection path captures fresh state on every call.

Structured schema requirements retain their manager, view, selected definition
set and snapshot bindings. Unspecified legacy requirements still require full
inspection. A detail manifest advertises loadability; it does not mean an
unselected type was returned or that a requirement is complete.

This is a selective projection, not a full-schema roundtrip. Validation compares
selected definitions exactly and measures complete discovery → details → actual
query sequences, including all serialized request sizes. Lossless transport
codec gm.schema-data/3 remains separate and preserves complete eligible inputs.
