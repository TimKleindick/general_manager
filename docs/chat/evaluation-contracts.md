# Evaluation contracts and completion

All chat adapters translate their protocol into the same neutral events. A
successful response has exactly one terminal `DoneEvent` with token usage. EOF
alone is insufficient. Ollama must receive `done: true`; Chat Completions must
receive a successful `finish_reason`; Anthropic must receive `message_stop` and
a successful stop reason; Gemini must receive `STOP`. Truncated, blocked or
unknown terminal reasons fail closed. SDK streams are closed on success and
failure. Custom providers must also emit a terminal `DoneEvent` and end their
iterator; adapters that synthesized success at EOF must migrate.

An Ollama `done: true` without the optional legacy `done_reason` remains a
terminal proof; an explicit reason must be `stop`. Chat Completions accepts
`stop` and modern `tool_calls`; legacy `function_call` is unsupported by this
adapter and fails closed.

`providers._shared.completed_events` owns neutral terminal validation for the
consumer, planned provider rounds, summaries and packaged evaluator including
its final-answer recovery path. It delays delivery of the terminal until the
iterator ends and cleanup succeeds, rejecting late events and exceptions.
Callers retain their existing deadlines, content/action validation, permission
checks, write confirmation, rate limits and STOP behavior. This adds no retries,
authentication changes or live evaluation requests. Reported usage is accounted
when the terminal is observed, even if later cleanup fails; accounting never
authorizes tool continuation or a success verdict. The Responses adapter keeps
its stricter `response.completed` admission boundary.

`closing_async_resource` owns awaited cleanup and preserves a primary failure or
cancellation when cleanup also fails. Forwarding experiment and SIWC recording
wrappers use it to propagate closure to their inner streams before returning.

## Packaged execution contract 3

New fingerprints, JSONL traces and readiness summaries identify
`chat-evaluation/3`. Module CLI, source helper, readiness and SIWC candidate
callers execute the production planner/executor/synthesizer scheduler through a
thin provider binding. Native tools use the actual exposed application schema;
fixed managers and tool results exist only behind explicit deterministic role
pins. The former provider/tool execution loop and its injected repairs are gone.
Ollama and historical dataset, score and report readers remain available.

