# Benchmark bulk imports

`scripts/benchmark_bulk_throughput.py` compares public `create()` calls with
canonical `create_many()` and eligible SQL bulk creation. It uses PostgreSQL,
Django's Redis cache backend, and a Redis Channels layer. Run it with development
dependencies from the repository root.

## Isolate the services

Use a dedicated PostgreSQL instance and dedicated Redis instance. The script
creates and drops a Django test database and resets its configured Redis logical
databases between passes. The defaults are PostgreSQL on port 55497 and Redis
on port 56497, with cache DB 15 and Channels DB 14. Do not point this benchmark
at an application database or shared Redis instance.

```bash
docker run --rm -d --name gm-bulk-perf-postgres \
  -e POSTGRES_PASSWORD=general_manager -e POSTGRES_DB=general_manager \
  -p 55497:5432 postgres:18
docker run --rm -d --name gm-bulk-perf-redis -p 56497:6379 redis:7
```

The connection can be changed through the
`GENERAL_MANAGER_TEST_DATABASE_NAME`, `_USER`, `_PASSWORD`, `_HOST`, and `_PORT`
environment variables and the script's Redis URL options.

## Measure independent passes

```bash
python scripts/benchmark_bulk_throughput.py \
  --rows 1000 --batch-size 100 --existing-rows 100000 --repeats 3 \
  --require-optimized-bulk-sql \
  --output-prefix /tmp/gm-throughput-1000-b100
```

`--require-optimized-bulk-sql` verifies selection and actual bulk insertion before
measurement; a fallback must not be described as SQL bulk persistence. The
script alternates path order between repetitions and creates a fresh fixture
for each pass. It checks permissions, row/history counts and attribution,
uniqueness, workflow delivery, related search plans, cache freshness, and real
Redis notification delivery outside the measured operation.

The harness retains notifications until that verification drains them. Channel
capacity is `max(rows + 10, 100)` and message expiry defaults to 3,600 seconds,
configurable through `GENERAL_MANAGER_BENCHMARK_CHANNEL_EXPIRY`. Long imports
otherwise outlast Channels' ordinary 60-second expiry even with sufficient
capacity. This setting changes benchmark transport retention, not application
defaults; no notification assertions are removed.

Elapsed time is measured without cProfile or tracemalloc. Profile, SQL/Redis
operation count, and peak Python allocation passes are separate. Use
`--metrics elapsed` for a larger timing matrix; use `--metrics profile memory
sql_redis` for detailed diagnostics on a representative case. JSON reports and
cProfile files are written under the output prefix.

All three paths use the same unique scalar fields, shared foreign keys, local rule,
history metadata, and application refresh behavior. An application that adopts
batch refresh registration opts into coalescing refreshes; per-record workflows
and audit history remain required. External search indexing is replaced after
direct and related invalidation planning, so the measurements do not include a
search server's indexing latency. Peak Python allocations come from tracemalloc;
they exclude native allocations, process RSS, and database/Redis server memory.
No elapsed-time threshold is a test gate or throughput promise.

## Baseline profiling

The pre-optimization package was archived from the PR #507 implementation before
editing runtime code. Its final verification recording used Python 3.12.4,
Django 5.2.16, PostgreSQL 18.6 and Redis 7, with 100 imported rows, batch size 100,
1,000 existing rows and two alternating repetitions. Both paths retained real
permissions, history, workflows, related search planning and Redis notifications.

To reproduce the frozen comparison with the current measurement harness:

```bash
baseline_dir=$(mktemp -d)
git archive 438bf6d2 | tar -x -C "$baseline_dir"
PYTHONPATH="$baseline_dir/src" \
GENERAL_MANAGER_BENCHMARK_SOURCE_COMMIT=438bf6d2 \
GENERAL_MANAGER_BENCHMARK_CHANNEL_EXPIRY=60 \
python scripts/benchmark_bulk_throughput.py \
  --rows 100 --batch-size 100 --existing-rows 1000 --repeats 2 \
  --output-prefix /tmp/gm-throughput-frozen
```

The [raw frozen report](../assets/benchmarks/bulk-import-2026-09-15/frozen-baseline.json)
includes source digests and every metric repetition. The harness records both the
manager module digest, a digest of the entire loaded Python package, and a
separate fingerprint of the benchmark script and its settings. Append mode
refuses to combine different source trees, harness versions, or workloads;
older reports without a harness fingerprint cannot be extended by this version.
Redis endpoints in new reports omit user information and redact credential
query parameters.
In current-source reports, the legacy JSON labels `baseline_repeated` and
`baseline_create_many` mean repeated `create()` and disabled-SQL `create_many()`
on that same source tree. Only the frozen report loads the archived package.

| Frozen path | Mean unprofiled elapsed | SQL statements | Redis commands | Peak Python bytes |
| --- | ---: | ---: | ---: | ---: |
| Repeated `create()` | 1.452 s | 2,506 | 5,634 | 623,613 |
| Canonical `create_many()` | 1.216 s | 1,516 | 5,634 | 952,797 |

The separate first canonical cProfile pass took 1.908 seconds. It showed 100
per-record cache invalidations consuming 0.507 seconds cumulatively, 100 history
save operations consuming 0.516 seconds, and 200 history-reason updates consuming
0.345 seconds. These nested cumulative timings overlap and must not be added.
Reducing SQL alone would leave substantial cache and lifecycle overhead.

## Current-source diagnostics

For 1,000 imported rows in two 500-row batches against 100,000 existing rows,
three independent diagnostic repetitions produced these means:

