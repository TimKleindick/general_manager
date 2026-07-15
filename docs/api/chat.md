# Chat API

## Installed evaluation CLI

```text
python -m general_manager.chat.evals [OPTIONS]
```

The module command configures Django, optionally registers a built-in schema
fixture, loads packaged YAML datasets, constructs one or more providers, runs
the selected cases synchronously, prints a report, and returns no Python value.
`python -m general_manager.chat.evals --help` can run without Django settings;
every evaluation run requires either `--settings MODULE` or a nonempty
`DJANGO_SETTINGS_MODULE`.

| Option | Value and behavior |
| --- | --- |
| `--settings` | Import path for the Django settings module. Overrides an existing `DJANGO_SETTINGS_MODULE` for this process. |
| `--provider` | Provider class import path. When omitted, GeneralManager imports the provider configured in `GENERAL_MANAGER["CHAT"]`. |
| `--model` | Model name merged into the selected provider configuration before provider construction. |
| `--dataset` | One packaged dataset name. When omitted, the runner selects all datasets. |
| `--fixture` | `toy` or `large`; registers the matching built-in eval schema before the run. |
| `--tier` | Integer tier filter. |
| `--tag` | Required tag filter; repeat the option to pass multiple tags. |
| `--compare` | Comma-separated provider class import paths. Runs each provider and prints a comparison report instead of the single-provider report. |
| `--verbose`, `-v` | Includes detailed failure information in a single-provider report. |
| `--trace-jsonl` | File path that receives per-case JSONL traces. |

The packaged dataset names are `basic_queries`, `demo_readiness`, `edge_cases`,
`follow_ups`, `large_schema`, and `multi_hop`. Dataset resources are part of the
wheel and source distribution as of GeneralManager 0.62.3.

### Exit status and failures

- Status 0: argument help was displayed, or every selected result passed.
- Status 1: at least one selected result failed.
- Status 2: argument parsing failed, including a run without Django settings.
- Other nonzero termination: Django setup, provider import/construction,
  fixture registration, dataset loading, trace writing, or evaluation raised an
  exception. Those exceptions are not converted into a CLI-specific error type.

`--settings` takes precedence over `DJANGO_SETTINGS_MODULE`; `--provider` takes
precedence over the configured provider; and `--compare` takes precedence over
the single-provider selection. `--model` applies to every constructed provider
by temporarily overriding the `GENERAL_MANAGER["CHAT"]` provider configuration.

### Compatibility

GeneralManager 0.62.3 stopped defaulting the installed command to
`tests.test_settings` and began shipping the built-in YAML datasets as package
data. Existing automation must now pass `--settings` or set
`DJANGO_SETTINGS_MODULE`. The help command remains settings-free.

The Python modules below implement the harness but are not registered stable
package exports. Application automation should prefer the module CLI documented
above rather than import runner internals directly.

See the [task guide](../howto/run_chat_evals.md) and
[command cookbook](../examples/chat_eval_cli.md).
