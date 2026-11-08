# Search-permission-routing benchmark results

The subsequent [SQL optimization comparison](sql-optimization.md) is the latest
result for the working tree. This report retains the measurements from the
initial implementation, before that follow-up.

## Initial implementation: final KnowledgeHub comparison

The final idle canonical comparison for the initial implementation ran
the same KnowledgeHub fixture and real GraphQL schema requests in balanced
**A-B-C-C-B-A** order: A = immutable GM 0.86.4, B = immutable GM 906, and C =
the current candidate workspace. Every round passed both search tests. Each
variant has 10 cold and 10 warm samples per case (five per process), one
untimed warmup, and no fixture setup/indexing in the timing boundary.

All three variants returned equal fixture, request-wire, response, and result
hashes for rich search, sparse 39-source WorkCenter search, and anonymous
search. The complete per-sample values, SQL counts, hashes, environment
bindings, and source fingerprints are in
[`final-comparison.json`](final-comparison.json). Times are milliseconds;
cells show median [inclusive IQR], min–max, and SQL/request.

IQR and range describe the observed sample dispersion, not a confidence
interval. Two processes and ten samples per mode are diagnostic measurements.
"Cold" clears all configured Django cache aliases before each sample;
Python/schema, OS, and SQLite buffers are already initialized. "Warm" clears
the aliases once before its untimed warmup. Neither mode measures a cold
process or database.

| Case | Variant | Cold | Warm | Functional / hashes |
| --- | --- | ---: | ---: | --- |
| rich-search | GM 0.86.4 | 5.530 [0.330], 5.380–5.921 / 13 | 5.557 [0.483], 5.172–5.887 / 13 | pass / equal |
| rich-search | GM 906 | 10.983 [0.278], 10.631–11.739 / 20 | 10.665 [0.191], 10.534–11.946 / 20 | pass / equal |
| rich-search | current candidate | 8.505 [0.209], 8.426–8.886 / 13 | 8.436 [0.286], 8.200–9.729 / 13 | pass / equal |
| 39-visible-work-centers | GM 0.86.4 | 1.426 [0.198], 1.353–3.013 / 2 | 1.530 [0.221], 1.331–1.668 / 2 | pass / equal |
| 39-visible-work-centers | GM 906 | 21.987 [0.416], 21.427–23.101 / 42 | 21.900 [0.173], 21.143–22.808 / 42 | pass / equal |
| 39-visible-work-centers | current candidate | 3.857 [0.255], 3.765–4.094 / 4 | 3.806 [0.249], 3.748–4.037 / 4 | pass / equal |
| anonymous-search | GM 0.86.4 | 1.864 [0.162], 1.654–2.579 / 0 | 1.827 [0.029], 1.621–1.962 / 0 | pass / equal |
| anonymous-search | GM 906 | 1.891 [0.076], 1.736–2.028 / 0 | 1.892 [0.124], 1.731–3.057 / 0 | pass / equal |
| anonymous-search | current candidate | 1.913 [0.031], 1.779–2.034 / 0 | 1.901 [0.047], 1.736–1.930 / 0 | pass / equal |

For WorkCenter, the current candidate is the measured fast path relative to
GM 906 (warm 3.806 ms / 4 SQL versus 21.900 ms / 42 SQL), though it remains
slower than the 0.86.4 baseline (1.530 ms / 2 SQL). This is a controlled
in-process classification, not a production latency claim.

Source bindings were verified before execution and the current candidate
fingerprint was identical before and after: manifest SHA-256
`67a365aa1aa1790691aa86b780cf9bd85f496dfde7d569cbd7b93b24abb4d2b0`.
The comparison used Python 3.14.10, Django 6.1.3, UTC, SQLite 3.53.1 in-memory
databases, and the canonical lazy actor harness. The supported environments
use filelock 3.24.3 for GM 0.86.4 and 4.2.9 for GM 906 and the candidate; the
comparison does not isolate GeneralManager from that dependency difference.
The candidate package metadata remains 0.87.5; the source path, not installed
metadata, identifies the candidate.

## Release-gate status

The original KnowledgeHub calendar and 1,000-booking project tests remain
blocked by `Query field authorization candidate limit exceeded.` The same
correctness-only runs against immutable origin/main `715c942f` fail at the
same `dailyEventList` and `projectStaffCostList` paths, so these two failures
pre-date the candidate and are not attributed to the search-routing change.
The candidate reproduces them; the final search timing above does not clear
those consumer gates. Detailed logs and the separate consumer-harness results
remain documented below and under `/tmp/kh-pr10933-*`.

