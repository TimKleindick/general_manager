# Excel-backed Interfaces

`ExcelInterface` connects a `GeneralManager` to an `.xlsx` workbook without
making the workbook a second database. Excel remains the authoritative source;
GeneralManager provides typed field parsing, manager-style reads and writes, and
a disposable mirror that can be shared by workers through Django's cache.

## The interface model

Declare an `ExcelField` on the manager's nested `Interface` class. The nested
`Meta` class identifies the workbook, worksheet, row key, and either a named
Excel table or a header row. Exactly one of `table` and `header_row` is required.
The `key` must name one of the declared fields, and parsed key values must be
non-blank and unique.

Built-in fields cover the common typed values:

- `ExcelCharField` parses text and can enforce `max_length`.
- `ExcelIntegerField` parses integer values.
- `ExcelDecimalField` parses finite `Decimal` values and can enforce
  `decimal_places` and `max_digits`.
- `ExcelField` supports a custom Python type, parser, dumper, header, aliases,
  required/default behavior, and editability.

Field names are the default workbook headers. `header` selects the write header,
while `aliases` permits older read headers during synchronization. Aliases do not
change the header used for writes. Custom parser and dumper callables should be
importable and their parsed values must be serializable by the configured cache
backend.

## Reads and synchronization

The first startup sync parses the workbook into typed `ExcelRowSnapshot` values.
Later reads compare the workbook fingerprint and skip parsing when the file is
unchanged. A changed workbook is parsed and validated before the mirror is
replaced. `Interface.sync_from_excel(force=True)` always refreshes it, and the
manager-level `sync_excel()` helper returns an `ExcelSyncDelta` describing
created, updated, and deleted rows.

Manager field reads go through the mirror instead of a per-instance value cache.
Keeping a manager instance therefore does not hide a later edit made in Excel.
Direct workbook edits are discovered on the next Excel-backed read or explicit
sync; schedule explicit syncs when dependency-cache invalidation must happen even
if no Excel-backed read occurs.

`filter()`, `exclude()`, and `all()` return Excel-backed buckets. They support
`exact`, `lt`, `lte`, `gt`, `gte`, `contains`, `startswith`, `endswith`, and `in`
lookups, plus `sort()` by one or more declared fields. Bucket results are
manager instances identified by the configured key, not copies of workbook
rows.

When a custom Excel interface marks a scalar attribute as a GraphQL ID with
`is_identifier=True`, list filters and exclusions convert equality and
membership operands to the declared `int`, `str`, or `UUID` type. An integer
reference retains its full precision, and a text key such as `00017` retains
its leading zeros.

## GraphQL list operands

A flat scalar GraphQL list can evaluate filters, exclusions and ordering using
one fresh workbook generation after row and operand authorization. The operation
keeps the exact authorized manager instances and the ordinary comparison rules.
Custom permission callbacks continue to run, and an edit that changes the
generation while selecting rows sends the operation through ordinary reads.
This specific operand shortcut does not apply to selected output fields,
grouping, related operands, explicit sync, or write-conflict checks. A query
may separately use a workbook-wide read snapshot as described below.

Custom read capabilities are not opted in automatically. An
`ExcelReadCapability` subclass may explicitly declare
`query_operand_snapshot_fields = frozenset({...})` and use
`self.get_query_operand_snapshot(interface_instance)` before its normal
synchronization. When it returns a row, use that same row as the read
capability normally would, including observation state and defensive copying.
When it returns `None`, follow the existing read path. Only opt in for passive,
direct scalar fields with stable metadata and getters that return precisely
the same value from that mirror and perform no per-read validation, external
effects, or reentrant operations. Never use this snapshot to perform a sync
or write.

## Reuse the workbook during a read

GraphQL query operations use a coherent raw workbook snapshot. The first
read checks the workbook contents and keeps an already parsed version if the
fingerprint matches. Subsequent reads within that operation use the same raw
rows, including permission checks and selected fields. No permission decisions
or caller-specific results are shared by the workbook mirror. A new operation
checks the source again; an ordinary filesystem cannot provide a current
content hash without reading the bytes. An external edit during a read is
visible to the next operation. Explicit sync and write conflict checks still
use authoritative workbook reads.

For a single read-only export or equivalent non-GraphQL operation:

```python
from general_manager.interface.capabilities.excel import ExcelReadCapability

with ExcelReadCapability.read_snapshot_scope():
    export_rows = [(row.code, row.name) for row in Product.all()]
```

Custom Excel readers may explicitly declare `query_read_snapshots = True` when
their ordinary reads use identical mirror rows, and they require no extra
per-read validation, side effects or reentrant reads. Call
`get_query_read_snapshot(interface_instance)` before the normal read path;
on a hit keep the same observation and defensive-copy behavior. A miss runs
the ordinary read. A custom sync that delegates to `ExcelSyncCapability` may
use `workbook_read_fingerprint` in its normal, non-forced freshness comparison
to share this check with the base implementation. Keep using
`workbook_fingerprint` for parser/preflight, force and write conflict checks.

## Writes and authority

`create`, `update`, and `delete` write through to Excel and then refresh the
mirror. Keys identify rows and cannot be updated. Before an existing-row write,
GeneralManager compares the observed row snapshot with the current workbook
row. If the row is missing or changed, the operation raises
`ExcelWriteConflictError` and keeps the refreshed Excel state. Creating a key
that appeared in Excel raises the same conflict family. Validation and malformed
workbook layouts raise `ExcelValidationError` or `ExcelStructureError`; these
errors are preserved so callers can distinguish them from generic attribute
evaluation failures.

Successful synchronization invalidates dependency-cache entries affected by
created, changed, or deleted rows. The invalidation is fenced so a worker does
not publish a stale dependency index after losing the shared cache lock.

## Shared workers and external editors

Parsed snapshots use the cache selected by `Meta.cache_alias` (`"default"` by
default), and `cache_version` can separate incompatible parser or dumper
schemas. Use the same resolved workbook path and field declarations in every
worker. A shared Django cache improves read consistency, but it cannot reconcile
separate local copies of the workbook.

Workers check a small fingerprint marker before fetching a shared snapshot.
When that fingerprint matches the local mirror, field reads and manager
construction reuse the parsed rows without deserializing the entire dataset.
Missing or incomplete cache entries retain the local mirror; synchronization
still checks the workbook content fingerprint and rebuilds from Excel when
needed. This does not introduce a polling delay or weaken write-conflict checks.
Snapshot cache keys use a separate namespace from older versions that did not
publish fingerprint markers, so each worker version rebuilds its own disposable
cache during a rolling upgrade.

GeneralManager coordinates its own processes with a persistent
`<workbook>.gm.lock` sidecar and writes via a temporary sibling file followed by
atomic replacement. Lock objects are reused within each process for nested access;
forked workers receive fresh lock objects and a fresh cache guard while still
coordinating through the same sidecar file. The workbook directory must allow
lock-file creation and temporary writes. Desktop Excel and other external programs
do not honor the sidecar lock, so fingerprint checks detect many—but not
every—overlapping save.
Coordinate human edits with application writes when lost updates are
unacceptable. `openpyxl` reads the values last saved by Excel and does not
calculate formulas.
