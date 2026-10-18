# GeneralManager structural evaluation development guide

This source-checkout harness exercises the production planned chat scheduler with
synthetic manager landscapes, deterministic fixture checks and separately bound
candidate/judge measurements. It is outside the distributed `src/` package.
For ordinary installed-provider comparisons, use
[the maintained module CLI](../../docs/howto/run_chat_evals.md). For contracts,
read [evaluation concepts](../../docs/concepts/chat_evaluation.md).

## What runs offline

Use Python 3.12+ with the repository's development dependencies and editable
install. The following commands use scripted providers and disposable fixtures;
they do not register/log in to an account or call an external model:

```bash
export PYTHONPATH="$PWD/src:$PWD"
python -m experiments.gm_eval catalog --output /tmp/gm-eval-catalog
python -m experiments.gm_eval fixtures --output /tmp/gm-eval-fixtures
python -m experiments.gm_eval parity --output /tmp/gm-eval-parity
python -m experiments.gm_eval harness --case E101 --profile weak-only --output /tmp/gm-eval-small
python -m experiments.gm_eval offline --output /tmp/gm-eval-offline
```

Choose a fresh output directory for each invocation. `--case` is repeatable;
`--scale` selects 5, 10, 50, 100 or 250 managers. Scripted profiles are
`weak-only`, `weak-fallback` and `strong-only`. `offline` combines selected
catalog/oracle validation, fixture checks and the default transport-parity cases.
Use `python -m experiments.gm_eval --help` to inspect exact options.

Each fixture starts an isolated Django subprocess and temporary SQLite database.
It registers real Database, ReadOnly and Calculation interfaces and verifies the
schema census. Distractors occupy declared manager slots rather than inflating
counts. The fixture's explicit read-only synchronization opt-in belongs to its
disposable seed phases, not a change to application startup defaults.

`report.json` is written before the CLI decides its exit status. A structural
metadata key named `status` is not itself a result. Successful infrastructure
checks exit 0; actual model-task/infrastructure failures or failed parity exit 1.
No scripted success is a live model-quality or production-readiness score.

## Source ownership map

| Responsibility | Owning source |
| --- | --- |
| Command selection and artifacts | `cli.py`, `__main__.py` |
| Production scheduler binding and conversations | `harness.py`, `runtime.py` |
| Provider profiles and terminal failure latch | `profiles.py` |
| Frozen case/reference definitions | `catalog.py`, `catalog*.json`, reference metadata |
| Synthetic data and Django fixture application | `seeds.py`, `fixture_app/` |
| Expected facts and same-metric source declarations | `oracle.py` |
| Blind extraction and deterministic comparison | `adjudication.py`, `scoring.py` |
| Reporting, trace and request-size diagnostics | `reporting.py`, `trace.py`, `efficiency.py` |
| Source/history/validation certificates | `semantic_contracts.py`, `validation_*.py`, `stream_lifecycle.py` |
| Regression tests | `tests/experiments/` and relevant `tests/unit/` modules |
| Results | The explicit external `--output` directory for that run |

Keep machine-bound reference JSON, contract owners and complete source manifests
at the paths recorded by their actual version. Internal helpers may be organized
by implementation responsibility; they are not additional public entry points.
Never rewrite an old manifest to pretend that a new layout produced an old run.

## Reference selection and reproducibility

The current default is **1.17**. The catalog contains 121 original cases and
149 conversation turns across 5/10/50/100/250-manager fixtures; a selected campaign
or named historical denominator is a separate cohort. Do not confuse catalog
size, turns, a campaign selection and scored outcomes.

Explicit 1.10–1.16 selectors retain their archived expectations/extraction
shapes. Reproducing an old run requires its exact reference, source and request
contract. A new default does not rescore a saved answer. Reference corrections
are recorded in versioned oracle/catalog metadata; old source fixtures, requests,
responses and raw reports remain unchanged.

The reference's business clock, calendars, quantity/revenue definitions, status
meaning, missing-value treatment and source constraints must be visible in the
candidate's supplied fixture definitions where applicable. Gold values and
expected input rows remain evaluator-only. Do not repair claims using expected
facts or add a case-ID/manager-name exception to obtain a pass.

Current semantic contracts distinguish result membership from asserted ranking,
selected winners from explanatory comparisons, unknown values from zero, and
physical provenance from generic citations. Every volunteered factual claim
still needs support. Reference 1.17 permits reviewed complete same-metric input
routes only with actual eligible answer usage and strict source restrictions;
matching final numbers alone are insufficient. It also accepts the closed
low/high scenario object only in the forecast scenario year slot. See
[evaluation concepts](../../docs/concepts/chat_evaluation.md) for these boundaries.

## Running and judging a candidate

A candidate uses production role construction, `prepare_planned_turn`,
`iter_planned_read_events`, native tools and durable conversation storage.
The adapter is read-only. Explicit scripted providers test infrastructure;
provider-backed runs need the selected real provider/transport and source-bound
admission. No helper silently injects a missing query, widens schema exposure,
repairs tool arguments or fabricates an answer.

Retain complete original conversations, request/response bodies, call IDs,
source hashes, role/profile settings, fixture selection, terminal provenance,
usage and serialized request sizes. The judge sees answer claims and eligible
independent evidence, without Gold. The scorer checks exact results, arithmetic,
units, populations and declared source routes. PASS, FAIL and UNSCORED all stay
visible on the full selected denominator; not-executed work remains explicit.

A separately reviewed source correction requires a new candidate/control binding.
An offline reassessment of a saved answer has its own old-report hash, selected
new reference and factual review; it is not a new candidate or judge invocation.

## Live transport boundary

The GM/SIWC integration accepts an explicitly supplied supported transport.
Importing this package and running the offline commands do not read personal
credentials or log in. Transport/session lifecycle belongs to
[the SIWC helpers](../siwc_eval/README.md), not to the oracle or scorer.

Live runs must bind the actual source, provider/judge models, case order,
original turns, request ceilings, body guard, deadlines and independent review.
A script or static PASS is not a start capability. Preserve auth, quota,
transport and integrity STOPs. Do not automatically retry an uncertain inference
POST, replay an interrupted request or relabel unknown remote completion.

Production chat defaults to 90 seconds for planning/evidence and 30 for synthesis.
A separately selected diagnostic profile may use 180/90 and a 300,000-character
full JSON guard. Earlier 200,000-character campaigns retain their original limits;
these are experiment-specific settings, not general exceptions or production
defaults. Report the actual bound settings and keep production compliance
unmeasured unless it was actually measured.

## Historical contract authority

Historical result files and external review evidence remain outside the published
user guide. Git history retains earlier remediation notes. One exact source
contract is intentionally retained at
[`docs/chat/eval-stream-metadata-r58.md`](../../docs/chat/eval-stream-metadata-r58.md)
because `stream_lifecycle.py` references it as provenance authority. It is excluded
from the published documentation rather than rewritten or deleted. The local
metadata policy it describes is not a public provider protocol specification.