The adapter is read-only and rejects mutation plans without executing a tool
fallback. The application's mutation permission and confirmation paths remain
unchanged. Default dispatch admission is 10 requests per turn across every role,
including fallback, with no extra final-answer request. Existing explicit SIWC
request ceilings and STOP behavior remain unchanged. A compatibility request-cap
alias does not restore the removed execution strategy. See the
[migration guide](../howto/run_chat_evals.md#7-migrate-older-evaluation-integrations).

## Scoring and historical contract 2

Contract `chat-evaluation/2` introduced the following scoring and completion
rules, retained by execution contract 3. `EvalResult.status` distinguishes `pass`, `fail` and
`unscored`; `passed` remains the boolean gate and is false for either failure
kind. A caught execution error is unscored even when its diagnostic text is
empty. A completed but wrong answer is a quality failure. A completed empty
answer in a strict per-turn contract remains `missing_turn_answer`, a quality
failure. Configured judges remain authoritative; a deterministic planned
scheduler test is not evidence of live model answer accuracy.

The planned evaluator also consumes actual scheduler error terminals. Technical
provider and deadline/budget failures are UNSCORED; a verified model task failure
is FAIL. A model-declared block cannot impersonate a technical failure merely by
using its public code. Negative scheduler regression scenarios can pass pytest
while their recorded evaluation outcome correctly remains FAIL or UNSCORED.

The same category owner checks unresolved reasons on a partial `done` terminal.
A proven technical task failure remains UNSCORED even when independent evidence
supports part of the answer. Model-declared failures retain model provenance and
are FAIL. A scheduler-confirmed cycle of rejected model operations is also a
model failure; clarification alone is not a blanket completeness failure.

Planned stream and timeout exceptions retain observed usage for the existing
accounting owners. A timeout remains a `TimeoutError`; this adds no attempt or
deadline extension. External cancellation propagates unchanged and is not an
evaluation verdict; usage retention across cancellation is not guaranteed by
the planned round exception contract.

`complete_provider_round(..., classify_provider_errors=False)` preserves an
unobserved provider exception and its identity by default. Once a terminal
reports usage, subsequent stream failure carries that observation in
`ProviderStreamError`. Opting into classification also wraps earlier stream
exceptions, with `usage_observed=False`; unknown usage is never recorded as an
observed zero. Planner and synthesis opt in. Only expiration of the round's own
`asyncio.timeout` creates `ProviderRoundTimeoutError`; a transport-raised timeout
remains a provider failure. Existing attempt counts and transport STOP latches
remain authoritative.

`complete_provider_round` and `plan_request` expose an optional `on_usage=None`
observation callback. Planner and synthesis retain each reported attempt before
awaiting EOF or cleanup; returned/exception usage is a fallback for providers
that did not invoke the observer. The planning stage retains those observations
outside its cancellable await and uses its existing quota owner on stage timeout.
The executor also accounts each observation before its own stage cancellation;
the result and exception paths book usage only when no observation was received.
The runner owns each dispatched quota task, shields it from stage cancellation
and awaits it during cleanup after provider work stops. Each observation is
dispatched once; provider deadlines and cancellation remain unchanged.
Synthesis preserves its outer stage deadline and reports expiry after even one
admitted attempt as `deadline_exceeded`. No attempt is duplicated or extended.

Readiness reports count PASS, FAIL and UNSCORED separately on the full selected
case denominator. Either failure kind prevents readiness. A product contract
owns hard tool requirements and strategy deviations; an older diagnostic cannot
promote an explicitly permitted order to a hard regression. Cases without a
product contract retain their legacy tool-sequence gate. Default tier0 selection
uses the runner's supported-dataset selector; explicit unsupported datasets still
fail before provider use.

Historical summaries without a contract marker load as `chat-evaluation/1`.
They remain unchanged and readable. A baseline from a different evaluation
contract is incomparable and cannot authorize `fail_on_regression`. Establish a
new baseline explicitly; do not rewrite or silently reinterpret old reports.
The unknown-manager case permits truthful named denials while checking explicit
false availability assertions. These bounded deterministic text checks remain
separate from SIWC's primary semantic answer Judge.

## GM reference 1.16

Public catalog, oracle, CLI-report and harness/offline entry points now default
to reference `1.16`. Explicit `1.10` through `1.15` selectors retain their exact
business contracts and blind extraction schemas. `fact_schemas()` still defaults
to legacy extraction descriptions; the request builder inherits the independently
bound `recommendation-claims/1` default for eligible recommendation contracts.
The adjudication response schema remains 1.5: reference and extraction policy are
separate selectors, not an incompatible relabeling of old responses.

E074 asks “Welche Projekte verwenden CU-ETP?” and accepts the complete unique
project set in any presentation order. E083 and other explicitly requested
rankings remain ordered. E074 no longer requires an unrequested ranking claim
or a C-dimension rank check. Logical conjunctions, disjunctions, exclusions and
supplier lists inside selection constraints use exact set membership. Missing,
extra and duplicate identities or filters still fail; other filters retain exact
comparison. No expected numeric result or original catalog text changes.

Every replay validates exact inner/outer case and integer turn equality, including
old envelopes. New 1.16 request hashes additionally bind outer coordinates.
Historical requests keep their digest recipe; inconsistent envelopes are
rejected rather than attributed to another case. Native tool messages are valid
conversation context, but only user messages can witness a user choice.

Nested historical IDs use only schema origins already proven by paired calls,
separate durable metadata and currently visible production history. Those same
proofs feed transport and normalization in original call order. Missing, changed,
future, conflicting or invisible schemas never acquire authority from the
current schema index or Gold. The new reference and runtime use the same terminal-category owner: production
deadlines spend the wall-clock budget, while provider failures remain technical
transport failures. Explicit saved per-turn flags stay authoritative. For an
unclassified legacy replay, the old deadline fallback remains versioned as
transport failure. Old reports are not automatically rescored.

GM capture retains the existing runtime audit origins in each turn's trace. New
1.16 requests bind the matching turn, terminal and audit projection in
`candidate_failure_provenance`, outside the blind Judge packet. Replay validates
that origins derive from those matching runtime records; public error wording
or model text cannot establish a technical cause. Explicit saved failure flags
keep priority. Older selectors retain their original unclassified fallback and
request digest; envelopes without the optional new proof are not rewritten.
Unresolved task identities join against the audit owner's existing opaque hash;
missing or invalid identities cannot establish a matching origin.
That helper is shared without importing runtime settings, so offline request
construction and replay validation still need no Django or provider setup.

This change makes no new claim about live answer accuracy or production
performance. Technical timeouts still require their own transport evidence.

## Actual case selection and comparable readiness

`runner.select_eval_cases(dataset_names=None, *, tier=None, tags=None)` freezes
the actual ordered case inputs once. It excludes the deterministic-only dataset
from provider-backed defaults and removes datasets with no matching cases from
the executed dataset list. Its `EvalSelection` stores immutable serialized inputs;
`iter_cases()` returns fresh cases for each candidate. Its detached `manifest()`
contains qualified dataset/case identities and SHA-256 hashes of complete case
definitions, including conversations and expectations.

`run_eval_suite` and `run_eval_suite_sync` retain dataset/filter arguments, or
accept a keyword-only `selection=...`. Do not combine the two forms. An explicit
selection is reused by the installed CLI's model/provider comparison, source
helper, fingerprint and readiness execution. Metadata claiming a different
selection is rejected before provider calls. `build_run_fingerprint` accepts
`selection_manifest=selection.manifest()` and verifies its actual dataset list.

An empty direct suite raises `ValueError`; installed/source CLIs reject it before
provider construction. Readiness writes `run_status=empty`, zero cases and a
nonzero exit without running tests or constructing a provider. Empty reports say
that no evaluation took place; they are not 100-percent passes. Unscored cases
remain in the complete selected denominator.

Readiness summaries carry the selection manifest. Comparison requires the same
actual ordered cases/definitions, fixture identifier, gate and evaluation/scoring
contract. Provider, model, prompt/tool fingerprints and the model-dependent
`run_hash` are allowed to differ. Filter expressions describe selection provenance;
the actual selected cohort determines cohort identity. Fixture identifiers must
refer to the intended same data fixture; this manifest does not snapshot an
arbitrary live project database. Historical JSON still loads without adding
invented cohort proof. Reports without a valid selection manifest are readable
but cannot establish a comparable new baseline; historical files are not changed.

The shared installed-distribution smoke verifies CLI bootstrap with an empty
selection as a negative control: it requires exit 2 and the specific empty-case
error. Both quality and release workflows use that same smoke owner. Its success
proves the expected rejection, not a passing evaluation or a provider call.
