# Chat evaluation transport helpers

The active structural evaluation is [gm_eval](../gm_eval/README.md). This
package supplies reusable Responses transport helpers and offline synthetic
proofs over the production scheduler. The former standalone toy live evaluator
has been removed.

## Development setup and offline checks

Use an isolated checkout with Python 3.12 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements/development.txt
python -m pip install -e .
python -m pytest tests/experiments/test_siwc_eval.py tests/experiments/test_siwc_decoder.py -q
python -m experiments.siwc_eval.runner
```

The development requirements include
[`requirements/chat-eval.txt`](../../requirements/chat-eval.txt). Its `httpx`
dependency exercises the actual SSE and disconnect parser with mock transport;
`PyJWT[crypto]` and `cryptography` exercise signed synthetic OIDC tokens. These
are test and experiment dependencies, separate from the installed product's
runtime dependencies. Both the ordinary and database CI test jobs install them.

The default runner is offline and uses synthetic fixtures, replay transport and
negative controls. The experiment test fixture rejects external connections.
Run the active structural harness using the commands in the gm_eval README.

## Replacement for the retired toy live command

For an infrastructure proof through the current structural harness, run:

```bash
PYTHONPATH=src:. python -m experiments.gm_eval harness \
  --case E101 --profile weak-only --output /tmp/gm-eval-native-smoke
```

Use a fresh output directory for each run. This command exercises a real
five-manager fixture and production orchestration using scripted providers;
it is **offline** and measures no live answer accuracy. It is not an automatic
replacement login or inference call. To evaluate an installed provider such as
Ollama against packaged datasets, use the maintained
[module CLI](../../docs/howto/run_chat_evals.md). Its settings, provider/model
selection, comparison, fixture, filter and trace options remain available.
Live SIWC evaluation continues to require an explicitly supplied transport and
the current structural harness's authorization and admission gates.

## Transport and authentication entry points

`provider.py` maps the shared message and tool abstractions into Responses
requests. `http.py` and `suite.py` handle streaming, explicit terminal states and
request limits. A stream without `response.completed` does not establish a
completed inference; the helpers perform no automatic inference POST retry.

`gm_eval.profiles.siwc_factory(transport)` accepts an explicitly supplied
transport and does not log in or read personal credentials. The old
`runner --live` and `--case` flags now exit with status 2 and a migration message
before Django setup, prompts, login, registration or inference. No larger request
allowance or hidden live fallback replaces the old three-request live command.
`compare` remains a separate, explicit live entry point with saved-session
support; its candidate and Judge models remain explicit in the code and must be
available in the account's model catalog.

`auth.py` verifies PKCE, callback state, nonce and the signed OIDC identity.
`persistent_auth.py` owns saved-session locking, refresh and explicit logout.
Saved registration and session files live outside the repository. Importing
these helpers or running the offline commands does not initiate registration,
login or inference. See the [official SIWC documentation](https://developers.openai.com/siwc/token-sharing-open-source)
for account eligibility and the current authentication contract.

## Historical calibration helper

`calibrate_answer_judge.py` is an optional historical calibration helper. Its
`calibration_items()` function requires private saved trace files from the
original calibration run, which are maintained as separate review evidence.
That command is not part of the offline setup or the synthetic regression suite.
The import-only regression does not establish that those external inputs exist.

New candidate reports identify `execution_contract: chat-evaluation/4` and
`strategy: planned`; the behavior fingerprint binds that execution contract.
The answer Judge policy remains `answer-correctness-v3`. Historical field names
such as `legacy_trace` and `legacy_diagnostics` remain format-compatible readers
of transcript and lexical-score evidence; they do not select an old execution
loop. Existing stored reports are not rewritten.
The `/4` package contract adds a bounded lexical result-list check; it does not
extend the independent answer Judge's semantic policy or regrade old runs.
