# GeneralManager structural evaluation development guide

This source-checkout harness exercises the production planned chat scheduler with
synthetic manager landscapes, deterministic fixture checks and separately bound
candidate/judge measurements. It is outside the distributed `src/` package.
For ordinary installed-provider comparisons, use
[the maintained module CLI](../../docs/howto/run_chat_evals.md). For contracts,
read [evaluation concepts](../../docs/concepts/chat_evaluation.md).

## What runs offline

Use Python 3.12+ with the repository's development dependencies and editable
install. The following commands use scripted providers and disposable fixtures;
they do not register/log in to an account or call an external model:

```bash
export PYTHONPATH="$PWD/src:$PWD"
python -m experiments.gm_eval catalog --output /tmp/gm-eval-catalog
python -m experiments.gm_eval fixtures --output /tmp/gm-eval-fixtures
python -m experiments.gm_eval parity --output /tmp/gm-eval-parity
python -m experiments.gm_eval harness --case E101 --profile weak-only --output /tmp/gm-eval-small
python -m experiments.gm_eval offline --output /tmp/gm-eval-offline
```

Choose a fresh output directory for each invocation. `--case` is repeatable;
`--scale` selects 5, 10, 50, 100 or 250 managers. Scripted profiles are
`weak-only`, `weak-fallback` and `strong-only`. `offline` combines selected
catalog/oracle validation, fixture checks and the default transport-parity cases.
Use `python -m experiments.gm_eval --help` to inspect exact options.

Each fixture starts an isolated Django subprocess and temporary SQLite database.
It registers real Database, ReadOnly and Calculation interfaces and verifies the
schema census. Distractors occupy declared manager slots rather than inflating
counts. The fixture's explicit read-only synchronization opt-in belongs to its
disposable seed phases, not a change to application startup defaults.

`report.json` is written before the CLI decides its exit status. A structural
metadata key named `status` is not itself a result. Successful infrastructure
checks exit 0; actual model-task/infrastructure failures or failed parity exit 1.
No scripted success is a live model-quality or production-readiness score.

## Source ownership map

| Responsibility | Owning source |
| --- | --- |
| Command selection and artifacts | `cli.py`, `__main__.py` |
| Production scheduler binding and conversations | `harness.py`, `runtime.py` |
| Provider profiles and terminal failure latch | `profiles.py` |
| Frozen case/reference definitions | `catalog.py`, `catalog*.json`, reference metadata |
| Synthetic data and Django fixture application | `seeds.py`, `fixture_app/` |
| Expected facts and same-metric source declarations | `oracle.py` |
| Blind extraction and deterministic comparison | `adjudication.py`, `scoring.py` |
| Reporting, trace and request-size diagnostics | `reporting.py`, `trace.py`, `efficiency.py` |
| Source/history/validation certificates | `semantic_contracts.py`, `validation_*.py`, `stream_lifecycle.py` |
| Regression tests | `tests/experiments/` and relevant `tests/unit/` modules |
| Results | The explicit external `--output` directory for that run |

Keep machine-bound reference JSON, contract owners and complete source manifests
at the paths recorded by their actual version. Internal helpers may be organized
by implementation responsibility; they are not additional public entry points.
Never rewrite an old manifest to pretend that a new layout produced an old run.

## Reference selection and reproducibility

The current default is **1.17**. The catalog contains 121 original cases and
149 conversation turns across 5/10/50/100/250-manager fixtures; a selected campaign
or named historical denominator is a separate cohort. Do not confuse catalog
size, turns, a campaign selection and scored outcomes.

Explicit 1.10–1.16 selectors retain their archived expectations/extraction
shapes. Reproducing an old run requires its exact reference, source and request
contract. A new default does not rescore a saved answer. Reference corrections
are recorded in versioned oracle/catalog metadata; old source fixtures, requests,
responses and raw reports remain unchanged.

The reference's business clock, calendars, quantity/revenue definitions, status
meaning, missing-value treatment and source constraints must be visible in the
candidate's supplied fixture definitions where applicable. Gold values and
expected input rows remain evaluator-only. Do not repair claims using expected
facts or add a case-ID/manager-name exception to obtain a pass.