The timing boundary is in-process GraphQL execution plus SQL capture and
formatted JSON encode/decode. It excludes HTTP/session/auth middleware,
browser/RTK, network, PostgreSQL, production indexes, and concurrent load.

## Synthetic search-permission-routing benchmark

This document records the reproducible GeneralManager-only diagnostic. It is
synthetic evidence for the search permission-routing change; it is not a
production cost estimate or an acceptance of the KnowledgeHub release gates.

The existing KnowledgeHub comparison remains complementary evidence. In
particular, `/tmp/kh-pr10933/kh-performance-comparison/comparison-report.md`
records the real rich-search, calendar, and 1,000-booking project cases and
their correctness blockers. This small fixture does not replace those queries
or fixtures.

The current worktree was also exercised against the supplied KnowledgeHub
search harness as a provisional source-binding check. That run is kept
separate from the immutable baseline/candidate comparison because the parent
candidate was still under security-review repair.

## Harness and boundary

`tests/perf/test_search_routing_benchmark.py` uses
`GeneralManagerTransactionTestCase`, a generated `DatabaseInterface` with only
native scalar fields, `ManagerBasedPermission`'s declarative public rules, and
`DevSearchBackend`. It creates deterministic rows named `needle row NNNN`,
indexes them before timing, and sends the actual GraphQL `search` request
through the test client. There are no properties, custom projections,
relations, or external search services.

The default fixture is 200 rows so an opt-in smoke run is practical. The
comparison fixture is 1,000 rows. Row creation and indexing are outside every
measured request. Each run performs one untimed warmup and seven measured
repetitions (the repetition count can be increased), recording elapsed
milliseconds and SQL count for every repetition. The report checks that all
successful payloads are equal within a source version and validates the exact
total, page metadata, first page, and filter result.

The benchmark is not selected by the normal suite. Opt in explicitly:

```bash
GENERAL_MANAGER_RUN_SEARCH_ROUTING_BENCHMARK=1 \
GENERAL_MANAGER_SEARCH_BENCHMARK_ROWS=200 \
GENERAL_MANAGER_SEARCH_BENCHMARK_SOURCE=local \
python -m pytest tests/perf/test_search_routing_benchmark.py -q -s
```

For a machine-readable report, add
`GENERAL_MANAGER_SEARCH_BENCHMARK_OUTPUT=/tmp/search-routing.json`. The
benchmark always prints one `SEARCH_ROUTING_BENCHMARK` JSON line. The source
metadata includes the benchmark path, imported GeneralManager paths,
`pyproject.toml` version, installed distribution version, and the explicit
source label. The imported `package_file` and `graphql_search_file` must point
to the selected archive/worktree, not to an unrelated installed checkout.

## Paired source commands

The same benchmark module is copied into immutable source archives. `pytest.ini`
sets `pythonpath = src`; the explicit `PYTHONPATH` below makes the selected
archive unambiguous as well. These commands use Python 3.12.4 (`python` in the
GeneralManager environment) and keep reports in `/tmp`.

```bash
ROOT=/tmp/gm-search-routing-benchmarks
mkdir -p "$ROOT/gm0864" "$ROOT/main715c942f"
git archive --format=tar v0.86.4 | tar -xf - -C "$ROOT/gm0864"
git archive --format=tar 715c942f | tar -xf - -C "$ROOT/main715c942f"
cp tests/perf/test_search_routing_benchmark.py \
  "$ROOT/gm0864/tests/perf/test_search_routing_benchmark.py"
cp tests/perf/test_search_routing_benchmark.py \
  "$ROOT/main715c942f/tests/perf/test_search_routing_benchmark.py"
```

Run the default-sized functional comparison:

