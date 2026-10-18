# Chat reference-data transport

Large conversations repeat schema definitions and evidence. The provider transport
can share identical values without deleting facts or changing the logical input.
This lossless encoding is separate from [selective schema discovery](chat_schema.md):
an overview intentionally omits definitions, while the codec reconstructs every
value in its complete eligible input.

## Scope and authority

Only explicit schema slots or whole-reference bindings opt into projection.
A JSON-looking string, prefix or role label inside data does not opt in. Actual
system/developer messages keep their roles. Repeated values share request-local
containers but every occurrence retains its task/evidence/history source,
manager, view, snapshot and provenance. Sharing equal bytes does not link a
requirement, establish freshness or grant permission.

Known persisted schema/discovery results require the structured original,
its paired tool call and verified durable origin. The known serializer must
reproduce the entire historical text exactly. Arbitrary history, errors,
mismatching data and unsupported origins stay literal. Independent historical
origins retain their ordered coordinates, argument/result hashes and individual
source bindings rather than borrowing authority from the current schema index.

The same-task `gm.schema-observation/1` representation may reference an exact
current unlinked observation. Its requirement list is empty. A later valid tool
selection is still needed to establish a compatible evidence link.

## Current formats

| Format or entry point | Meaning |
| --- | --- |
| `gm.schema-data/3` | Schema-only encoding at explicitly selected locations |
| `gm.reference-data/2` | Explicitly bound executor and judge whole-reference encoding |
| `gm.reference-data/3` | Whole-reference encoding with explicit synthesis ownership |
| `reference_message()` without reference scope | Schema-only behavior, `REFERENCE_DATA=` prefix |
| Internal synthesis producer | Synthesis binding, `RESOLVED_REFERENCE_DATA=` prefix |

The schema codec's versions 1/2 and reference codec's versions 1/2 remain
versioned readers with their original instructions and literal semantics.
Synthesis scope requires reference version 3; conflicting explicitly declared
profile versions fail before dispatch. Archival evaluations retain their original
source, role-version and request bindings.

## Reconstruction and bounded work

The codec uses backward references, ordered dictionary shapes and bounded list
patches. Literal marker-shaped objects and strings are escaped. Lists keep
order, length and duplicates. Scalars retain their types, so `false`, `0` and
`0.0` are distinct. Defaults, enum spellings, evidence payloads, original text
formats and each occurrence's source binding must reconstruct exactly.

Before provider dispatch, the decoder validates against the complete caller-held
original, independent occurrence bindings, hashes and expansion limits. Foreign
bindings, invalid references, unused objects and excessive depth/work/size fail.
The encoder compares the entire escaped message with protocol instructions and
metadata. If projection grows it, it keeps the original representation. It does
not truncate an oversized request or load a fuller schema.

## Measuring the right boundary

Record logical and encoded message hashes separately, with per-message receipts
identifying the actual transport format. Measure the final serialized provider
body, including escaping, tools and codec metadata. Compare complete original
conversations and discovery → details → query exchanges; a small isolated detail
response does not describe cumulative provider input.

Codec roundtrips establish fidelity and capacity. They do not establish model
comprehension, answer accuracy or latency. An experiment's body-character guard
is independently configured: historical 200,000-character controls and a later
explicit 300,000-character diagnostic profile are different bound runs, not a
framework-wide production setting. The normal production stage defaults remain
90 seconds for planning/evidence and 30 for synthesis.

For role configuration see [configuration](../howto/configure_chat.md); for
reproducible comparisons see [evaluation](chat_evaluation.md).