Current semantic contracts distinguish result membership from asserted ranking,
selected winners from explanatory comparisons, unknown values from zero, and
physical provenance from generic citations. Every volunteered factual claim
still needs support. Reference 1.17 permits reviewed complete same-metric input
routes only with actual eligible answer usage and strict source restrictions;
matching final numbers alone are insufficient. It also accepts the closed
low/high scenario object only in the forecast scenario year slot. See
[evaluation concepts](../../docs/concepts/chat_evaluation.md) for these boundaries.

## Running and judging a candidate

Use the same `run` command for any case selection. Repeat `--case`; the runner
retains catalog order and executes every original turn of each selected case.
Unknown IDs and duplicate IDs are errors before setup or authentication. Without
`--case`, `run` selects **all 121 cases and 149 turns**. The separate offline
`parity` command retains its small default infrastructure sample.

```bash
# Prepare intent locally; this does not authenticate or call a model.
python -m experiments.gm_eval run --prepare-only --case E103 --case E107 \
  --output /tmp/gm-eval-selection

# Start the same selection with its reviewed infrastructure/start record.
python -m experiments.gm_eval run --case E103 --case E107 \
  --start-record "$GM_EVAL_START_RECORD" \
  --start-record-sha256 "$GM_EVAL_START_SHA256"

# No case arguments means the entire catalog, with a matching full-run record.
python -m experiments.gm_eval run \
  --start-record "$GM_EVAL_START_RECORD" \
  --start-record-sha256 "$GM_EVAL_START_SHA256"
```

The reviewed infrastructure supplies `R26_EXPECTED_CONTROL_MANIFEST_SHA256` and
the matching fresh start record. The command delegates to the existing serial
supervisor; it never creates its own authentication, retry or admission path.
Selection, models, budgets, source, output location and review receipts remain
bound before execution. A record for a subset cannot authorize an invocation
without `--case`. A saved request is intent, not permission to start.
`--seed`, `--profile` and `--scale` belong to offline commands and are rejected
for `run`; live settings come from the reviewed record.

Optionally add `--first-message "Your explicit question"` to preparation and
execution. It replaces only the first user message of **each** selected case;
later messages are retained verbatim. An empty or whitespace-only override is
rejected. Without the option, original messages remain unchanged. Preflight and
execution use the same effective text. Reports retain original and effective
messages and hashes, including partial interrupted conversations.

An override changes the context of the whole conversation. All its turns are
therefore **UNSCORED against the original Gold**, even if a later turn is
unchanged or the supplied first text happens to equal an original. The original
reference is not edited and no original-benchmark judge request is issued for
that conversation. Such runs can be inspected as custom scenarios; they do not
improve the benchmark score. Infrastructure failures and STOPs still apply.

Local safety/transport qualification uses its own fixed test conversation and
does not require a two-turn case in the requested live selection. Its separate
offline role does not add a live case, alter the denominator or grant live
authority. The same case ID may occur in both roles; qualification still denies
network access and never reuses a real inference response.

A candidate uses production role construction, `prepare_planned_turn`,
`iter_planned_read_events`, native tools and durable conversation storage.
The adapter is read-only. Explicit scripted providers test infrastructure;
provider-backed runs need the selected real provider/transport and source-bound
admission. No helper silently injects a missing query, widens schema exposure,
repairs tool arguments or fabricates an answer.

Retain complete original conversations, request/response bodies, call IDs,
source hashes, role/profile settings, fixture selection, terminal provenance,
usage and serialized request sizes. The judge sees answer claims and eligible
independent evidence, without Gold. The scorer checks exact results, arithmetic,
units, populations and declared source routes. PASS, FAIL and UNSCORED all stay
visible on the full selected denominator; not-executed work remains explicit.

Current reference 1.17 uses adjudication schema 1.6 and the additional-quantities/1
coverage policy. The **same single judge response** must review the complete
current answer for extra quantities, including prose, tables and written-out
amounts. Each extra claim needs a verbatim quote, a verifiability decision and
source references supporting its entity, metric, unit, period/population and
arithmetic. Matching numeric scalars alone do not establish support. This remains
semantic judgment; deterministic quote/hash validation proves binding, not that
the judge found every claim or judged it correctly.

