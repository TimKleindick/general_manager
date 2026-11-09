# KH compatibility validation

This follow-up restores the requested GM contract and validates the existing KH
consumer adaptations in an isolated candidate. It supersedes the open KH
blockers in the earlier [search report](kh-pr-validation.md). GM is based on
`origin/main` at `715c942f2a94a5b2cf1435dd06b71228cb730448`; the existing PR is
[#583](https://github.com/TimKleindick/general_manager/pull/583). PR #582 remains closed.

## Contracts and ownership

- Omitted `page` **and** `pageSize` return the complete local list. Supplying
  either keeps finite defaults, maximum-size validation and the existing invalid
  configuration errors. Incomplete remote responses cannot masquerade as complete
  unpaginated results. The original 20-day and 365-day KH tests remain unchanged.
- Native list operand authorization budgets additional, unknown, custom and
  delegated field rules. Every runtime field and related-row check still runs,
  including nonmatching and off-page candidates. Search and request sources keep
  strict accounting; search shares its counter across one resolver execution.
- Proven calculation constructor inputs bound the requested domain before
  permission constraints are intersected. Existing native owners handle exact
  snapshot predicates, including inactive rows, historical values, native database
  lookups and custom getters. Missing backing rows and unknown lookups fail closed.
- Search classification ignores Python 3.14 compiler annotation metadata and
  treats callable permission factories as unknown. The first same-name index
  declaration is used consistently; an unconfigured index is not searched.
- FK identity authorization checks both the public `id` alias and the actual
  primary-key field. Terminal native relation fallbacks require every serialized
  identity leaf. Untyped containers remain unproven: no broad container allowance
  was added that could hide related-manager dependencies.
- Empty-relation proofs expire with their run or the existing data-change
  invalidation. Only cached values produced by those proofs are evicted; ordinary
  cached values, including `None`, retain their behavior.
- Backend exhaustion is explicit where the provider can truncate results.
  Meilisearch caps, cutoff/timing evidence, raw overdelivery and page-coordinate
  mismatches cannot produce an apparently exact authorized result. Pagination and
  ranking checks share one settings read per backend call. A local Meilisearch
  v1.45 probe confirmed its offset/limit echo behavior.
- Declared search dependencies contribute canonically to schema fingerprints;
  undeclared native indexes retain their previous hashes. Existing reconciliation
  owns reindexing. **No runtime index-content freshness check was added.**

The Meilisearch guards follow its documented [pagination cap](https://www.meilisearch.com/docs/capabilities/full_text_search/how_to/paginate_search_results)
and [search cutoff](https://www.meilisearch.com/docs/capabilities/full_text_search/how_to/configure_search_cutoff).
Custom adapters retain their existing short-page contract when
`SearchResult.exhausted` is omitted; they must reject truncated intermediate pages.

## Functional and quality gates

| Gate | Result |
| --- | --- |
| Full GM suite | 16,543 passed, 130 skipped, 2 warnings; exit 0 (739.58 s) |
| Restored omission regressions | 162 passed, 46 subtests |
| KH full backend, Linux/SQLite | 7,219 passed, 8 skipped, 1,279 subtests; no failures/errors |
| KH PostgreSQL contracts | 20 passed, 7,227 deselected; no failures/errors |
| Original 1,000-booking and 20/365-day calendar tests | 3 passed, 4 subtests; original assertions preserved |
| GM Ruff, formatting, strict mypy, pre-commit, strict docs build | Passed |
| KH backend Ruff, formatting, mypy | Passed; 885 typed source files |
| Independent scoped reviews | No remaining blocking findings |

The first final-source GM run had 16,540 passes and three expectations for the
superseded finite-pagination default. Only those expectations were corrected;
the production source was unchanged, and both affected modules then passed.
The full final rerun is recorded separately above. Earlier macOS KH diagnostics
were not release evidence; Linux with complete Git history resolves their
platform/archive failures.

A subsequent hosted run exposed four SQLite-specific expectations in two new
calculation tests on PostgreSQL and MariaDB. The test-only portability correction
compares both result identities and count with the active database's native ORM
exclusion, and uses an exact-case operand for the separate global-owner isolation
check. Production source and all KH/performance evidence remain unchanged.
The entire affected module passes on SQLite, PostgreSQL 18 and MariaDB 11.8:
24 tests and 9 subtests per backend. The isolated MariaDB check used PyMySQL;
the hosted matrix separately exercises mysqlclient.

KH uses published PR #10933 HEAD `3dd8149665c4d8413713260e6c1f18a33135a44b` plus
its existing local consumer changes: 97 tracked changed paths and 8 additional
source/test files, with no content differences in the isolated candidate.
The original dirty checkout was preserved. Linux validation used Python 3.14.7
and Django 6.1.3; PostgreSQL was 17.11 locally (the hosted workflow specifies
17.6). The GM source manifest was unchanged before/after all KH gates:
`ac20e4cc41e27d5f444958b4c7bcb7e361e07a951cb3983508adbdbd0014e99f`.
Installed GM metadata still says 0.87.5; the import path and source manifest,
not that metadata, identify the candidate.

[KH command/evidence receipt](kh-compatibility-evidence.json) records exact
commands, environments, original test names and artifact hashes. Temporary raw
logs remain in `/tmp/kh-gm583-followup/`. These are isolated candidate results;
no hosted KH result for a new published GM release is claimed.

## Balanced performance comparison

The unchanged supplied harness and the same KH source run in A–B–C–C–B–A order:
GM 0.86.4, the earlier fix `906cd0be89b8ee9172ee7e83c875c3d3c4a25eca`, and this
candidate. Each variant has two fresh processes and ten cold plus ten warm
samples per case. No other test suite ran concurrently. All 13 cases have equal
fixture, request, response and result hashes between 0.86.4 and this candidate,
verified across every timed sample. The earlier fix reproduces its three known
budget failures; an error is not a fast successful response.

Times below are warm medians in milliseconds with inclusive IQR in brackets.
The SQL column lists 0.86.4 / earlier fix / candidate statements per complete flow.

| Case | GM 0.86.4 | Earlier fix | Candidate | SQL |
| --- | ---: | ---: | ---: | --- |
| 39-visible-work-centers | 1.371 [0.201] | 22.119 [0.719] | 3.257 [0.264] | 2 / 42 / 2 |
| rich-search | 5.395 [0.294] | 10.937 [0.371] | 7.940 [0.063] | 13 / 20 / 12 |
| anonymous-search | 1.865 [0.111] | 1.896 [0.040] | 1.902 [0.145] | 0 / 0 / 0 |
| bounded-month | 15.110 [0.419] | 18.408 [0.396] | 22.490 [0.230] | 7 / 7 / 7 |
| legacy-year-events | 58.661 [2.434] | ERROR | 142.236 [1.328] | 29 / error / 29 |
| legacy-year-all-365 | 104.772 [3.188] | ERROR | 194.659 [2.689] | 29 / error / 29 |
| full-year-month-windows-events | 153.123 [4.526] | 192.291 [3.567] | 240.845 [2.696] | 73 / 73 / 73 |
| full-year-month-windows-all-365 | 201.189 [6.981] | 251.805 [4.539] | 294.099 [2.602] | 73 / 73 / 73 |
| private-outsider-month | 19.165 [0.770] | 20.876 [0.555] | 21.076 [0.235] | 43 / 48 / 48 |
| raw-all-1000 | 280.859 [8.919] | ERROR | 2328.126 [7.912] | 160 / error / 170 |
| summary-on-1000 | 26.921 [0.638] | 27.582 [0.566] | 28.821 [1.469] | 4 / 4 / 4 |
| summary-list-on-1000 | 29.420 [0.579] | 29.903 [0.594] | 31.036 [0.707] | 6 / 6 / 6 |
| anonymous-summary-list | 3.296 [0.177] | 1.395 [0.192] | 1.442 [0.204] | 2 / 0 / 0 |

The simple search still uses **two SQL statements**. Its median is about 2.4×
0.86.4 and substantially below the earlier fix; SQL parity is not runtime parity.
The previous [four-to-two-query audit](sql-optimization.md) remains a separate
measurement of that earlier optimization stage.

**The 1,000-booking raw flow remains a performance gap:** 2.328 seconds versus
0.281 seconds (about 8.3×), and 170 versus 160 SQL statements over ten pages.
It is functionally correct and passes the original test, but this is not a
performance-parity result. List operand checks still cover the authorized source
domain before each request's filters and page; no check was removed to improve
the measurement. The year, raw-booking and summary flows remain separate gates;
month-window alternatives do not replace the original single-year test.

[Comparison evidence](kh-compatibility-comparison.json) preserves every timing,
SQL count, payload size, source/harness binding, command, equality hash, min/max
and cold/warm aggregate. IQR/range describe observed dispersion, not a confidence
interval. Cold clears Django caches; Python, schema, OS and SQLite are initialized.
Warm has one untimed warmup and a fresh calculation context for every request.
Search uses the canonical lazy request actor, including its SQL lookup.

All variants use Python 3.14.10, Django 6.1.3, SQLite 3.53.1 in-memory data,
UTC and `PYTHONHASHSEED=0`. Supported filelock versions differ (3.24.3 for 0.86.4,
4.2.9 for both newer variants). The boundary includes backend execution and JSON
encode/decode, excluding HTTP/session middleware, the browser, network, production
search services and production-database latency.

## Publication boundaries

KH's dependency manifests still name the published GM 0.87.5. Publishing a new
GM release and adopting its actual version in KH are separate from these source
checks; no manual GM version bump was made.

The exact private frontend dependency `@constellium/know-ui-library@3.20.0` is
absent from the local cache. The current normal registry fetch with existing
configuration fails with HTTP 401. Credentials and permissions were unchanged,
and older package versions were not substituted. Full frontend validation and
the hosted KH result for the eventual new version remain unverified.
