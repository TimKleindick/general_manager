# Run the installed chat evaluation suite

Use the installed module CLI when you want to evaluate a provider against the
datasets shipped in the GeneralManager wheel. The command runs inside a Django
project, so it must load that project's settings before it imports providers or
registers fixtures.

Configure the provider first by following [Add LLM chat to a GeneralManager
project](install_llm_chat.md). The eval runner uses the same provider adapter and
model configuration as the runtime chat API unless command options override
them.

## 1. Choose the Django settings module

Pass the module explicitly:

```bash
python -m general_manager.chat.evals \
  --settings myproject.settings \
  --help
```

For repeated runs, set the standard Django environment variable instead:

```bash
export DJANGO_SETTINGS_MODULE=myproject.settings
python -m general_manager.chat.evals --help
```

`--help` is the only normal invocation that does not require settings. A run
without `--settings` or a nonempty `DJANGO_SETTINGS_MODULE` exits with status 2
before `django.setup()` runs. GeneralManager 0.62.3 removed the old fallback to
the repository's test settings, so installed commands cannot silently evaluate
against a development-only configuration.

## 2. Run a shipped dataset

The wheel includes `basic_queries`, `demo_readiness`, `edge_cases`,
`follow_ups`, `large_schema`, `multi_hop`, and `planned_orchestration`. The
`basic_queries` dataset can use the built-in toy schema and data:

```bash
python -m general_manager.chat.evals \
  --settings myproject.settings \
  --provider general_manager.chat.providers.OllamaProvider \
  --dataset basic_queries \
  --fixture toy \
  --tier 0 \
  --verbose
```

Omit `--provider` to use the provider configured in
`GENERAL_MANAGER["CHAT"]`. Omit `--dataset` to run every shipped dataset whose
managers and expectations fit the configured project. Use `--fixture large`
with `large_schema`; omit fixtures when the selected dataset is intended to run
against your project's own managers and data.

## 3. Narrow or compare a run

Repeat `--tag` to require several tags, and use `--model` to override the model
inside the selected provider configuration for this invocation:

```bash
python -m general_manager.chat.evals \
  --settings myproject.settings \
  --dataset demo_readiness \
  --tier 1 \
  --tag grounding \
  --tag discovery \
  --model llama3 \
  --trace-jsonl /tmp/chat-eval.jsonl
```

Compare providers by passing their import paths as one comma-separated value:

```bash
python -m general_manager.chat.evals \
  --settings myproject.settings \
  --dataset basic_queries \
  --fixture toy \
  --compare general_manager.chat.providers.OllamaProvider,myproject.chat.TestProvider
```

The command exits with status 0 when every selected case passes and status 1
when any case fails. Provider imports, provider construction, Django setup, and
dataset-loading errors propagate and produce a nonzero process exit.

## 4. Interpret the report

Treat hard product-contract failures as regressions. Strategy diagnostics can
identify a skipped discovery path even when the final answer still satisfies
the product contract. Use `--verbose` for failure details and `--trace-jsonl`
when you need the full per-case trace.

See the [chat prompt and eval model](../concepts/chat_prompting.md), the
[copy-ready command recipes](../examples/chat_eval_cli.md), and the complete
[chat eval CLI reference](../api/chat.md).

## 5. Roll out planned chat safely

Keep planned chat disabled by default while you validate the application's
catalog, profile construction, role mappings, and single trust group. Start
with the implicit `default` profile (the existing legacy provider and
configuration) or configure explicit `planner`, `simple_executor`,
`complex_executor`, `synthesizer`, and `fallback_executor` roles. Use one
non-production environment first; public requests must never select profiles or
trust groups.

Add deterministic fake-provider cases for graph validation, manager resolution,
round exhaustion, 90-second evidence and 30-second synthesis deadlines,
calculation evidence, partial coverage, and every stable terminal reason. Run
those cases together with the existing legacy WebSocket, SSE, and HTTP tests.
Then enable `GENERAL_MANAGER["CHAT"]["planned"]["enabled"]` for the
non-production environment and inspect the allowlisted audit events: role,
match-source category, hashed canonical call identity, progress, budgets,
latency, usage/cost, evidence counts, coverage, and terminal reason. Do not
add raw results, profile names, trust groups, plans, hidden manager metadata,
credentials, or provider exceptions to an audit sink.

Production rollout is an application-owned settings change and requires no
migration. If an evaluation or operational check regresses, disable planned
mode; the next request uses the compatible legacy strategy. Mutation requests
already use that legacy safety path, including its authentication, mutation
allow-listing, confirmation, persistence, and transport behavior.

## 6. Run deterministic planned orchestration evaluations

The shipped `planned_orchestration` dataset is exercised by deterministic
role-pinned fake providers in the test suite, not by a network provider. It
covers alias resolution, a one-edge dependency, dynamic children, calculation,
partial answers, budget and deadline exhaustion, duplicate calls, no-progress
termination, and mutation fallback. Run it with the legacy eval regressions and
sanitized-diagnostic checks:

```bash
python -m pytest \
  tests/unit/test_chat_planned_evals.py \
  tests/unit/test_chat_evals.py \
  tests/unit/test_chat_eval_diagnostics.py -q
```

These tests require a role override for every planned role and reject mixed
trust groups. They assert deterministic fingerprints, aggregate provider usage,
public coverage, and traces with profile and trust-group values removed. The
legacy strategy remains a separate adapter, so existing `_run_turn` behavior
and event shape remain covered independently.
