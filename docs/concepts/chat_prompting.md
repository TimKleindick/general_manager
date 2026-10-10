# Chat prompt and eval iteration

GeneralManager chat uses a fixed set of discovery tools instead of one tool per
manager. This keeps the prompt small enough for projects with hundreds of
managers, but it means prompt changes must be tested against tool-choice and
answer-quality evals before they are treated as reliable.

This page is for prompt and eval contributors. Application developers should
start with [Add LLM chat to a GeneralManager project](../howto/install_llm_chat.md)
and [How LLM chat works](chat.md).

## Prompt contract

The system prompt is built in `general_manager.chat.system_prompt` and is split
into stable sections:

- identity and grounding
- available tool descriptions
- tool decision process
- query construction rules
- answer rules
- mutation safety
- tool examples
- compact schema context
- project-specific developer instructions

The prompt should keep these behaviors stable:

- call `search_managers` when the user does not provide an exact manager name
- call `get_manager_schema` before using uncertain fields, filters, or relations
- call `find_path` for cross-manager questions
- answer data questions only from tool results
- copy returned values exactly and avoid values not present in the tool JSON
- report empty results honestly
- avoid mutation calls unless the user clearly requests a write

## Eval workflow

Add eval cases before changing prompt text. The datasets live in
`general_manager.chat.evals.datasets` and are scored as product contracts first:
hard contract failures affect pass/fail, while strategy deviations explain when
the model skipped a preferred discovery path but still satisfied the product
contract.

## Eval tiers

The chat eval suite is a product behavior contract first and a model benchmark
second.

- Tier 0: toy contract cases that verify the harness, tool loop, prompt basics,
  and safety invariants.
- Tier 1: local demo readiness cases that should pass before showing the
  prototype with a weaker local Ollama model.
- Tier 2: synthetic large-schema cases that stress manager discovery, path
  finding, and no-hallucination behavior.
- Tier 3: production-like cases copied or adapted from real project workflows.

Hard contract failures indicate product behavior that must be fixed. Strategy
deviations indicate that the model skipped a preferred discovery path while
still satisfying the hard contract.

Run the deterministic tests first:

```bash
PYTHONPATH=src python -m pytest tests/unit/test_chat*.py
```

Then run a live provider pass. For a local Ollama Gemma model:

```bash
PYTHONPATH=src python scripts/run_chat_evals.py --tier 0 -v
```

For local demo readiness with a weaker model:

```bash
PYTHONPATH=src python scripts/run_chat_evals.py --model glm-4.7-flash:q4_K_M --dataset demo_readiness --tier 1 -v --trace-jsonl /tmp/chat-demo-eval.jsonl
```

For synthetic large-schema discovery checks:

```bash
PYTHONPATH=src python scripts/run_chat_evals.py --fixture large --dataset large_schema --tier 2 -v
```

For debugging a specific dataset, include the transcript:

```bash
PYTHONPATH=src python scripts/run_chat_evals.py --model gemma4:e4b --dataset basic_queries -v --show-chat --trace-jsonl /tmp/chat-basic-eval.jsonl
```

### Installed module CLI

The built-in YAML eval datasets ship with GeneralManager. The installed module
CLI still needs the consuming Django project's settings for Django and provider
configuration. For `basic_queries`, register its matching built-in toy schema
and data:

```bash
python -m general_manager.chat.evals \
  --settings myproject.settings \
  --dataset basic_queries \
  --fixture toy \
  --provider general_manager.chat.providers.OllamaProvider
```

Omit a built-in fixture only when the selected dataset's managers and
expectations match the configured project's schema and data.

You can set `DJANGO_SETTINGS_MODULE` instead of passing `--settings`. Displaying
the CLI help does not require Django settings:

```bash
python -m general_manager.chat.evals --help
```

Treat failures by category:

- **Product contract**: fix unsafe behavior, wrong data, hallucinated fields, or
  ungrounded answers.
- **Strategy deviation**: improve prompt/tool descriptions when the model skips
  a preferred discovery path but still satisfies the hard contract.
- **Tool selection**: adjust the decision process or add a more specific eval
  when the legacy tool-sequence judge is still active for a case.
