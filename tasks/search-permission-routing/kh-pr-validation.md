# KnowledgeHub PR validation

Checked on 2026-11-08 against [KH PR #10933](https://constellium.ghe.com/constellium/knowledge_hub/pull/10933),
published HEAD `3dd8149665c4d8413713260e6c1f18a33135a44b`.
**KH is not green with this GM candidate.** Search passes with the existing
local consumer changes, but the published consumer lacks complete projection
dependencies. The original booking and yearly-calendar gates also fail.

## Source and execution boundary

- The clean run used an archive of the published KH HEAD, excluding all local
  modifications. The archive command was `git archive --format=tar HEAD`, run
  from the KH backend directory, and extracted into
  `/tmp/kh-pr10933-new-gm-check/head`.
- A separate control used the existing modified KH backend at
  `/Users/tim/Documents/Codex/2026-11-06/task/knowledge_hub-pr10933-gm582/backend`.
  Its local changes were preserved. Its results do not represent the published
  PR or attribute the improvement to one particular local change.
- Both runs loaded GM from this branch's `src` via `PYTHONPATH`. An import
  check confirmed that source path. The installed distribution metadata still
  said `0.87.5`; it was not used as evidence of the executed source version.
- The GM source manifest digest was
  `a5d00911c9bd1417e0c299f2f995a32ee5d967a6f33bf139018a1f5623a64867`,
  matching the [final SQL comparison](sql-optimization.md).
- Interpreter: `/tmp/kh-pr10933/gm906cd0b-performance-venv/bin/python`,
  Python 3.14.10, Django 6.1.3, SQLite in-memory test database.
- These were targeted local checks with the new source. Neither KH dependency
  pins nor its remote branch were changed. Hosted CI still tests its existing
  `GeneralManager==0.87.5` pin, not this candidate.

## Results

| Gate | Published KH HEAD | Existing local KH changes |
| --- | --- | --- |
| WorkCenter search module | Failed: incomplete `field_dependencies`; pytest reports 77 failed subtest cases and 5 passed tests | 9 passed, 74 subtests passed |
| Original 1,000-booking test | Failed: `pageSize must not exceed 100` at `projectStaffCostList` | Failed: `Query field authorization candidate limit exceeded.` at `projectStaffCostList` |
| Original yearly-calendar test | Failed: `Query field authorization candidate limit exceeded.` at `dailyEventList` | Same failure |

Each failing invocation exited with code 1; the local search invocation exited
with code 0. The 77 search failures are parametrized `unittest.subTest` cases,
not 77 separate test modules. Search requires complete dependency declarations
under the documented new projection contract. The page-size and authorization
budget failures are separate existing query constraints; this search PR does
not relax either constraint or include the calculation constructor-scoping
changes from GM PR #582.

The remote `backend-tests` check and its required aggregate were also red at
the checked SHA. The other reported checks passed. The
[failing hosted job](https://constellium.ghe.com/constellium/knowledge_hub/actions/runs/389546577/job/1581742532)
predates this local validation and cannot establish the new candidate's hosted
status. No full KH-suite pass is claimed.

## Reproduction

Run the following from each backend directory: the clean archive and the
existing local backend. The latter control depends on its preserved local
changes and is not reproducible from the published SHA alone.

```sh
export PYTHONHASHSEED=0 TZ=UTC DJANGO_SETTINGS_MODULE=tests.pytest_settings
export LOG_TO_STDOUT=0 PROFILE_SERVER_IP_DEV=127.0.0.1
export PROFILE_DOMAIN_DEV=profile.test.invalid CERTIFICATE_NAME=localhost
kh_backend="$PWD"
gm_source=/Users/tim/.codex/worktrees/c99b/general_manager/src
kh_python=/tmp/kh-pr10933/gm906cd0b-performance-venv/bin/python
export PYTHONPATH="$gm_source:$kh_backend"

"$kh_python" -m pytest -c "$kh_backend/pytest.ini" -p no:cacheprovider \
  apps/plant/tests/integration/test_work_center_search_graphql.py \
  -q --tb=short -o addopts=--import-mode=importlib

"$kh_python" -m pytest -c "$kh_backend/pytest.ini" -p no:cacheprovider \
  apps/project_management/tests/integration/test_benchmark_project_hours_summary.py::TestProjectHoursSummaryBenchmark::test_main_measures_exactly_the_transactional_fixture \
  -q --tb=short -o addopts=--import-mode=importlib

"$kh_python" -m pytest -c "$kh_backend/pytest.ini" -p no:cacheprovider \
  apps/user/tests/integration/test_user_daily_events.py::TestDailyEvent::test_year_query_loads_event_sources_once_per_month \
  -q --tb=short -o addopts=--import-mode=importlib
```

The temporary exact command receipt is
`/tmp/kh-pr10933-new-gm-check/receipt.json`. Individual logs use
`search-clean.log`, `project-clean.log`, `calendar-clean.log`, and the corresponding
`*-dirty-escalated.log` names in that directory. This report preserves the
source binding, commands, results and limitations independently of those
temporary files.