```bash
cd "$ROOT/gm0864" && \
GENERAL_MANAGER_RUN_SEARCH_ROUTING_BENCHMARK=1 \
GENERAL_MANAGER_SEARCH_BENCHMARK_ROWS=200 \
GENERAL_MANAGER_SEARCH_BENCHMARK_SOURCE=v0.86.4 \
GENERAL_MANAGER_SEARCH_BENCHMARK_OUTPUT="$ROOT/gm0864-200.json" \
PYTHONPATH="$ROOT/gm0864/src" \
python -m pytest tests/perf/test_search_routing_benchmark.py -q -s

cd "$ROOT/main715c942f" && \
GENERAL_MANAGER_RUN_SEARCH_ROUTING_BENCHMARK=1 \
GENERAL_MANAGER_SEARCH_BENCHMARK_ROWS=200 \
GENERAL_MANAGER_SEARCH_BENCHMARK_SOURCE=origin-main-715c942f \
GENERAL_MANAGER_SEARCH_BENCHMARK_OUTPUT="$ROOT/main-200.json" \
PYTHONPATH="$ROOT/main715c942f/src" \
python -m pytest tests/perf/test_search_routing_benchmark.py -q -s
```

Run the fixed 1,000-row comparison. The `ALLOW_FAILURE` setting is only for
recording a known source-limit failure in the JSON report; omit it when a
failure should make pytest fail.

```bash
cd "$ROOT/gm0864" && \
GENERAL_MANAGER_RUN_SEARCH_ROUTING_BENCHMARK=1 \
GENERAL_MANAGER_SEARCH_BENCHMARK_ROWS=1000 \
GENERAL_MANAGER_SEARCH_BENCHMARK_SOURCE=v0.86.4 \
GENERAL_MANAGER_SEARCH_BENCHMARK_OUTPUT="$ROOT/gm0864-1000.json" \
PYTHONPATH="$ROOT/gm0864/src" \
python -m pytest tests/perf/test_search_routing_benchmark.py -q -s

cd "$ROOT/main715c942f" && \
GENERAL_MANAGER_RUN_SEARCH_ROUTING_BENCHMARK=1 \
GENERAL_MANAGER_SEARCH_BENCHMARK_ROWS=1000 \
GENERAL_MANAGER_SEARCH_BENCHMARK_SOURCE=origin-main-715c942f \
GENERAL_MANAGER_SEARCH_BENCHMARK_ALLOW_FAILURE=1 \
GENERAL_MANAGER_SEARCH_BENCHMARK_OUTPUT="$ROOT/main-1000.json" \
PYTHONPATH="$ROOT/main715c942f/src" \
python -m pytest tests/perf/test_search_routing_benchmark.py -q -s
```

The optional `GENERAL_MANAGER_SEARCH_BENCHMARK_RAISE_TOTAL_LIMIT=1` diagnostic
sets only `GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT` to rows + 1. It does not change the
1,000 field-decision ceiling and must be recorded separately from the primary
comparison:

```bash
cd "$ROOT/main715c942f" && \
GENERAL_MANAGER_RUN_SEARCH_ROUTING_BENCHMARK=1 \
GENERAL_MANAGER_SEARCH_BENCHMARK_ROWS=1000 \
GENERAL_MANAGER_SEARCH_BENCHMARK_SOURCE=origin-main-715c942f-raised-total-limit \
GENERAL_MANAGER_SEARCH_BENCHMARK_RAISE_TOTAL_LIMIT=1 \
GENERAL_MANAGER_SEARCH_BENCHMARK_ALLOW_FAILURE=1 \
GENERAL_MANAGER_SEARCH_BENCHMARK_OUTPUT="$ROOT/main-1000-raised-total-limit.json" \
PYTHONPATH="$ROOT/main715c942f/src" \
python -m pytest tests/perf/test_search_routing_benchmark.py -q -s
```

## Recorded baseline results

All rows below are the seven measured repetitions after one warmup. Times are
milliseconds; SQL is the count per measured request. Payload equality and
functional verification are reported by the harness, not inferred from timing.

| Source | Rows | Status | Median | Min | Max | SQL/request | Payload | Functional |
| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| v0.86.4 (`da9d21c6`) | 200 | ok | 52.427 | 52.013 | 58.151 | 200 | equal | verified |
| origin/main (`715c942f`) | 200 | ok | 31.497 | 31.207 | 34.113 | 1 | equal | verified |
| v0.86.4 (`da9d21c6`) | 1,000 | ok | 834.098 | 783.326 | 863.582 | 1,000 | equal | verified |
| origin/main (`715c942f`) | 1,000 | failed | 26.965 | 26.544 | 50.105 | 1 | n/a | no |

