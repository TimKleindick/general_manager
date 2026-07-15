# Run the installed chat evaluation suite

Use the installed module CLI when you want to evaluate a provider against the
datasets shipped in the GeneralManager wheel. The command runs inside a Django
project, so it must load that project's settings before it imports providers or
registers fixtures.

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
`follow_ups`, `large_schema`, and `multi_hop`. The `basic_queries` dataset can
use the built-in toy schema and data:

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
