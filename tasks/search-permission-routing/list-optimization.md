# Native list CPU and hydration optimization

This report describes the earlier `d05ce4e8` checkpoint. The subsequent
[query-structure preparation](query-structure-optimization.md) contains the
current comparison and its explicit per-call static-definition contract.

This follow-up addresses the raw 1,000-booking regression reported for
PR #583 at `4f220906f186c57d8078b7f6ffd1d318cdaa63a9`. Search, raw bookings,
summaries and the original complete-year calendar remain separate gates.

## Cause and implementation

The original diagnostic reproduced identical response and result hashes at
259.901 ms for GM 0.86.4 and 2,169.255 ms for the prior candidate. These are
single diagnostic observations, not the final repeated comparison.

Across ten requests, the prior candidate performed 50,000 operand visits,
60,000 additional-rule analyses and 68,000 runtime field checks. GM 0.86.4
performed 8,000 runtime field checks. Much of the added CPU work scanned the
entire permission MRO and inspected descriptors for every operand and row.

- Permission instances now keep shallow declaration snapshots and resolve only
  the requested field. Dictionary-only MRO precedence and the previous lifetime
  are preserved: replacing a class declaration affects the next permission
  instance, while edits within a rule dictionary remain live.
- Additional-rule analysis looks up the requested declaration directly. Exact
  built-in scalar/container types and ABC bookkeeping do not need repeated
  dynamic descriptor discovery. Arbitrary values and custom metaclasses retain
  live inspection. No permission metaclass or global decision cache was added.
- Query operand ordering is prepared once per authorization call. Every original
  runtime operand check still runs, including repeated fields, nonmatching rows,
  off-page rows, related objects and repeated candidate occurrences.
- Paginated native lists use the bucket owner's count-only path. Exact existing
  snapshots are reusable; otherwise SQL `COUNT` replaces a full filtered-row
  materialization. Ordinary counts, unpaged lists, custom count/length behavior,
  historical checks, dependencies and cache invalidation keep their contracts.

The extra materialization came from the sequence source authorization → native
filter/sort → `len()` → page slice. The source and filtered result have different
query signatures, so `len()` previously loaded another 1,000 rows per request.
The new count path removes those 10,000 redundant ORM hydrations over ten pages.
It does not eliminate the count statement or reuse rows across unrelated query
signatures.

## Authorization boundaries

No actor/object result is saved in structural metadata. Existing permission
constructors and field decisions remain request-time work. Field definitions
being equivalent to row definitions does not prove callback purity: registered
callbacks may deny or raise on a subsequent evaluation. Consequently repeated
scalar checks are deliberately retained. Regression tests cover both a later
denial and a later exception, as well as class/mixin replacement, in-place rules,
custom metaclasses, dynamic descriptors, actor changes and pagination behavior.

Interface metadata also remains live. Replaceable capabilities and manager
properties do not have an immutable-schema revision contract that would justify
reusing their results across candidate visits.

## Verification and repeated comparison

The repeated comparison uses the unchanged supplied KH harness in
A–B–C–C–B–A order: GM 0.86.4, the prior PR source at `4f220906`, and the
optimized source. Each variant runs in two fresh processes, with ten cold and
ten warm samples per case. No other test suite ran during timing. Fixture,
request, response and result hashes match across every sample of all 13 cases.

Warm medians in milliseconds, inclusive IQR in brackets:

| Complete flow | GM 0.86.4 | Prior PR | Optimized | SQL: baseline / prior / optimized |
| --- | ---: | ---: | ---: | --- |
| 39-visible-work-centers | 1.470 [0.187] | 3.353 [0.288] | 3.315 [0.228] | 2 / 2 / 2 |
| rich-search | 5.587 [0.188] | 8.055 [0.073] | 7.914 [0.065] | 13 / 12 / 12 |
| anonymous-search | 1.865 [0.082] | 1.926 [0.147] | 1.934 [0.018] | 0 / 0 / 0 |
| bounded-month | 15.107 [0.116] | 22.777 [0.162] | 19.150 [0.173] | 7 / 7 / 7 |
| legacy-year-events | 59.944 [0.627] | 144.520 [0.662] | 101.484 [0.747] | 29 / 29 / 29 |
| legacy-year-all-365 | 106.729 [1.393] | 197.452 [0.813] | 156.404 [0.657] | 29 / 29 / 29 |
| full-year-month-windows-events | 154.462 [1.517] | 244.401 [0.856] | 202.080 [0.903] | 73 / 73 / 73 |
| full-year-month-windows-all-365 | 202.565 [2.268] | 298.987 [1.604] | 257.356 [1.288] | 73 / 73 / 73 |
| private-outsider-month | 19.225 [0.302] | 21.462 [0.089] | 21.244 [0.089] | 43 / 48 / 48 |
| raw-all-1000 | 283.970 [2.911] | 2339.785 [6.268] | 1102.634 [11.681] | 160 / 170 / 170 |
| summary-on-1000 | 27.333 [0.530] | 28.541 [0.422] | 28.720 [0.567] | 4 / 4 / 4 |
| summary-list-on-1000 | 29.592 [0.274] | 31.287 [0.133] | 31.515 [0.558] | 6 / 6 / 6 |
| anonymous-summary-list | 3.384 [0.254] | 1.452 [0.194] | 1.463 [0.254] | 2 / 0 / 0 |