| Path | SQL statements | Redis commands | Peak Python bytes | cProfile calls | Profiled elapsed |
| --- | ---: | ---: | ---: | ---: | ---: |
| Repeated `create()` | 25,006 | 56,034 | 1,739,021 | 38,033,943 | 24.690 s |
| Canonical `create_many()` | 15,026 | 56,034 | 3,426,690 | 30,667,836 | 20.045 s |
| Bulk SQL | 1,042 | 103 | 2,461,746 | 2,788,294 | 1.119 s |

SQL and Redis counts were identical across their three repetitions. Bulk SQL
used 95.8% fewer SQL statements and 99.8% fewer Redis commands than repeated
creation. Its peak Python allocation was 41.6% higher than repeated creation,
but 28.2% lower than canonical batching. Memory remains bounded by batch size
for independently committed imports; these numbers do not include server memory.

Every diagnostic fixture asserted 1,000 created rows, attributed history rows,
permission evaluations and workflow events. It also asserted that direct and
related search dispatch occurred. Canonical
paths performed 1,000 application refreshes and notifications; bulk SQL performed
two, each carrying its 500 identifiers. All notifications were received.

These profile timings are **not** throughput measurements. In the optimized
profiles, permission user resolution consumed roughly 0.6 seconds cumulatively;
per-record search resolution and observer publication remained smaller costs.
The separate unprofiled matrix below is the basis for speedup comparisons.

## Unprofiled timing matrix

All cases use 100,000 existing rows, the same supported workload and three
alternating repetitions on the final source tree. Elapsed means include the
create calls and their required dispatch, but exclude fixture setup and
post-run verification. Python ran on a macOS arm64 host with local Docker
PostgreSQL and Redis services.

| Imported rows | Batch size | Repeated `create()` | Canonical `create_many()` | Bulk SQL | Speedup over repeated |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 100 | 1.517 s | 1.246 s | 0.069 s | 21.9× |
| 1,000 | 100 | 14.488 s | 12.371 s | 0.631 s | 23.0× |
| 1,000 | 500 | 14.318 s | 12.178 s | 0.524 s | 27.3× |
| 1,000 | 1,000 | 14.663 s | 12.284 s | 0.528 s | 27.8× |
| 5,000 | 1,000 | 73.098 s | 61.576 s | 2.554 s | 28.6× |

The measured 21.9–28.6× gains exceed the 3× target for this supported workload.
They are not a guarantee for arbitrary managers, hooks or application workloads.

For the 1,000-row/500-batch case, repeated timings ranged from 14.122 to 14.659
seconds, canonical timings from 11.965 to 12.360 seconds, and SQL timings from
0.521 to 0.525 seconds. The 500- and 1,000-row batches performed similarly here;
this sample does not establish an optimal batch size.

Raw reports retain each repetition and its semantic assertions:
[100 rows / batch 100](../assets/benchmarks/bulk-import-2026-09-15/r100-b100.json),
[1,000 / 100](../assets/benchmarks/bulk-import-2026-09-15/r1000-b100.json),
[1,000 / 500, including diagnostics](../assets/benchmarks/bulk-import-2026-09-15/r1000-b500.json),
[1,000 / 1,000](../assets/benchmarks/bulk-import-2026-09-15/r1000-b1000.json),
[5,000 / 1,000](../assets/benchmarks/bulk-import-2026-09-15/r5000-b1000.json).
Detailed SQL, Redis, profile and memory passes cover the 1,000-row/500-batch
case; the other matrix cases measure elapsed time only.
The recordings up to 1,000 rows used the original 60-second notification expiry
and delivered every message. The 5,000-row case uses 3,600 seconds on all paths
because delivery verification happens after the import; its earlier expired-message
attempt was rejected and is excluded. Reports record capacity and expiry, and
append mode refuses to mix those settings.

## Interpretation and limits

The optimized workload opts into the public refresh API: cache generations
advance once per SQL batch, and one commit-delayed Channels notification carries
the batch identifiers. Canonical creation advances and notifies once per row.
History and workflow events remain per record. Applications requiring arbitrary
per-row signal receivers use the fallback and should not expect these gains.
The benchmark verifies refresh execution and notification delivery, not
concurrent rebuilding of application-owned cache entries. Its applied generation
counter is instrumentation; shared derived caches also need the commit timing
and transaction-local read policy described in the
[refresh integration guide](create_many.md#application-refresh-receivers).

The benchmark uses the in-memory workflow registry with per-record delivery
assertions. Durable database workflow/outbox commit, rollback and concurrent
conflict behavior are covered separately by PostgreSQL regression tests; their
throughput is not represented by these timings. Related search planning is real,
but search-server indexing is excluded. Measurements cover one local environment
and supported workload, not every manager or production deployment.

The 100,000 existing rows are seeded directly outside measurement, without
backfilling history for those fixtures. Every newly imported row must have its
required history and creator attribution. Timing order alternates across three
repetitions; competing local activity can still introduce noise. Larger batches
retain more row, history and callback objects, and a caller-owned transaction
retains commit work across batches until the outer transaction exits.
Seeding also warms database pages; these are not cold-start measurements.

The permission engine still evaluates every record and retains its existing user
resolution behavior. These user lookups, per-record permission object creation,
workflow payload creation and search target resolution remain linear costs.
Shared foreign-key validation and history-actor resolution are batched, but this
change does not introduce a broader permission-user cache or bulk outbox writer.

After benchmarking, stop only the services you created:

```bash
docker stop gm-bulk-perf-postgres gm-bulk-perf-redis
```
