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
`general_manager.chat.evals.datasets` and are scored on tool sequence, query
results, and answer text.

Run the deterministic tests first:

```bash
PYTHONPATH=src python -m pytest tests/unit/test_chat*.py
```

Then run a live provider pass. For a local Ollama Gemma model:

```bash
PYTHONPATH=src python scripts/run_chat_evals.py --model gemma4:e4b -v
```

For debugging a specific dataset, include the transcript:

```bash
PYTHONPATH=src python scripts/run_chat_evals.py --model gemma4:e4b --dataset basic_queries -v --show-chat
```

Treat failures by category:

- **Tool selection**: adjust the decision process or add a more specific eval.
- **Query correctness**: prefer tool-side normalization for common harmless LLM
  formatting mistakes, and keep prompt wording exact.
- **Answer quality**: strengthen answer rules and examples, but do not relax
  grounding requirements.

The eval runner should mirror production message shape. In particular, after a
tool call it resumes with a neutral assistant marker plus the `tool` result, not
with placeholders such as `[tool:query]`.