**Raw bookings improve from 2,339.785 to 1,102.634 ms: 52.9% less elapsed time.**
The remaining gap to GM 0.86.4 is 3.88×, down from 8.24× in the same comparison.
The optimized warm range is 1096.071–1112.823 ms.
This is an improvement, **not performance parity**. The simple search retains
its two-statement fast path; its small timing difference is within observed
dispersion. The complete 365-day calendar improves from 197.452 to 156.404 ms.

The final diagnostic still records all 50,000 operand visits, 60,000 local-rule
analyses and 68,000 runtime field checks. ORM materializations fall from 21,140
to 11,140, matching the baseline materialization count for this fixture. SQL
remains 170 versus baseline 160: each request still authorizes the full source
and then counts the native filtered result. Those different query domains cannot
safely share a cardinality solely because this fixture happens to match every row.
GM 0.86.4 performs only 8,000 runtime field checks in this flow. The retained
authorization work is the main remaining gap; no callback decision is skipped
or reused to improve the timing.

Local verification of the optimized production source:

- GM full suite: 16,557 passed, 130 skipped, 2,585 subtests; four tests were
  blocked only by sandbox restrictions on local loopback sockets and Docker.
  All four passed unchanged with the required local access. Combined coverage:
  16,561 passed; this is not presented as a single clean initial command.
- Ruff, formatting, strict mypy (355 source files), all pre-commit hooks and
  `mkdocs build --strict`: passed.
- KH full backend: 7,219 passed, 8 skipped, 1,279 subtests.
- KH PostgreSQL contracts: 20 passed, 7,227 deselected.
- Original KH 1,000-booking and complete-calendar gates: 3 passed, 4 subtests,
  with the original queries, fixtures and assertions.
- Independent scoped review: no remaining blocking findings.

The first hosted run found a Django 6.1 incompatibility in the new hydration
test observer: a bare mock of `from_db` lacked the `__func__` attribute Django
inspects. The observer now preserves the real classmethod protocol. Production
source and benchmark inputs are unchanged. The same seven hydration tests pass
on Django 5.2 and 6.1; all 139 focused tests pass on Django 6.1. The repaired
observer still fails against the prior production source with 25 versus the
expected 15 hydrations, so the regression assertion was not weakened.

[Per-sample comparison evidence](list-optimization-comparison.json) records
commands, source bindings, equality hashes, cold/warm samples and dispersion.
[Verification evidence](list-optimization-verification.json) binds the test
results and diagnostic call counts to the same source as the comparison.

All timed variants use Python 3.14.10, Django 6.1.3, SQLite 3.53.1, UTC and
`PYTHONHASHSEED=0`. Supported filelock versions differ: 3.24.3 for GM 0.86.4
and 4.2.9 for both newer variants. Linux KH validation uses the same previously
verified image (Python 3.14.7, Django 6.1.3); PostgreSQL is 17.11.

The benchmark boundary includes backend execution and JSON encode/decode. It
excludes HTTP/session middleware, browser, network, production search services
and production-database latency. Cold clears Django caches; Python, schema, OS
and SQLite are already initialized. Warm has an untimed warmup; each request has
a fresh calculation context. IQR and min/max describe observed dispersion, not
a confidence interval.

No merge, release or manual version change is part of this follow-up. KH source
validation does not establish hosted green checks against a newly published GM
version; its dependency adoption remains a separate step.
