# Run the installed chat evaluation suite

See [evaluation concepts](../concepts/chat_evaluation.md) for
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
when any case fails or is unscored. An empty selection is rejected; it is not a pass. Provider imports, provider construction, Django setup, and
dataset-loading errors propagate and produce a nonzero process exit.

## 4. Interpret the report

Treat hard product-contract failures as regressions. Strategy diagnostics can
identify a skipped discovery path even when the final answer still satisfies
the product contract. Use `--verbose` for failure details and `--trace-jsonl`
when you need the full per-case trace.

See [evaluation and reproducible comparisons](../concepts/chat_evaluation.md), the
[copy-ready command recipes](../examples/chat_eval_cli.md), and the complete
[chat eval CLI reference](../api/chat.md).


## 5. Freeze comparisons and retain evidence

The CLI freezes the actual ordered selected cases once and reuses them for each
compared provider. Keep the settings/source revision, fixture identity, provider
and model configuration, full outcome denominator and traces with the report.
Do not exclude UNSCORED results or silently replace a failed follow-up with an
earlier successful turn. Comparisons across evaluation contracts or different
case definitions need a new explicitly established baseline.

Both source CLIs use fingerprint contract `chat-run-fingerprint/2`. Its prompt
hash includes the existing system prompt plus the native planner, executor and
synthesis instructions, their conditional and repair variants, and active schema
transport instructions. The scoring contract remains `chat-evaluation/5`.
Historical fingerprints remain unchanged; summaries without the new fingerprint
contract cannot serve as baselines for new native runs. Establish a new baseline
instead of rewriting historical artifacts. Within the same fingerprint contract,
comparisons across provider, model and prompt hashes remain supported.

The production stage defaults are 90 seconds for planning/evidence and 30 for
synthesis. Configure roles and domain metadata using
[the configuration guide](configure_chat.md); verify the application with
`python manage.py check`. A diagnostic experiment's longer deadline or larger
request guard is not a production default.

## 6. Test deterministic orchestration in a source checkout

The `planned_orchestration` dataset uses explicit deterministic role pins. It is
not a network-provider CLI dataset. The relevant source tests run the production
scheduler with controlled provider/tool IO:

```bash
python -m pytest   tests/unit/test_chat_planned_evals.py   tests/unit/test_chat_eval_execution.py   tests/unit/test_chat_eval_diagnostics.py -q
```

These test mechanics and negative outcomes, not model answer accuracy. A negative
scheduler scenario can pass pytest while its captured quality outcome correctly
remains FAIL or UNSCORED.

## 7. Migrate older integrations

New runs identify `chat-evaluation/5` and use the production planned scheduler.
The selected no-argument provider class is bound across planner, executor,
synthesizer and fallback, with serial evaluator execution. Old `/1`, `/2`, `/3` and `/4`
reports remain readable and unchanged; they are not comparable new baselines.
Contract `/5` retains the bounded result-list check and adds conservative
quantity-prose abstention after a recognized list; it does not establish factual
correctness of arbitrary prose or quantity claims. See the
[coverage limits](../concepts/chat_evaluation.md#packaged-answer-checks-have-limited-coverage).

Remove `strategy="legacy"`, `recover_missing_tools=True` and custom `tool_defs`
overrides from internal `run_case` callers. Omit `tool_defs` to use native
scheduler definitions. `None`, an empty list and the exact current
`get_tool_definitions()` list remain compatibility sentinels, not overrides.
Modified schemas, subsets and duplicates reject before provider calls.

`max_provider_requests` is the hard per-turn dispatch cap across all roles,
including failed attempts (default 10, supported range 1–64).
`max_tool_iterations` is its compatibility alias; do not supply both. It does not
mean executor-only iterations or authorize an extra final-answer request.
This cap is separate from normal production round accounting.

Custom providers must emit exactly one terminal `DoneEvent`, then end/close the
iterator successfully. EOF alone and a cleanup failure cannot establish success.
Retain original provider/timeout/cancellation provenance and observed usage.

The retired standalone `experiments.siwc_eval.runner --live` toy flags reject
before setup/authentication. Source-checkout infrastructure experiments are
explained in the [GM development guide](https://github.com/TimKleindick/general_manager/tree/feature/chat-schema-executor-v53/experiments/gm_eval).
They do not log in implicitly or authorize an automatic live replacement.