The origin/main 1,000-row samples consistently returned the GraphQL error
`Query field authorization candidate limit exceeded.` The optional raised-total-
limit run returned the same error, so it does not remove the field-decision
ceiling. The failure is recorded evidence, not a performance result and not a
claim that the source is functionally equivalent.

The local worktree smoke run (200 rows, before the final candidate handoff)
was successful: median 28.988 ms, min 28.817 ms, max 29.192 ms, 20 SQL per
request, equal payloads, and functional verification. The later 1,000-row
candidate diagnostics are recorded below, but remain separate from this
provisional smoke result and from any frozen release comparison.

## Remaining evidence

This synthetic fixture deliberately omits the KnowledgeHub work-center search,
the calendar queries, and the original 1,000-booking project queries. The
existing KnowledgeHub comparison report remains the source for those cases and
their original release-gate outcomes. A successful synthetic result cannot
clear those gates, and a failed synthetic source-limit run must remain visible
when interpreting any candidate timing.

## KnowledgeHub source-binding check (provisional)

The supplied canonical lazy-actor harness was run without modifying the
KnowledgeHub tree or either installed GM environment. The backend's default
file logger points at its read-only `logs/general_manager.log`; setting
`LOG_TO_STDOUT=1` is an isolated logging-only adjustment. `-p
canonical_request_actor` installs the existing fresh outer
`CalculationRunContext` and lazy auth-model lookup per request. `-p
no:cacheprovider` is recommended for reruns so pytest does not attempt to write
the read-only backend `.pytest_cache`.

The source-binding preflight selected the workspace source exactly:

```text
gm_file=/Users/tim/.codex/worktrees/c99b/general_manager/src/general_manager/__init__.py
search_file=/Users/tim/.codex/worktrees/c99b/general_manager/src/general_manager/api/graphql_search.py
distribution=0.87.5
```

Use this exact isolated command when the parent declares the final candidate
ready (change only `KH_PERF_VARIANT` and output directory labels as needed):

```bash
KH_BACKEND=/Users/tim/Documents/Codex/2026-11-06/task/knowledge_hub-pr10933-gm582/backend
KH_HARNESS=/tmp/kh-pr10933/kh-performance-comparison/harness
GM_SRC=/Users/tim/.codex/worktrees/c99b/general_manager/src
GM_PYTHON=/tmp/kh-pr10933/gm906cd0b-performance-venv/bin/python
KH_PERF_VARIANT=gm-current-worktree \
KH_PERF_ROUND=request-a \
KH_PERF_OUTPUT_DIR=/tmp/kh-pr10933-current-worktree-canonical-request/request-a \
PYTHONHASHSEED=0 TZ=UTC DJANGO_SETTINGS_MODULE=tests.pytest_settings \
LOG_TO_STDOUT=1 PROFILE_SERVER_IP_DEV=127.0.0.1 \
PROFILE_DOMAIN_DEV=profile.test.invalid CERTIFICATE_NAME=localhost \
PYTHONPATH="$GM_SRC:$KH_BACKEND:$KH_HARNESS" \
"$GM_PYTHON" -m pytest -c "$KH_BACKEND/pytest.ini" \
  -p no:cacheprovider -p canonical_request_actor \
  "$KH_HARNESS/test_kh_performance.py::TestRichSearch" \
  "$KH_HARNESS/test_kh_performance.py::TestSparseSearch" -q
```

The same command with `KH_PERF_ROUND=request-b` and output directory
`/tmp/kh-pr10933-current-worktree-canonical-request/request-b` was run as a
second order-control pass. Both provisional passes passed 2/2 tests. They
retain the harness boundary: in-process GraphQL schema execution, SQL capture,
JSON encode/decode and hashing; no HTTP/session/auth middleware, browser,
network, PostgreSQL, or production index.

| Provisional current-worktree case | Round A cold median / SQL | Round A warm median / SQL | Round B cold median / SQL | Round B warm median / SQL | Correct |
| --- | ---: | ---: | ---: | ---: | --- |
| rich-search | 8.358 ms / 13 | 8.265 ms / 13 | 8.117 ms / 13 | 8.150 ms / 13 | yes |
| anonymous-search | 1.838 ms / 0 | 1.820 ms / 0 | 1.795 ms / 0 | 1.806 ms / 0 | yes |
| 39-visible-work-centers | 3.994 ms / 4 | 4.312 ms / 4 | 3.865 ms / 4 | 4.037 ms / 4 | yes |

