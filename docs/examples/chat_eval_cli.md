# Installed Chat Eval Commands

These commands use datasets packaged with GeneralManager. Replace
`myproject.settings` and provider paths with importable modules from the Django
project where the command runs.

## Smoke-test the installed wheel

```bash
python -m general_manager.chat.evals \
  --settings myproject.settings \
  --provider general_manager.chat.providers.OllamaProvider \
  --fixture toy \
  --dataset basic_queries \
  --tier 0
```

This is the smallest directly usable installed-package check: it configures
Django, registers the toy managers expected by `basic_queries`, loads the YAML
dataset from package data, constructs the provider, and returns status 1 if any
case fails.

## Use project data and the configured provider

```bash
DJANGO_SETTINGS_MODULE=myproject.settings \
python -m general_manager.chat.evals \
  --dataset follow_ups \
  --tag grounding \
  --verbose \
  --trace-jsonl /tmp/follow-ups.jsonl
```

No fixture is registered in this version, so the dataset must match managers
and data exposed by `myproject.settings`. Because `--provider` is absent, the
command imports the provider from `GENERAL_MANAGER["CHAT"]`.

## Compare two provider classes

```bash
python -m general_manager.chat.evals \
  --settings myproject.settings \
  --fixture toy \
  --dataset basic_queries \
  --compare general_manager.chat.providers.OllamaProvider,myproject.chat.CandidateProvider
```

The comparison succeeds only if every result from both providers passes. See
the [task guide](../howto/run_chat_evals.md) for setup and the
[CLI reference](../api/chat.md) for every option and exit status.
