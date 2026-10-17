# Run the installed chat evaluation suite

See [evaluation contracts and migration](../chat/evaluation-contracts.md) for
completion requirements, PASS/FAIL/UNSCORED reporting and historical baselines.

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
CLI runs the six provider-backed datasets through production Planned orchestration by default. The
`planned_orchestration` dataset is packaged separately for deterministic,
role-pinned tests and is not selected by the provider-backed CLI. The `basic_queries`
dataset can use the built-in toy schema and data:

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
`GENERAL_MANAGER["CHAT"]`. Omit `--dataset` to run every provider-backed
shipped dataset whose managers and expectations fit the configured project.
Selecting `--dataset planned_orchestration` in this CLI is rejected; use the
deterministic test command in section 6 instead. Use `--fixture large` with
`large_schema`; omit fixtures when the selected dataset is intended to run
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

Validate the application's catalog, profile construction, role mappings and
single trust group before enabling chat traffic. Every new read already uses
Planned orchestration. The configured provider supplies the implicit `default`
profile:

```python
GENERAL_MANAGER = {
    "CHAT": {
        "enabled": True,
        "provider": "general_manager.chat.providers.OllamaProvider",
        "provider_config": {"model": "gemma4:e4b"},
        "planned": {
            "enabled": True,
            "catalog": "myproject.chat.catalog.catalog",
        },
    }
}
```

For role-specific models, configure explicit profiles. Every role must be
mapped, every profile must provide a `trust_group`, and all mapped profiles
must use the same trust group:

```python
GENERAL_MANAGER["CHAT"].update(
    {
        "provider_profiles": {
            "fast_local": {
                "provider": "general_manager.chat.providers.OllamaProvider",
                "provider_config": {"model": "gemma4:e4b"},
                "trust_group": "local",
            },
            "strong_local": {
                "provider": "general_manager.chat.providers.OllamaProvider",
                "provider_config": {"model": "qwen3.5:9b"},
                "trust_group": "local",
            },
        },
        "planned": {
            "enabled": True,
            "catalog": "myproject.chat.catalog.catalog",
            "roles": {
                "planner": "strong_local",
                "executor": "strong_local",
                "synthesizer": "strong_local",
                "fallback": "strong_local",
            },
            "max_concurrent_tasks": 3,
            "evidence_timeout_seconds": 90,
            "synthesis_timeout_seconds": 30,
        },
    }
)
```

The catalog callable must return metadata for already chat-exposed managers.
Each entry requires `domain`, `aliases`, `use_when`, and `distinguish_from`.
Catalog metadata improves local ranking only; it does not expose managers or
change permissions:

```python
def catalog():
    return {
        "PartManager": {
            "domain": "manufacturing",
            "aliases": ["part", "component"],
            "use_when": "The question concerns designed components.",
            "distinguish_from": ["MaterialManager"],
        },
    }
```

Run `python manage.py check` before enabling traffic. Use one non-production
environment first; public requests must never select profiles or trust groups.

Add deterministic fake-provider cases for graph validation, manager resolution,
round exhaustion, 90-second evidence and 30-second synthesis deadlines,
calculation evidence, partial coverage, and every stable terminal reason. Run
those cases together with the existing mutation WebSocket, SSE, and HTTP tests.
Planned is the default for every new read. In the non-production environment,
inspect the allowlisted audit events: role,
match-source category, hashed canonical call identity, progress, budgets,
latency, usage/cost, evidence counts, coverage, and terminal reason. Do not
add raw results, profile names, trust groups, plans, hidden manager metadata,
credentials, or provider exceptions to an audit sink.

Migrate explicit role maps to `planner`, `executor`, `synthesizer`, and `fallback`
before rollout. `planned.enabled=False` is deprecated and does not restore the
previous read loop. If operational checks regress, roll back the application
release or disable chat with the global `CHAT.enabled` switch. Mutation requests
retain their safety path, including authentication, mutation
allow-listing, confirmation, persistence, and transport behavior. See the
[planned-chat cookbook](../examples/planned_chat_orchestration.md) for a
client-visible event sequence and the [Chat API reference](../api/chat.md#planned-read-orchestration)
for exact payload and error contracts.

## 6. Run deterministic planned orchestration evaluations

The shipped `planned_orchestration` dataset requires explicit deterministic
role pins and is excluded from the network-provider CLI. Tests replace provider
and tool IO while running the same production scheduler. They cover aliases,
dependencies, dynamic children, calculations, partial answers, deadlines,
duplicate calls and failed-operation cycles:

```bash
python -m pytest \
  tests/unit/test_chat_planned_evals.py \
  tests/unit/test_chat_eval_execution.py \
  tests/unit/test_chat_eval_diagnostics.py -q
```

Role pins must cover every role in one trust group. The archived
`legacy_stability` and `mutation_fallback` definitions remain readable, but no
legacy evaluator or mutation fallback executes them. A mutation plan in this
read-only evaluator returns `read_only_eval_mutation_plan`; application writes
continue to use the production permission and confirmation workflow.

## 7. Migrate older evaluation integrations

The module CLI, source-checkout helper and readiness driver now use one thin
adapter over the real production scheduler and exposed GraphQL tools. Ollama
and custom no-argument provider classes remain supported. The selected provider
is pinned to planner, executor, synthesizer and fallback; execution is serial.
Do not substitute the deterministic fixture adapter for a provider comparison.

New runs identify `chat-evaluation/3`. Earlier `/1` and `/2` summaries and raw
traces remain readable and retain their original meaning; establish a new
baseline rather than compare different execution contracts. Dataset expectations
and historical result files are unchanged.

Internal `run_case(..., strategy="legacy")`, `recover_missing_tools=True` and
arbitrary custom tool definitions now fail with an explicit migration error.
Omit `tool_defs` to use the scheduler-owned definitions. `None`, an empty list
and the exact current `get_tool_definitions()` list are compatibility sentinels,
not tool overrides. Modified schemas, subsets and duplicates are rejected before
any provider call. Returned tool definitions own their nested schema data;
editing them cannot change the native contract or another tool's definition.
Discovery is refreshed from the current exposed schema for
every conversation turn, as it is in the application transports.
No harness code injects queries, rewrites arguments or manufactures an answer.
`max_provider_requests` is a hard per-turn dispatch cap (default 10, range 1–64)
across all four roles, including failed attempts. `max_tool_iterations` is a
compatibility alias for that cap; passing both names is rejected. It no longer
means executor-only iterations and cannot authorize an extra final-answer call.
The SIWC suite explicitly retains its 16-request per-turn and existing per-case
transport ceilings. Native deadlines, completion checks and transport STOP
latches remain authoritative.

The separate experimental `experiments.siwc_eval.runner --live` toy entry has
been removed. Its old flags fail with status 2 before setup or authentication;
its three-request limit has not been silently enlarged. For an offline infrastructure proof in a source checkout, use a fresh output
directory:

```bash
PYTHONPATH=src:. python -m experiments.gm_eval harness \
  --case E101 --profile weak-only --output /tmp/gm-eval-native-smoke
```

This uses scripted providers and the real production scheduler; it does not
log in, call a live model or measure answer accuracy. Explicit live SIWC runs
still require their existing transport and admission gates.