Fixture hashes matched the supplied harness in both rounds: rich search
`08a6d6a8ae197da2b83efeb2b1a165eac7119781aae0f84663fb5ca1124f5879` and
39-source search
`0f9fb65cebbd0bd6804422e2d4c1c9f356e661a54eebbc5d72a9a4fadb15b8bd`. The
outputs are isolated under
`/tmp/kh-pr10933-current-worktree-canonical-request/`; they are not source
archives, release evidence, or final-candidate measurements.

## Historical provisional KnowledgeHub comparison

The parent candidate was edited concurrently while the first A-B-B-A search
comparison was running. The completed run is therefore retained as
**provisional**, not as a frozen-candidate release measurement. It used the
same canonical lazy actor plugin, SQLite in-memory database, fixture, request
and result checks as the supplied harness. Outputs are under
`/tmp/kh-pr10933-current-candidate/`.

The completed A-B-B-A order was baseline-a, candidate-a, candidate-b,
baseline-b, with 10 cold and 10 warm samples per case and variant. The
candidate samples were equal to the recorded GM 0.86.4/0.87.5 canonical
fixture and request/result hashes (rich fixture
`08a6d6a8ae197da2b83efeb2b1a165eac7119781aae0f84663fb5ca1124f5879`, sparse
fixture
`0f9fb65cebbd0bd6804422e2d4c1c9f356e661a54eebbc5d72a9a4fadb15b8bd`).
Times are milliseconds; each cell is median [inclusive IQR], with min/max and
SQL count shown separately.

| Case | Baseline cold | Candidate cold | Baseline warm | Candidate warm | SQL baseline → candidate |
| --- | ---: | ---: | ---: | ---: | ---: |
| rich-search | 5.796 [0.172], 5.690–6.147 | 10.191 [1.466], 9.245–12.346 | 5.731 [0.128], 5.450–6.387 | 10.439 [0.653], 9.321–10.932 | 13 → 13 |
| 39-visible-work-centers | 1.475 [0.198], 1.375–3.208 | 4.914 [0.586], 4.589–5.628 | 1.556 [0.202], 1.344–1.650 | 4.932 [0.460], 4.568–7.364 | 2 → 4 |
| anonymous-search | 1.950 [0.203], 1.677–2.800 | 2.134 [0.246], 1.887–2.440 | 1.856 [0.088], 1.649–1.945 | 1.997 [0.139], 1.779–3.409 | 0 → 0 |

These numbers are diagnostics only: parent edits occurred during the run and
the host was subsequently shared with the full GeneralManager suite. The
pre/post source manifests are retained as
`/tmp/kh-pr10933-current-candidate/source/gm-current-before.sha256` and
`gm-current-after.sha256`; they differ in `graphql_search.py`,
`manager_based_permission.py`, and `search/authorization.py`. A later gate
snapshot changed `graphql_query_permissions.py` and `search/projection.py`;
its post-gate and post-synthetic manifests are identical, with manifest hash
`3a435ab1369d517a7935059c21e902b5f3b6dca72c69cce4dd83d76635f47d07`.
This historical two-variant run is superseded by the final idle three-variant
comparison at the beginning of this report. Its source drift and shared-host
interference are retained here so the provisional numbers are not mistaken for
the final result.

The original KnowledgeHub release-gate tests were run against the newer
candidate snapshot with an isolated workspace-source binding (no KH source or
installed GM environment was modified):

```text
apps/project_management/tests/integration/test_benchmark_project_hours_summary.py::TestProjectHoursSummaryBenchmark::test_main_measures_exactly_the_transactional_fixture  FAILED
apps/user/tests/integration/test_user_daily_events.py::TestDailyEvent::test_year_query_loads_event_sources_once_per_month  FAILED
```

Both fail with `Query field authorization candidate limit exceeded.` The
project failure occurs at `projectStaffCostList` while measuring the original
1,000-booking fixture; the calendar failure occurs at `dailyEventList` for the
year query. Logs are in
`/tmp/kh-pr10933-current-candidate/gates/project/run.log` and
`gates/calendar/run.log`.