- **Query correctness**: prefer tool-side normalization for common harmless LLM
  formatting mistakes, and keep prompt wording exact.
- **Answer quality**: strengthen answer rules and examples, but do not relax
  grounding requirements.

The eval runner should mirror production message shape. In particular, after a
tool call it resumes with a neutral assistant marker plus the `tool` result, not
with placeholders such as `[tool:query]`.

### Evaluate follow-up turns independently

Use `expectations.turns` for multi-turn cases. Supply one nonempty expectation
mapping for each user turn. Each turn must return an answer and pass its own
checks; a correct first answer or query cannot satisfy a later request. The
turn's `answer_contains` entries are all required, overriding the older aggregate
judge's 80% keyword threshold. Keyword checks still do not prove semantic
correctness; exclusions also match terms inside explanatory negations.
The legacy harness carries the complete assistant/tool exchange into subsequent
turns. Top-level expectations remain aggregate checks for backward compatibility.

For data requests, `result_set` checks the last query to the named manager in
that turn. It compares an unordered multiset, so missing rows, extra rows, and
duplicate rows matter. `fields` selects the columns to compare; it defaults to
the keys of the first expected row and is required for an empty expected result.
Nested objects are projected onto their expected keys too, so selecting extra
fields does not fail a correct query. Relation lists are compared without order,
while preserving duplicates.
Missing queries and query errors do not count as empty results. For example:

```yaml
expectations:
  turns:
    - result_set:
        manager: PartManager
        fields: [name]
        rows: [{name: Bolt}, {name: Bearing}, {name: Gear}]
      answer_contains: [Bolt, Bearing, Gear]
    - result_set:
        manager: PartManager
        fields: [name]
        rows: [{name: Bolt}]
      answer_contains: [Bolt]
      answer_excludes: [Bearing, Gear]
```

Keep query and answer checks separate: exact query rows alone cannot establish
the correctness of arbitrary prose. Match page-specific expectations to the
requested page; this judge does not combine multiple query pages. A dataset must
not reject a supported alternate discovery path solely because it differs from
an oracle script. Turn failures and exact-result reasons appear in verbose
reports and traces. Invalid turn contracts are rejected before inference.

The local SIWC comparison sets an explicit limit of 16 model requests per user
turn, including the final answer. Its case ceiling is 16 times the number of
user turns. Exhaustion is reported as a harness budget outcome, separately from
completed answers that fail quality checks. This experiment setting does not
change the runtime chat API or the default installed eval CLI budget.

The opt-in SIWC experiment uses `answer-correctness-v3` for its primary score.
Each answer is assessed against an independently generated fixture reference in
a fresh, tool-free Astra `medium` request. The judge receives the user questions
and current answer, without the candidate model identity, tool trajectory,
previous answers, or legacy scores. Correct selections from larger query results
and equivalent wording can pass; incorrect associations, quantities, omissions,
negations, and stale follow-ups must fail. Every turn must pass independently.

Legacy tool-path, query-set, and keyword scores remain diagnostic in that
experiment. Judge uncertainty or failure is unscored and stops the run, rather
than reducing a model's answer-quality score. Candidate and judge requests are
counted separately. Saved artifacts include actual responses, tool results,
references and verdict explanations, including partial transcripts on transport
failure. The semantic judge remains fallible and is calibrated with positive and
negative examples. These settings do not replace the generic deterministic eval
contracts shown above or establish production readiness.

## Production-readiness loop

Use the readiness loop when changing the chat system prompt, tool metadata, tool
schemas, tool-loop harness, or eval contracts.

```bash
PYTHONPATH=src python scripts/run_chat_readiness_loop.py \
  --model glm-4.7-flash:q4_K_M \
  --gate demo \
  --output-dir /tmp/gm-chat-readiness \
  --baseline-json .chat-readiness/demo-baseline.json \
  --fail-on-regression
```

The loop writes:

- `summary.json`: machine-readable pass rates, selected gate, run hash, and
  diagnostics.
- `report.md`: human-readable report with diagnostics and baseline comparison.
- `trace.jsonl`: per-case conversation, tool calls, tool results, answer text,
  and run fingerprint.