Only an otherwise PASS becomes UNSCORED for unverifiable, missing, malformed or
stale additional coverage. Existing fact/semantic failures remain FAIL. Coverage
is separate from the existing dimension verdicts and never repairs required facts
or relaxes answer_supported. Saved coverage is validated again at scoring, and
all cases stay in the selected denominator. No additional judge call is made.
Archived reference versions retain schema 1.5 metadata; saved requests retain
their original schemas and instructions. New current-reference metadata records
the new judge contract; numeric Gold, conversations and historical reports are
unchanged. Old live results do not measure this new gate.

A separately reviewed source correction requires a new candidate/control binding.
An offline reassessment of a saved answer has its own old-report hash, selected
new reference and factual review; it is not a new candidate or judge invocation.

## Live transport boundary

The GM/SIWC integration accepts an explicitly supplied supported transport.
Importing this package and running the offline commands do not read personal
credentials or log in. Transport/session lifecycle belongs to
[the SIWC helpers](../siwc_eval/README.md), not to the oracle or scorer.

Live runs must bind the actual source, provider/judge models, case order,
original turns, request ceilings, body guard, deadlines and independent review.
A script or static PASS is not a start capability. Preserve auth, quota,
transport and integrity STOPs. Do not automatically retry an uncertain inference
POST, replay an interrupted request or relabel unknown remote completion.

Production chat defaults to 90 seconds for planning/evidence and 30 for synthesis.
A separately selected diagnostic profile may use 180/90 and a 300,000-character
full JSON guard. Earlier 200,000-character campaigns retain their original limits;
these are experiment-specific settings, not general exceptions or production
defaults. Report the actual bound settings and keep production compliance
unmeasured unless it was actually measured.

## Stream lifecycle contract

[`stream_lifecycle.py`](stream_lifecycle.py) owns strict response-lifecycle
validation. `metadata_contract=None` retains the legacy accepted language;
`siwc-response-metadata-1` explicitly enables the closed local metadata policy.
Unknown selectors reject. This policy describes observed synthetic captures,
not a public provider protocol or the semantics of its opaque moderation flags.

The optional `response.metadata` event is accepted once, immediately before
text-done after consecutive text deltas, or immediately before arguments-done
after argument deltas for the final declared tool. Other positions reject.
Original sequence numbers remain part of the complete `0..N-1` sequence; no
event is removed or renumbered. The closed envelope contains only `type`,
`response_id`, `sequence_number` and `metadata`, with the bound response identity.
Every nested object is closed: the sole metadata key is
`openai_chatgpt_moderation_metadata`; its keys are `prompt`, `generation`,
`tool_call` and `tool_response`, with the latter three empty. `prompt.omnimod`
contains only `protection_type="omnimod"` and a singleton `outputs` list.
Its object contains only singleton `results` and `is_blocked`; the result contains
only `category_flags`, whose exact 42-name vocabulary is `_OMNIMOD_FLAGS` in the
validator. Every flag and `is_blocked` must be literal JSON `false`. Unknown
fields, false-like values, missing flags and unsupported variants reject.

`function_calls` binds all declared calls in order with unique IDs, exact names
and arguments, complete item closure and no missing or extra call. The singleton
`function_call` interface remains available; supplying both selectors rejects.
[`validation_history.py`](validation_history.py) reconstructs already bound
sources through the runtime's `remember_tool_result` and `group_tool_results`.
It preserves provider ordinals, declaration/result order and independent
history/source checks; this helper grants no additional execution authority.

Metadata neither replaces text/response completion nor certifies safety, task
success or retry permission. Metadata absence supplies no moderation proof.
All identity, text/item, error/incomplete-terminal and lifecycle checks remain;
callers independently enforce HTTP/auth/quota/transport, request/raw/boundary,
history/source, parser/action/feedback, audit, fixture and cleanup gates. Validation
of parsed objects does not prove wire-level duplicate-key absence. Any later
integrity rejection still stops the run; it does not authorize replay or retries.

The [lifecycle regressions](../../tests/experiments/test_gm_eval_stream_lifecycle_r58.py)
retain the original synthetic streams as required test fixtures, with source-hash
and negative controls. Historical result reports and development notes belong
outside the current tree; earlier versions remain available in Git history.