The reusable consumer harness was also run for `TestCalendar` and
`TestProject`. On that newer snapshot all six calendar cases failed at the
same authorization-limit probe. For the project cases, `raw-all-1000` failed
with the same limit, `summary-on-1000` and `anonymous-summary-list` passed
with five cold and five warm samples, and `summary-list-on-1000` failed with
`projectHoursSummaryList requires an exact project filter`. This confirms the
missing declaration/limit issue affects the original calendar and raw project
paths; it is not a harness setup failure. JSON diagnostics are under
`/tmp/kh-pr10933-current-candidate/consumer-harness/`.

## Candidate synthetic 1,000-row diagnostic

On the stable post-gate candidate snapshot, the required synthetic 1,000-row
run retained the normal scan limit and recorded the expected failure: seven
repetitions after one warmup, median 770.927 ms, min 758.912 ms, max 793.128
ms, and 100 SQL statements per request. The error was
`Exact search total cannot be established within the server scan limit; narrow
the query. Partial search pages cannot safely exclude unreadable content.`
The report is `/tmp/kh-pr10933-current-candidate/synthetic-1000.json`.

The separately labelled raised-total-limit diagnostic (`GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT=1001`)
returned the full functional payload on all seven repetitions: median 768.595
ms, min 758.131 ms, max 793.090 ms, 100 SQL statements per request, equal
payload hashes, `total=1000`, and `totalIsExact=true`. It does not alter the
field-decision ceiling and is not a primary comparison result. Its report is
`/tmp/kh-pr10933-current-candidate/synthetic-1000-raised-total.json`.

## Final three-variant timing command and receipt

Earlier interim timings remain provisional because of candidate source edits
and concurrent full-suite CPU load. The following command was executed after
the full suite became idle, in balanced order **A-B-C-C-B-A**:

- A = immutable GM 0.86.4 venv (`/tmp/kh-pr10933/gm0864-performance-venv/bin/python`).
- B = immutable GM 906 source archive
  `/tmp/kh-pr10933/gm906cd0b-source/TimKleindick-general_manager-906cd0b/src`
  with the fixed-906 venv.
- C = current candidate workspace `src`, with the fixed-906 venv.

The source bindings and run outputs were captured under
`/tmp/kh-pr10933-final-three-variant/`; each round uses fresh SQLite test
databases, the canonical lazy actor, five cold and five warm samples per
successful case, and no setup/indexing in timing. The exact command template is:

