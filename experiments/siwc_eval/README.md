# Chat evaluation transport helpers

The active structural evaluation is [gm_eval](../gm_eval/README.md). This
package supplies its reusable Responses transport and synthetic tests, together
with the older standalone toy-evaluation commands.

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

## Transport and authentication entry points

`provider.py` maps the shared message and tool abstractions into Responses
requests. `http.py` and `suite.py` handle streaming, explicit terminal states and
request limits. A stream without `response.completed` does not establish a
completed inference; the helpers perform no automatic inference POST retry.

`gm_eval.profiles.siwc_factory(transport)` accepts an explicitly supplied
transport and does not log in or read personal credentials. The standalone
`runner --live` command requests interactive confirmation before authentication
and inference. `compare` is a separate live entry point with saved-session
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
