# Search SQL optimization follow-up

## Cause and change

The four SQL statements in the canonical 39-source WorkCenter request were:

| Statement | Responsibility |
| --- | --- |
| 1. User lookup | Resolve the canonical lazy request actor. |
| 2. WorkCenter candidate SELECT | Hydrate native source members matching backend candidate IDs. |
| 3. ProcessChain SELECT | Authorize the candidate's reverse-FK collection. |
| 4. Supplier SELECT through the membership table | Authorize the candidate's M2M collection. |

The last two queries were needed to establish that both collections were empty;
they were not duplicate hydration or index-freshness reads. The existing request
cache already shared their loaded values with later accesses.

The owning DatabaseBucket/ORM descriptor path now adds correlated EXISTS to the
candidate SELECT for supported first-hop collection relations. A false result
lets that exact native accessor return its normal filtered bucket with
`.none()`, retaining dependency tracking. The database still checks the relation
tables, but no separate relation SELECT is sent for proven empty collections.
Positive or unproven results retain the existing loading and authorization.
Custom query implementations, historical/cross-database relations, and through
relations targeting a non-PK source field do not use this shortcut.

Already validated projection paths and index definitions are reused from schema
construction. Detached snapshots detect in-place list changes; a changed search
configuration cannot reuse the old fast approval. Runtime object, field and
relationship permission checks, native source membership, stored-key coverage,
scan limits, count, ordering and pagination behavior remain enforced.

Per the user's clarification, search no longer reserializes source rows to check
index-value freshness. The separate indexing/invalidation pipeline owns that
responsibility, including removing obsolete content from former relation targets.
The measured change includes this removal and static definition reuse as well as
the SQL optimization; the timing gain is not attributed solely to fewer SQL calls.

## Final paired KnowledgeHub measurement

All six rounds passed both original rich/sparse search tests. Fixture, wire
request, response and result hashes are equal across every variant and sample
for all three cases. The canonical lazy actor and request calculation context
were retained. No test suite ran concurrently with the final comparison.

Times below are milliseconds, shown as median [inclusive IQR], min–max.
“Previous candidate” is the preserved source from the start of this follow-up,
remeasured in the same comparison; its timing need not match the earlier report.
SQL is statements per request, including the lazy user lookup.

| Case | Variant | Cache cold | Warm | SQL |
| --- | --- | ---: | ---: | ---: |
| 39-visible-work-centers | GM 0.86.4 | 1.778 [0.299], 1.481–2.091 | 1.724 [0.388], 1.420–2.121 | 2 |
| 39-visible-work-centers | Previous candidate (4 SQL) | 4.223 [0.275], 3.922–4.437 | 4.142 [0.292], 3.828–5.240 | 4 |
| 39-visible-work-centers | Optimized candidate | 3.529 [0.335], 3.268–4.764 | 3.487 [0.282], 3.182–3.645 | 2 |
| rich-search | GM 0.86.4 | 5.942 [1.246], 5.834–8.567 | 6.035 [0.108], 5.740–6.330 | 13 |
| rich-search | Previous candidate (4 SQL) | 8.770 [0.039], 8.650–8.888 | 8.744 [0.139], 8.489–10.381 | 13 |
| rich-search | Optimized candidate | 8.285 [0.041], 8.065–9.457 | 8.330 [0.156], 8.046–8.783 | 12 |
| anonymous-search | GM 0.86.4 | 1.957 [0.056], 1.761–2.030 | 1.921 [0.071], 1.764–3.348 | 0 |
| anonymous-search | Previous candidate (4 SQL) | 1.961 [0.052], 1.750–2.060 | 1.975 [0.048], 1.751–2.007 | 0 |
| anonymous-search | Optimized candidate | 2.010 [0.120], 1.844–2.104 | 1.999 [0.068], 1.765–3.232 | 0 |

The sparse warm median improves by **15.8%**, with **4 → 2 SQL statements**.
It still takes **2.02×** the GM 0.86.4 median; SQL-count parity is not latency
parity. The rich warm median improves by **4.7%**, with **13 → 12 SQL**.
The small anonymous-case difference lies within the observed sample dispersion.

## Measurement limits and reproduction

Order: GM 0.86.4 → previous candidate → optimized candidate → optimized candidate
→ previous candidate → GM 0.86.4. Each variant has two fresh processes and ten
samples per case/mode, with five samples per process. Each warm sequence has an
untimed warmup. Fixture creation and indexing are outside the timed boundary.

Cache cold means clearing configured Django cache aliases before each sample;
Python, schema, OS and SQLite are already initialized. Warm clears aliases once
before warmup. IQR and range describe observed dispersion, not a statistical
confidence interval. This is a diagnostic in-process comparison, not production
latency evidence.

The shared stack is Python 3.14.10, Django 6.1.3, SQLite 3.53.1 in-memory databases,
UTC and PYTHONHASHSEED=0. The supported filelock versions differ: 3.24.3 for
GM 0.86.4 versus 4.2.9 for the previous/optimized candidate. This compares those
supported stacks. HTTP/session middleware, browser, network, PostgreSQL,
production search backends and concurrent load are excluded.

[`sql-optimization-comparison.json`](sql-optimization-comparison.json) preserves
all samples, hashes, SQL traces with call sites, effective interpreter/package
bindings, exact command arrays, working directory and task environment. The raw
harness output and pre-optimization source snapshot remain temporary under
`/tmp/kh-search-sql-optimization/`. The supplied KH harness is also temporary at
`/tmp/kh-pr10933/kh-performance-comparison/harness/`.

The optimized source manifest was equal before and after both timing rounds:
`a5d00911c9bd1417e0c299f2f995a32ee5d967a6f33bf139018a1f5623a64867`.
The installed candidate distribution metadata is 0.87.5; recorded import paths
and source fingerprints identify the actual working-tree code.

## Verification and remaining release gates

- Broad affected selection: 812 passed, 17 skipped, 186 subtests passed.
- Final static-reuse/configuration-mutation selection: 94 passed, 9 subtests passed.
- The one-query empty-collection regression fails on the preserved previous
  source and passes on the optimized source.
- Non-PK through membership and denied-target checks retain the ordinary path.
- Ruff, strict mypy (361 sources), all pre-commit hooks and strict documentation
  build passed. Independent scoped review has no remaining blocking findings.

The separate original 1,000-booking, calendar and project release gates were not
rerun for this follow-up. Their previously documented origin/main failures remain
open; the search measurements do not clear them. See
[`validation.md`](validation.md) for the complete verification record.
