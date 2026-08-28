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

The optional planned strategy is a read-only orchestration layer behind the
normal chat transports. It converts a complex read into a validated graph of
one to six root tasks, resolves each task against the live chat-exposed schema,
collects immutable evidence, and synthesizes an answer only from evidence that
belongs to resolved roots. Mutations stay on the legacy confirmation path. The
[planned-chat rollout guide](../howto/run_chat_evals.md#5-roll-out-planned-chat-safely)
shows how to enable the strategy; the [planned-chat cookbook](../examples/planned_chat_orchestration.md)
contains a copy-ready settings and transport example.

The five configured roles separate responsibilities: `planner` returns a
strict JSON task graph, `simple_executor` and `complex_executor` gather
evidence, `synthesizer` produces the grounded answer, and
`fallback_executor` handles bounded escalation. A provider profile is selected
by the server-side role mapping; clients cannot select a profile or trust
group. All profiles used by one turn must share a trust group so escalation
cannot silently cross a provider boundary.

Roots can depend only on earlier roots, the longest root dependency chain has
one edge, and a root can create at most two non-recursive children. The task
runtime states are `pending`, `running`, `resolved`, `blocked`, and
`budget_exhausted`; only committed compatible evidence resolves a task. Failed
calls, candidate lists, provider prose, and raw plans are diagnostics rather
than facts.

Every provider request consumes a round, including failed, duplicate, cached,
planner, and synthesis requests. A root and its children share 15 rounds. The
whole turn has `min(5 + 13 * root_task_count, 80)` rounds. Local resolver passes
are free but are limited to ten; after two consecutive no-progress passes an
executor must select, escalate within its trust group, or block with
`manager_unresolved`.

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
not resumable and introduces no persistence table or migration. This is
compatible with legacy chat: disabling `planned.enabled` immediately restores
the legacy strategy and its event behavior. The [chat API reference](../api/chat.md#planned-read-orchestration)
lists the stable event, error, settings, and Python helper contracts.
