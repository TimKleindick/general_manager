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

GeneralManager coordinates its own processes with a persistent
`<workbook>.gm.lock` sidecar and writes via a temporary sibling file followed by
atomic replacement. The workbook directory must allow lock-file creation and
temporary writes. Desktop Excel and other external programs do not honor the
sidecar lock, so fingerprint checks detect many—but not every—overlapping save.
Coordinate human edits with application writes when lost updates are
unacceptable. `openpyxl` reads the values last saved by Excel and does not
calculate formulas.
