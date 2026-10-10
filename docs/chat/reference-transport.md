# Lossless reference transport

The schema codec `gm.schema-data/3` and its version 1/2 decoders remain available.
They encode only explicit `SchemaSlot` locations and preserve complete originals.
Public GraphQL tools, schema views, selectors, snapshots, exposure, defaults and
requirement linking retain their existing contracts.

`reference_message(..., reference_scope=None)` retains that schema-only behavior.
The executor explicitly supplies `reference_scope="executor"`; the validated
Judge builder explicitly supplies `reference_scope="judge"` when it has attested
schema locations. Other callers, plain messages and privileged role messages are
not opted in by content, a JSON prefix or a role label inside data.

These two callers may use `gm.reference-data/1`. Its root occurrence binds the
complete reference hash to the executor task ID or validated Judge request hash.
Every existing schema occurrence still binds its original owner, provenance,
manager, view, snapshot and text format. A single backward-reference/ordered-shape/
list-patch table can share equal values across schema and ordinary reference data.
The new format is a lossless encoding of the complete reference, not a selective
GraphQL schema view. Original strings remain opaque data.

Only separately attested historical schema text is materialized as structured
data. When its known JSON serializer sorts keys, equivalent current subtrees can
provide key order for sharing. Decoding renders each historical occurrence with
its original serializer and checks the original text exactly. Current structured
data retains exact key order, scalar types, list order, duplicates, errors and
missing/zero distinctions. Literal marker keys are escaped and decoded once.
Expansion retains the existing work, depth, object and size limits.

The encoder measures the entire escaped message with metadata. It selects the
smaller valid schema-only or whole-reference representation, and uses original
text when neither is profitable. This choice does not change a schema selector,
load a full schema or bypass a cache. Originals and versioned projection receipts
remain attached for traces, replay and adjudication.

An exact, current, unlinked `get_manager_schema` result already present in the
same task's reference can use `gm.schema-observation/1` in its tool message. Its
`schema_observation_ref` binds the task, evidence record, call identity and call
ID, source provenance, manager/view/snapshot and canonical payload hash. Its
requirement IDs are explicitly empty. It grants no completion authority and is
distinct from a linked `evidence_ref`. The runtime still requires a tool call
with the intended requirement ID to link eligible cached evidence. Legacy,
stale, failed, foreign or mismatching results remain complete raw messages.

Both text and structured provider adapters receive the same operational
reference. The original tool text and structured result remain separately
available; decoding requires their exact source in the same caller-held task
reference and their paired tool call. Persisted tool results and history are not
rewritten. Per-message receipts declare the actual transport format; the eval
profile's existing schema codec setting continues to identify the base schema
codec. Full provider-body size, not decoded content size, remains the 200000
character admission criterion. Offline reconstruction does not establish model
quality or authorize an additional live run.

Deterministic offline probes read `logical_messages` after validating the
complete transport and original bindings. SIWC replay tests still send the
actual encoded provider body and check its native calls, results and persistence.
The decoder migration changes no live model messages, scoring or gold data.