Treat the loop as an iteration driver:

1. Run the loop.
2. Fix the largest hard diagnostic class first.
3. Change one surface per iteration: prompt, tool metadata/schema, harness, or
   dataset.
4. Rerun the same gate and compare to the previous accepted baseline.
5. Commit when deterministic tests pass and the selected gate improves without
   a new hard diagnostic category.

Do not relax product contracts to make a weaker local model pass. A contract
change is valid only when the expected behavior was wrong for production.

## Production hardening gates

Before enabling chat for production traffic:

- Run the deterministic chat suite:
  `PYTHONPATH=src python -m pytest tests/unit/test_chat*.py tests/integration/test_chat*.py -q`
- Run the full project suite:
  `PYTHONPATH=src python -m pytest -q`
- Run the local demo gate for stability:
  `PYTHONPATH=src python scripts/run_chat_readiness_loop.py --gate tier0 --model glm-4.7-flash:q4_K_M --output-dir /tmp/gm-chat-readiness-tier0 --skip-tests`
- Run the large-schema gate:
  `PYTHONPATH=src python scripts/run_chat_readiness_loop.py --gate large --model glm-4.7-flash:q4_K_M --output-dir /tmp/gm-chat-readiness-large --skip-tests`

A gate may pass with generic prompt/tool retries, but it must not pass with forbidden recovery or harness-synthesized answers.

## Planned orchestration bounds and grounding