```bash
KH_BACKEND=/Users/tim/Documents/Codex/2026-11-06/task/knowledge_hub-pr10933-gm582/backend
KH_HARNESS=/tmp/kh-pr10933/kh-performance-comparison/harness
GM906_SRC=/tmp/kh-pr10933/gm906cd0b-source/TimKleindick-general_manager-906cd0b/src
GM_CURRENT_SRC=/Users/tim/.codex/worktrees/c99b/general_manager/src
GM0864_PYTHON=/tmp/kh-pr10933/gm0864-performance-venv/bin/python
GM906_PYTHON=/tmp/kh-pr10933/gm906cd0b-performance-venv/bin/python
OUT=/tmp/kh-pr10933-final-three-variant
mkdir -p "$OUT"/baseline-a "$OUT"/fix906-b "$OUT"/candidate-c "$OUT"/candidate-c2 "$OUT"/fix906-b2 "$OUT"/baseline-a2
export PYTHONHASHSEED=0 TZ=UTC DJANGO_SETTINGS_MODULE=tests.pytest_settings
export LOG_TO_STDOUT=1 PROFILE_SERVER_IP_DEV=127.0.0.1
export PROFILE_DOMAIN_DEV=profile.test.invalid CERTIFICATE_NAME=localhost

# A-B-C-C-B-A; change only output/variant/round labels between runs.
PYTHONPATH="$KH_BACKEND:$KH_HARNESS" KH_PERF_OUTPUT_DIR="$OUT/baseline-a" KH_PERF_VARIANT=gm0864-baseline KH_PERF_ROUND=baseline-a "$GM0864_PYTHON" -m pytest -c "$KH_BACKEND/pytest.ini" -p no:cacheprovider -p canonical_request_actor "$KH_HARNESS/test_kh_performance.py::TestRichSearch" "$KH_HARNESS/test_kh_performance.py::TestSparseSearch" -q
PYTHONPATH="$GM906_SRC:$KH_BACKEND:$KH_HARNESS" KH_PERF_OUTPUT_DIR="$OUT/fix906-b" KH_PERF_VARIANT=gm906-fixed KH_PERF_ROUND=fix906-b "$GM906_PYTHON" -m pytest -c "$KH_BACKEND/pytest.ini" -p no:cacheprovider -p canonical_request_actor "$KH_HARNESS/test_kh_performance.py::TestRichSearch" "$KH_HARNESS/test_kh_performance.py::TestSparseSearch" -q
PYTHONPATH="$GM_CURRENT_SRC:$KH_BACKEND:$KH_HARNESS" KH_PERF_OUTPUT_DIR="$OUT/candidate-c" KH_PERF_VARIANT=gm-current-candidate KH_PERF_ROUND=candidate-c "$GM906_PYTHON" -m pytest -c "$KH_BACKEND/pytest.ini" -p no:cacheprovider -p canonical_request_actor "$KH_HARNESS/test_kh_performance.py::TestRichSearch" "$KH_HARNESS/test_kh_performance.py::TestSparseSearch" -q
PYTHONPATH="$GM_CURRENT_SRC:$KH_BACKEND:$KH_HARNESS" KH_PERF_OUTPUT_DIR="$OUT/candidate-c2" KH_PERF_VARIANT=gm-current-candidate KH_PERF_ROUND=candidate-c2 "$GM906_PYTHON" -m pytest -c "$KH_BACKEND/pytest.ini" -p no:cacheprovider -p canonical_request_actor "$KH_HARNESS/test_kh_performance.py::TestRichSearch" "$KH_HARNESS/test_kh_performance.py::TestSparseSearch" -q
PYTHONPATH="$GM906_SRC:$KH_BACKEND:$KH_HARNESS" KH_PERF_OUTPUT_DIR="$OUT/fix906-b2" KH_PERF_VARIANT=gm906-fixed KH_PERF_ROUND=fix906-b2 "$GM906_PYTHON" -m pytest -c "$KH_BACKEND/pytest.ini" -p no:cacheprovider -p canonical_request_actor "$KH_HARNESS/test_kh_performance.py::TestRichSearch" "$KH_HARNESS/test_kh_performance.py::TestSparseSearch" -q
PYTHONPATH="$KH_BACKEND:$KH_HARNESS" KH_PERF_OUTPUT_DIR="$OUT/baseline-a2" KH_PERF_VARIANT=gm0864-baseline KH_PERF_ROUND=baseline-a2 "$GM0864_PYTHON" -m pytest -c "$KH_BACKEND/pytest.ini" -p no:cacheprovider -p canonical_request_actor "$KH_HARNESS/test_kh_performance.py::TestRichSearch" "$KH_HARNESS/test_kh_performance.py::TestSparseSearch" -q
```

Before and after the six commands, fingerprint every candidate `src/**/*.py`
and `pyproject.toml`; abort the final claim if the candidate manifest changes.
Report all three source paths, package/distribution versions, fixture/request/
response/result hashes, median/IQR/min/max and SQL counts. The immutable
origin/main gate result below is correctness-only and is not a timing variant.

## Immutable origin/main gate control

To separate candidate-introduced behavior from the known base behavior, the
same two original tests were run with `PYTHONPATH` bound first to the immutable
`/tmp/gm-search-routing-benchmarks/main715c942f/src` archive at `715c942f`.
The source preflight reported:

```text
python=/tmp/kh-pr10933/gm906cd0b-performance-venv/bin/python
general_manager=/tmp/gm-search-routing-benchmarks/main715c942f/src/general_manager/__init__.py
graphql_search=/tmp/gm-search-routing-benchmarks/main715c942f/src/general_manager/api/graphql_search.py
distribution=0.87.5
```

Both controls failed identically to the current candidate:

```text
apps/project_management/tests/integration/test_benchmark_project_hours_summary.py::TestProjectHoursSummaryBenchmark::test_main_measures_exactly_the_transactional_fixture  FAILED
apps/user/tests/integration/test_user_daily_events.py::TestDailyEvent::test_year_query_loads_event_sources_once_per_month  FAILED
```

Each error was `Query field authorization candidate limit exceeded.` at
`projectStaffCostList` and `dailyEventList`, respectively. This correctness
control shows those two failures pre-date the candidate source; it does not
establish equivalence for other queries or authorize release. Logs are retained
at `/tmp/kh-pr10933-origin-main-gates/project/run.log` and
`/tmp/kh-pr10933-origin-main-gates/calendar/run.log`.
