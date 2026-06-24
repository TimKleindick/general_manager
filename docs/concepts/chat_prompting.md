# Chat prompt and eval iteration

GeneralManager chat uses a fixed set of discovery tools instead of one tool per
manager. This keeps the prompt small enough for projects with hundreds of
managers, but it means prompt changes must be tested against tool-choice and
answer-quality evals before they are treated as reliable.

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
