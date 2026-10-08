# Lossless schema and discovery transport

`gm.schema-data/3` transports explicitly selected structured values. The complete
logical request, role/history positions, defaults, exact text formats, occurrence
hashes and independent source bindings remain reconstructible. It does not replace
selective loading or claim that overview/detail responses roundtrip a full schema.

Version 2 adds ordered dictionary key shapes. `$gm_shape: [n, values]` uses the
unique string keys in earlier `objects[n]` with exactly one value per key. Sixteen
bounded passes at most select profitable key shapes using the entire escaped
transport size. Values, key order and source bindings remain exact. Shape marker
keys in original data are escaped as literals. Version 1 remains decodable with
its original instruction and marker semantics; it treats `$gm_shape` as literal
data. Receipts bind the actual version and exact logical/transport message hashes.

Python `project_reference` without a reference binding and schema-only
`compact_messages` emit version 3 when projection reduces the serialized message. Evaluation
profiles default to version 3 and reject conflicting explicit version labels before
dispatch. Historical version-1 evaluations keep their frozen sources and profiles;
using new code requires a new source/control binding and independent review. The
provider's final 200000-character guard and all codec bounds stay unchanged.

Explicit executor/Judge reference bindings additionally permit the separate
`gm.reference-data/1` format. Exact unlinked current schema copies can use
`gm.schema-observation/1` without gaining requirement or completion authority.
Their defaults, scope checks, complete-text reconstruction and per-message
version receipts are documented in [Reference transport](reference-transport.md).

One additional bounded transform can share long key strings across different
wide dictionaries. Earlier scalar string entries supply keys inside ordered key
lists using the existing backward references. Each map keeps every member, its
original order and its own values. Same-name definitions, defaults, enums and
manager references remain distinct. The complete joint table, shifted references
and metadata must decrease escaped transport size and fit all existing bounds.
This uses version 2's existing grammar; it does not change inspection selectors,
snapshot/exposure rules or evidence eligibility.

Known persisted `get_manager_schema` and `search_managers` results can carry a
historical annotation. The result is supplied separately from its text; the known
persistence serializer must match the entire text exactly. Discovery selection
accepts public contract-version-2 manager-summary lists. Arbitrary JSON strings,
user messages, errors and mismatching text stay literal. Existing API names with
`schema` cover both data types; direct discovery uses `judge_discovery` explicitly.

Judge history selection requires a unique paired original call and separate
verified durable role, tool name, arguments and result. Audit proofs bind original
call IDs, source turns, durable/history positions and payload/argument hashes.
Discovery keeps its own tool/payload binding; no snapshot, schema view or full-schema
authority is invented. Equal values across exposures/snapshots can share transport
containers while every occurrence retains its own binding.

At most sixteen passes add small repeated marker-free containers to the existing
large-container pool. A pass is accepted only if the complete escaped transport,
including the table, shifted references and metadata, becomes smaller. No default,
enum, manager or eval case is hardcoded. Literal-marker scaffolding is never pooled.
All small containers can be candidates, including tiny manifest definitions; a raw
character-length heuristic cannot establish their escaped transport cost. The
same full escaped-size profit check and sixteen-pass bound decide acceptance.
References point backward; object/node/depth/character limits and independent
full-original decoding remain enforced. Final message projection preserves the
original representation whenever compression would grow it; this does not bypass
schema selectors, snapshot checks or exposure.

Offline saved Sol requests, rebuilt with the real adjudication builder and Responses
serializer: E042 turn 2 goes from 221959 historical projected characters to 192892,
versus 309483 without projection. All real metadata counts against the unchanged
200000 guard. Seven saved requests across E019/E041/E042/E043 reconstruct exactly.
This proves transport fidelity, not a new live candidate/Judge score or scale result.

Provenance equality uses canonical JSON rather than Python container equality.
Boolean, integer, float, string, null, nested arrays and objects retain their
original distinctions (`false` differs from `0`; `2` differs from `2.0`). Object
key order does not change a JSON value. This applies to paired durable arguments
and results, parsed historical outputs, sender proofs and packet/observation
bindings. Discovery contract versions require an actual integer; persisted
question metadata also compares JSON-exactly. These checks concern source
identity; the lossless codec and semantic scoring rules are unchanged.

Version 3 adds a copied-list patch: `$gm_patch: [n, [[index, value], ...]]`. Its
base is an earlier list object, indices are distinct integers in range, and at
most sixteen explicit changes preserve the length and order. Shape value lists
can use a reference or patch. Eight profitable transforms and at most 64 candidate
pair comparisons per pass bound the work. Every remaining value is copied exactly
from the base and still checked against the full caller-held original. Original
patch marker keys are escaped. Version-1 and version-2 decoding keeps each exact
old instruction and marker semantics; old version-2 patch-looking data stays
literal. New evaluation/profile defaults bind version 3 explicitly, so historical
version-2 freezes require their original source/control bindings.
