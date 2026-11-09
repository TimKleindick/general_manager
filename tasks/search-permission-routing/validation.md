# Search permission routing validation

This records the initial search implementation. Subsequent KH compatibility
changes, restored omission semantics and final release-gate results are tracked
in [KH compatibility validation](kh-compatibility-validation.md).

Base: `origin/main` at `715c942f2a94a5b2cf1435dd06b71228cb730448`.
Working branch: `tk/search-permission-routing`. Related performance issue: #304.
No version number, release or production data was changed.

## Implementation boundary

- Schema construction stores a static classification for each manager/index.
  It never stores a user's or an object's permission result.
- Eligible database searches restrict the existing native bucket to backend
  candidate primary keys and retain its hydration and runtime row authorization.
- Native source-path authorization, relation aliases, and the complete
  `field_dependencies` contract reuse the search-specific implementation from
  reference fix `906cd0be89b8ee9172ee7e83c875c3d3c4a25eca`. Unrelated calculation
  resolver changes from that branch are not included.
- Extra/unknown field policies retain full source authorization. Unknown native
  property/descriptor dependencies fail before querying the backend.
- Related-row denial on the fast path removes the entire dependent projection.
  The thorough path retains its permission error. Invalid relation data still
  raises an error on both paths.
- Every hit needs complete stored data with exactly the configured fields.
  Following the user's clarification, value freshness belongs to separate index
  maintenance; search no longer reserializes documents. That pipeline must
  remove obsolete content from former relation targets before serving it.
- Native first-hop collection emptiness can be established with correlated
  EXISTS in the candidate SELECT. Only proven empty relations avoid subsequent
  loading; their native buckets retain filters and dependency tracking.
- An incomplete backend scan raises `SEARCH_SCAN_LIMIT_EXCEEDED` in both total
  modes. Returning a bounded raw prefix could expose hidden content through
  counts and page boundaries. These compatibility changes are documented in
  the search and GraphQL security guides.

## Executed checks

Local environment: Python 3.12.4, Django 5.2.17, SQLite; repository development
requirements and the editable package installed into `.venv`.

| Check | Result |
| --- | --- |
| Full `python -m pytest` run during implementation | 16,458 passed, 130 skipped; 24 failures subsequently resolved/rechecked |
| Explicit rerun of every failed test family | 33 passed, 17 skipped, 156 subtests passed |
| Search, identity, configuration, indexer, and permission integration selection | 216 passed, 18 skipped, 136 subtests passed |
| Final permission/source-proof repairs | 147 passed, 18 subtests passed |
| Native descriptor and per-index proof regression selection | 107 passed, 6 subtests passed |
| Final dependent-value transport and authorization selection | 45 passed, 6 skipped, 8 subtests passed |
| Ruff lint and formatting | Passed; 952 files formatted |
| Strict mypy | Passed; 360 source files |
| `pre-commit run --all-files` | Passed |
| `mkdocs build --strict` | Passed |
| Independent security review of repaired search paths | No remaining blocking findings |

The full run started before the final search fixes and fixture updates. Its
search failures were superseded by the explicit green reruns above; successful
checks for unchanged code were reused. Two loopback tests and two synthetic
Docker-context tests initially failed because of sandbox restrictions and
passed when rerun with the required local access. The final complete run is
recorded below. Optional external-provider tests remain skipped when their
services are not configured.

Temporary command logs use `/tmp/gm-search-routing-*.log`. The durable KH
measurement and acceptance results are in [benchmark-results.md](benchmark-results.md).

## Follow-up SQL optimization

The user's follow-up removes the runtime index-value freshness comparison;
separate index maintenance owns that guarantee. Stored key coverage, native
membership and all runtime object, field and relation checks remain enforced.

- Canonical SQL tracing identified the two extra reads as the empty WorkCenter
  process-chain and supplier relations. Neither was a duplicated read.
- First-hop native collection emptiness can be queried with EXISTS during
  candidate hydration. Populated, custom, historical, cross-database and
  non-PK through relations retain their existing loading behavior.
- Already validated projection paths are reused. Detached index-definition
  snapshots detect in-place edits; changed configurations lose fast approval.
- The focused search/ORM/history/performance selection passed: 812 tests,
  17 skips and 186 subtests. Final definition-reuse and mutation regressions
  passed: 94 tests and 9 subtests. Successful unchanged checks were reused.
- The new empty-collection query regression fails against the preserved
  pre-optimization source (3 relation/root queries instead of 1) and passes
  on the optimized source. The separate non-PK through test preserves both
  target membership and its permission denial.
- Independent scoped review found no remaining blockers after fixing non-PK
  through correlation and detached startup configuration snapshots.
- Final Ruff/formatting, strict mypy (361 sources), all pre-commit hooks and the
  strict documentation build passed. The balanced final KH comparison passed
  all six rounds with equal fixture/request/response/result hashes; the sparse
  case now uses 2 SQL statements and the rich case 12.

Follow-up command logs are under `/tmp/kh-search-sql-optimization/`. The final
comparison is documented in [sql-optimization.md](sql-optimization.md).

## Final pre-PR verification

The complete final GM suite passed on 2026-11-08: **16,478 passed, 130 skipped,
2 warnings in 741.13 seconds** (exit code 0). Command:

```sh
.venv/bin/python -m pytest --junitxml=/tmp/gm-search-pr-full.xml
```

This run used Python 3.12.4 and Django 5.2.17 with the required local test access.
Its log is `/tmp/gm-search-pr-full.log`. No implementation or test files changed
after the run; subsequent edits only record validation results. The existing
Ruff/formatting, strict mypy, full pre-commit and documentation checks remain
valid for that source tree. The normal commit hooks also check the final report
files. External services absent from this environment remain outside the local
pass claim.

Targeted consumer checks against the exact published KH PR #10933 HEAD and the
existing local KH changes are recorded in
[kh-pr-validation.md](kh-pr-validation.md). The published KH consumer is not
green with this GM source. Search passes with the local consumer changes, but
the independent booking and calendar gates remain red. Hosted KH CI retains
its existing dependency pin and has not validated this candidate.

## Release acceptance

Search timing is measured separately from the original 1,000-booking,
calendar, and project gates. The original booking and yearly-calendar tests
still exceed the existing field-authorization budget on both this candidate
and unchanged `origin/main`. They remain release blockers; this search change
does not claim to repair those independent calculation-query paths.

Complete dependency declarations remain a trusted application assertion,
covering all branches of arbitrary Python properties and serializers. The
executed functional and KH measurements use DevSearch and SQLite; they do not
establish production service latency or equivalence for an untested search
backend's ranking algorithm.
