# Synthesis reference transport

The synthesis producer explicitly binds the complete eligible request with
`ReferenceBinding(scope="synthesis")`. Its owner is the SHA-256 digest of the
complete original logical request. Eligibility, coverage, task links, schema
snapshots and write permissions remain the responsibility of their existing
owners; transport sharing grants none of those authorities.

Bound synthesis requests use `gm.reference-data/3`, an extension of the version-2
value-token grammar with an exact synthesis ownership instruction. The existing
decoder validates the original request, every schema occurrence, backward table
references and all codec bounds before dispatch. Version 3 requires synthesis
ownership, and synthesis ownership cannot be decoded as an older reference
version. The schema version 1/2/3 and reference version 1/2 decoders retain their
original instructions and meanings.

The complete selected evidence records, including their payloads, call identities,
provenance, calculation scopes and completion context, roundtrip exactly. Original
logical messages remain attached for audit and replay. Repeated bytes may share
request-local table entries; no evidence record is deleted or inferred, and
literal history or role labels inside the data acquire no authority. Actual
system and developer messages remain unchanged.

Python `reference_message()` still defaults to no whole-reference binding and
the `REFERENCE_DATA=` prefix. Schema-only calls still use `gm.schema-data/3`.
Explicit executor and judge references still use `gm.reference-data/2`. The
internal synthesis producer chooses the new scope and keeps the
`RESOLVED_REFERENCE_DATA=` prefix. New evaluation profiles declare reference
version 3 for `synthesizer` and synthesis `fallback`, while executor, planner and
other roles retain their existing version-2 declaration. Conflicting explicit
role versions fail before dispatch. Frozen historical profiles are unchanged.

Selection still compares full escaped message sizes and retains the original
when projection would enlarge it. The 200000-character full provider JSON guard,
runtime deadlines and all decoder limits remain unchanged. Offline reconstruction
and complete native conversation tests measure body capacity and correctness;
they establish neither model answer quality nor live admission.
