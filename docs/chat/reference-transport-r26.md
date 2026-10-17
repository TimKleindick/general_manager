# Reference transport version 2

New explicitly bound executor and Judge whole references use
`gm.reference-data/2`. Schema-only projection still uses `gm.schema-data/3`.
The version 1/2/3 schema decoders and the `gm.reference-data/1` decoder retain
their original instructions and literal semantics.

Inside the version-2 root and object table, the value string `"~n"` is a
backward reference to table entry `n`. Its index is an unsigned ASCII decimal
integer without leading zeros. Literal value strings beginning with `~` gain
one leading `~`; decoding a value beginning with `~~` removes exactly one.
Other strings beginning with `~` are invalid. Dictionary keys and escaped
literal-object pair keys remain opaque. Shape key lists are values and use the
same string escape. Literal marker objects still use `$gm_literal`; unescaped
`$gm_ref` objects are invalid version-2 wire values. Shapes and list patches
retain their existing integer-index grammar, order and bounds.

The rewrite is one bounded pass after existing sharing. The encoder validates
the complete reconstructed original, source bindings, occurrence hashes and
text formats before dispatch. Selection compares complete escaped message sizes,
including version instructions and metadata. It retains the original text when
neither schema-only nor bound whole-reference encoding reduces that size.
The provider's existing full JSON admission guard and all codec limits are
unchanged. Exact logical and transport hashes are different and both are recorded.

Bound version-2 references also share identical value strings. Repeated long
strings use the existing backward object table; a final phase shares other
profitable strings in at most sixteen passes after the structural rewrites.
Profit is measured on the complete escaped transport using its final short
tokens. Dictionary keys and literal-object pair keys remain opaque. The
schema-only encoder retains its existing sharing behavior. This changes only
wire size: every definition, default, snapshot, provenance value, requirement
link and calculation result still reconstructs exactly.

Prospective evaluation profiles separately declare
`schema_transport_version="gm.schema-data/3"` and
`reference_transport_version="gm.reference-data/2"` in every role configuration.
Conflicting explicit versions fail before dispatch. Frozen historical profiles
and source bindings remain historical; new source/profile/body hashes are
required for new-candidate measurements.

Judge history can also carry `gm.history-origins/1` audit proofs. Every matching
paired schema/discovery call must have exactly one independently verified durable
row, and every call and durable coordinate must be distinct. The complete typed
output and argument scope must agree across origins. An omitted schema view may
agree with its actual returned public default; exact original argument hashes
are still retained independently. Any other argument difference, missing durable
source, unverified persistence or ambiguous row assignment keeps the text literal.

A multiple-origin occurrence preserves the complete ordered origin set: source
turn, original and packet call IDs, task ID, durable coordinate, manager/view/
snapshot where present, and exact argument/payload hashes. The set has its own
digest, and the request digest also binds its full audit proofs. Source validation
requires each call/durable pair to reproduce the entire historical text with its
known serializer. Sharing these bytes grants no query, requirement, completion
or schema authority. Old single-origin proofs retain their original shape.

Offline complete public Discovery → Details → Query → Judge measurements use
real native tools, persistence and the complete Responses body serializer.
They establish reconstruction and body capacity, not model quality or live
admission. Selected definitions are compared with their full-schema definitions;
the separate full-schema lossless codec roundtrip is measured independently.
