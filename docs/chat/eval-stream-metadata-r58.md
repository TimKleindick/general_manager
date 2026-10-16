# Closed transport metadata contract (R58)

The saved synthetic E078 executor captures include a `response.metadata` event
between the final text delta and `response.output_text.done`. The old strict
lifecycle certificate rejects it. R58 makes this specific form explicit through
`experiments.gm_eval.stream_lifecycle.verify_stream_lifecycle`, porting the
existing lifecycle owner and adding an opt-in closed contract. Historical controls,
captures, scores and reports remain unchanged.

The same complete original turn also contains three native tool calls in one
response. The legacy proof passed one expected tool with that entire stream.
The new explicit `function_calls` selector binds every declared call in original
order, with unique IDs, exact names and arguments, complete item closure and no
extra or missing call. `function_call` retains the singleton interface; using
both selectors rejects. The history/source-frame migration must pass the full
declared list and independently bind each selected tool's result and history.
The [official function-calling guide](https://developers.openai.com/api/docs/guides/function-calling)
also describes multiple calls per response and identity-bound results.

`validation_history.history_from_bound_sources` reconstructs already bound
sources using the actual runtime's `remember_tool_result` and
`group_tool_results` methods. Backend/cache identities exclude `requirement_id`;
schema feedback deliberately retains the original selection. Response groups
retain their provider ordinals, declaration order and ordered results. The
existing evidence store, schema observations, projection receipts and provider
encoder then recreate the complete logical and wire history. This helper grants
no source, execution or permission authority on its own.

## Authority and version

`metadata_contract=None` retains the legacy accepted language. The explicit
`siwc-response-metadata-1` selector enables the local policy below; an unknown
selector fails. A new operational namespace must bind the exact repository
source and selector in its source manifest and review. All action, tool,
planner and history callers must delegate to the same owner. A matching state
signature alone remains insufficient.

This is a **project-owned acceptance policy for the observed transport form**,
not a public OpenAI metadata specification or an interpretation of proprietary
moderation categories. The checked [official event union](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_stream_event.py)
does not define `response.metadata`. The official definitions distinguish
[text finalization](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_text_done_event.py)
from [response completion](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_completed_event.py).
Neither event is replaced by metadata. The public
[moderation documentation](https://developers.openai.com/api/docs/guides/moderation)
does not establish semantics for this private metadata envelope. The local
policy conservatively stops on any unrecognized or non-false indication and
never treats accepted metadata as a safety, completion, task-success or retry
certificate. Absence of metadata remains legal and supplies no moderation proof.

## Closed shape and transition

The optional event is consumed exactly once, after one or more consecutive
text deltas and immediately before text-done, or after argument deltas and
immediately before arguments-done for the final declared tool. It is unsupported
in reasoning, earlier tool arguments, item boundaries and terminal positions.
Its original integer sequence participates in the complete `0..N-1` sequence;
events are never removed, renumbered or exempted from raw/boundary bindings.
The exact envelope keys are `type`, `response_id`, `sequence_number`, `metadata`;
the response ID must equal the response bound by the surrounding lifecycle.

Every nested object is closed:

- `metadata` has only `openai_chatgpt_moderation_metadata`.
- That object has exactly `prompt`, `generation`, `tool_call`, `tool_response`.
  The latter three must be literal empty objects.
- `prompt` has only `omnimod`; it has exactly `protection_type="omnimod"` and
  `outputs`, a list of one object with exactly `results` and `is_blocked`.
- `results` has exactly one object with only `category_flags`. Its exact flag
  vocabulary is the 42 opaque names in `_OMNIMOD_FLAGS`, source-bound as part
  of this contract. Count-only matches, missing names and new names reject.
- Every flag and `is_blocked` must be the JSON boolean `false`; numeric zero,
  null, true, additional fields and unknown variants reject.

All streamed text/item checks, function-argument binding, header/terminal
identity, error/incomplete rejection and closed lifecycle requirements remain.
Callers retain independent HTTP/auth/quota/transport, exact request/raw/boundary,
history, source, parser/action/feedback, audit, fixture and cleanup gates. This
validator does not itself establish wire-level duplicate-key absence after an
upstream JSON parser has produced objects; R58 claims no new wire guarantee.

## Regression evidence and limits

The fixture retains three complete original event arrays. Re-encoding in the
original JSON format reproduces source hashes
`1ba1ac5ad6098e7495f39eead5754f0ba215d310560ace7fba2d76793176233e`
and `ddee34e1bf311c859b3436d43ed121b368f133710b4fcff3351e9066c5759d48`.
Original metadata ordinals remain 105 and 107. Both malformed actions remain
invalid JSON. The original three-tool response retains hash
`35c7981753882f258fb42d3162a35e3bc78fa04979487b6cbb5831ed25fb2609`
and metadata ordinal 12. Negative controls cover identities, original sequences,
duplicates/placement, all nested shapes, false-like values, unknown events,
text/part/item conflicts, missing/reordered/extra tools, wrong tool bindings and
failure/incomplete terminals. Independent synthetic
metadata-free text and function-call streams retain their legacy behavior.

The metadata-positive stream tests prove this transition, not a complete E078
operational certificate or new model-quality result. Full-capture replay must
still pass every downstream history and tool-source check. Any later rejection
remains a global STOP; no event-skipping, retry, historical rescoring or timeout
exception follows from this extension.