The Planned strategy is the read orchestration layer behind the
normal chat transports. It converts a complex read into a validated graph of
one to six root tasks, resolves each task against the live chat-exposed schema,
collects immutable evidence, and synthesizes an answer only from evidence that
belongs to resolved roots. Mutations stay on the legacy confirmation path. The
[planned-chat rollout guide](../howto/run_chat_evals.md#5-roll-out-planned-chat-safely)
shows how to configure the strategy; the [planned-chat cookbook](../examples/planned_chat_orchestration.md)
contains a copy-ready settings and transport example.

The three processing stages are `planner`, `executor`, and `synthesizer`.
The planner returns a strict JSON task graph, the executor gathers evidence,
and the synthesizer produces the grounded answer. A fourth configured role,
`fallback`, handles recovery under the existing checks and deadlines. Every
task uses the same executor role regardless of its complexity. A provider
profile is selected by the server-side role mapping; clients cannot select a
profile or trust group. All profiles used by one turn must share a trust group
so recovery cannot silently cross a provider boundary.

Roots can depend only on earlier roots, the longest root dependency chain has
one edge, and a root can create at most two non-recursive children. The task
runtime states are `pending`, `running`, `resolved`, `blocked`, and
`budget_exhausted`; only committed compatible evidence resolves a task. Failed
calls, candidate lists, provider prose, and raw plans are diagnostics rather
than facts.

Every provider request is counted, including failed, duplicate, cached,
planner, and synthesis requests. Normal planned turns have no global,
per-root, or local-pass round limit. Local resolver passes do not incur provider
charges. Successful discovery and schema inspection can continue without adding
declared evidence; they do not trigger a no-progress escalation or stop.

Transport/provider exceptions stop the affected task. Invalid output receives
correction feedback; recurrence of the same validation diagnosis after feedback, or the same
failed tool batch without intervening successful work, stops an error cycle.
Distinct correctable errors may continue within the existing deadline. These
failure checks never turn rejected output into evidence. Planner and synthesis
retain their correction/fallback protocols and trust-group validation.

Lower-level callers can opt into a bounded `RoundBudget`; its historical direct
constructor remains compatible. Normal scheduler factories use
`RoundBudget(..., enforce_limits=False)`, with `None` for unbounded limits and
remaining rounds. Explicit numeric limits still produce `budget_exhausted`.

The 120-second default response budget has a 90-second planning/evidence stage
and a 30-second synthesis stage. Each provider request is capped by its stage's
remaining time. At the evidence deadline, no new round starts, async provider
work is cancelled, and unfinished tasks become `deadline_exceeded`; synthesis
uses only already committed evidence. PostgreSQL queries receive a statement
timeout bounded by the smaller of query timeout and remaining evidence time.
Other database backends have best-effort cancellation: a synchronous query can
finish in its worker thread after the response stops awaiting it.

Derived evidence permits only `count`, `sum`, `average`, `minimum`, `maximum`,
`difference`, `ratio`, and `percentage`. Operands reference resolved query
evidence; arbitrary expressions, imports, callbacks, and code execution are
not allowed. An invalid, missing, incompatible, or nonnumeric operand blocks
that requirement rather than becoming a synthesized fact.

Plans, routes, budgets, candidates, and evidence lineage stay in memory for the
request. A disconnect or process restart ends the turn; planned execution is
not resumable and introduces no persistence table or migration. The deprecated
`planned.enabled=False` switch does not change read dispatch; global chat
exposure remains controlled by `CHAT.enabled`. The [chat API reference](../api/chat.md#planned-read-orchestration)
lists the stable event, error, settings, and Python helper contracts.


### Native read schema

Read-tool contract version 2 derives executable names and types from the active
GraphQL schema. Ask for `get_manager_schema` before constructing uncertain
selections or filters. Use exact names, typed nested input objects and actual
collection wrappers; do not translate a Python property into a guessed field.
Use `{field, arguments, fields}` for arguments on nested selections. A returned
path is executable from its source manager and cannot simply be reversed.
For exhaustive answers retain nested page totals and cover every relevant page;
`has_more` describes only root pagination and `complete` describes the returned
response rather than the task's semantic completion. Full schema definitions
are not embedded in every planner request.

Structured clarification responses contain only `clarification.language` (`de`,
`en`, `fr`) and a nonempty unique `clarification.requirements` list drawn from
`criterion`, `metric`, `horizon`, `population`, `customer_identity`,
`record_selector`, and `unit`. The runtime renders fixed questions; this branch
accepts no free answer text, result claims, entity names, numbers, or evidence IDs.
The existing read plan must still resolve schema/identity evidence. Data answers
continue to require nonempty unique eligible `evidence_ids`; mixed answers and
questions use that grounded branch. The normal fallback, delivery, and persistence
paths apply to both response shapes.


Schema tools default to a compact overview. Use its exact `type_manifest` names
and manager-bound `snapshot` with `view="detail"` to load input, enum or output
definitions as needed. Inspect related manager references with their own
overview. Schema requirements declare structured `schema` scope; unspecified
legacy requirements require explicit `view="full"`. `schema_complete` describes
full-schema inspection, not data or task completeness. See the chat API reference
for the full Python helper defaults and migration contract.

Schema freshness is shared across the current turn: a newer snapshot or failed
contract capture for a manager invalidates that manager's older evidence in every
task. Records remain immutable for audit. Invalid selector shapes do not certify
any definition and do not trigger a full-schema fallback.

Freshness commits synchronously while tool captures are serialized, before any
post-tool signal or persistence callback. Adding or adopting an immutable schema
record never changes current freshness; only a new capture observation can do so.
A delayed older success or failure cannot replace a newer observation.

Schema capture inspects only reachable definitions admitted by the captured
exposure view. Related manager definitions stay bounded references. Public defaults
are frozen through GraphQL's native value-to-AST conversion before final contract
formatting; internal enum values, private defaults and arbitrary application
metadata do not need to be copyable. Snapshot changes follow the observable enum
spellings, default representations and exposure contract.

### Analytical population and calendar scope

For an unqualified ranking whose metric, period and entity are clear, planned
reads use all accessible records of that entity. Explicit current and unchanged
prior user filters remain binding. The default does not resolve an ambiguous
metric, period or entity, and query coverage or planner assumptions do not become
user choices. Named selection targets require eligible identity query evidence,
including when the filtered result is empty; explanatory entities cannot replace
them.

Explicit requested periods and later user corrections take precedence over a
completed-calendar-year default. The current year remains queryable and must not
be silently removed. When the supplied business clock establishes that an
included calendar year is still running, briefly identify it as incomplete.
When comparing completed calendar years, explain the current incomplete year
only if the verified selected period actually excludes it. Derive the note from
the supplied clock and query bounds, preserve unchanged entity filters, and keep
configured fiscal calendars distinct from calendar years.
