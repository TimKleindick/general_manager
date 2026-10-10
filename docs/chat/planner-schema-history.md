# Selective planner schema history

The default planning view is gm.planner-context/1. A qualified historical native
get_manager_schema result may become a smaller HISTORICAL_SCHEMA_REFERENCE in
this temporary view. The durable message, structured result and full history
remain untouched. Original user text, choices, identity/query evidence, units,
the entire manager catalog and distinguishing metadata remain complete.

Native origins now retain their original canonical payload JSON separately from
their known text serialization. This matters because sorted display text and the
original structured object can have different byte hashes. Projection verifies
both representations, the native payload hash, exact role/content hash, known
tool, durable conversation/message locator, matching manager/view and precise
detail selector/snapshot. It never grants origin from JSON-looking text. Missing
old payload bytes, locators or arguments retain full visibility. Unsupported
inspection versions, untagged legacy schemas, errors and other tool kinds also
remain complete. Malformed qualified attestations fail before the provider.

Each reference retains manager, view, inspection version, snapshot, original
content/payload hashes, source locator and a real explicit reload action. It has
no current schema, query or completion authority. Details reload the original
exact names and snapshot; a stale token requires a fresh own overview and new
selectors. Full is requested explicitly. Unspecified old requirements retain
their existing full-inspection rule. Manager-reference and exposure boundaries
are enforced by the unchanged fresh native schema tools.

Only double-escaped history-size gains are selected. The context-version marker
and planner guidance have their own fixed costs, measured with whole requests.
Selective references do not claim a lossless original-history roundtrip. Separate
codec gm.schema-data/3 slots remain limited to original, unprojected eligible
schema messages; that codec still roundtrips its complete logical input. Provider
roles, the single executor, write/confirmation workflow, guard and time budgets
are unchanged. The planner API continues to accept complete original messages;
only its temporary provider context has this deliberate new default.
# Evaluation history and transport parity

The evaluation report's `history` remains the complete history rendered by the
production persistence adapter. Planner traces retain the actual selective
context and its original conversation/message IDs. For parity, each reference
must match a reference independently derived from that route's prior durable
native tool row, including its role, arguments, payload/content hashes, snapshot,
view, inspection version, reload action and authority flag. Only then may the
temporary comparison replace database IDs by the corresponding durable row
ordinal. All other fields and message order remain exact. Missing or forged
references fail parity; raw requests and route-local provenance stay in the
report. This changes report reconstruction and parity comparison, not scoring,
Gold, planner semantics, native tool results or persistence.
Duplicate JSON keys are rejected; JSON scalar types remain distinct during
comparison, including Boolean versus number and integer versus float.
