# How-To: Use an Excel Interface

`ExcelInterface` lets a manager read and write rows from an `.xlsx` workbook while keeping a Django-cache-backed mirror for shared reads.

## Define the manager

Declare Excel fields on the nested interface and point `Meta` at either an Excel table or a header row.

```python
from decimal import Decimal

from general_manager.interface import ExcelInterface
from general_manager.interface.excel import ExcelCharField, ExcelDecimalField
from general_manager.manager import GeneralManager


class Product(GeneralManager):
    sku: str
    name: str
    price: Decimal

    class Interface(ExcelInterface):
        sku = ExcelCharField(max_length=24, unique=True)
        name = ExcelCharField(max_length=120, header="Product Name", aliases=("Name",))
        price = ExcelDecimalField(max_digits=10, decimal_places=2)

        class Meta:
            workbook = "data/products.xlsx"
            sheet = "Products"
            table = "ProductsTable"
            key = "sku"
```

For a plain worksheet range, use `header_row` instead of `table`:

```python
class Meta:
    workbook = "data/products.xlsx"
    sheet = "Products"
    header_row = 1
    key = "sku"
```

Configure exactly one of `table` or `header_row`. Table mode reads and resizes the named Excel table during creates and deletes. Header-row mode reads headers from the configured row and writes below the last non-empty row.

## Headers and keys

`Meta.key` names the Excel field that identifies each row. Keys must be present, non-blank, and unique after parsing.

By default, a field reads and writes the column with the same name as the field. Set `header="Product Name"` when the workbook header differs from the field name. Set `aliases=("Name",)` to allow older read headers during sync; writes still use the declared field header.

## Startup sync and checks

Excel interfaces register a startup hook that runs `sync_from_excel(force=True)`. This warms the in-memory mirror before the first read. If the workbook is missing or structurally invalid at startup, GeneralManager logs the problem and leaves the previous mirror state intact.

Excel interfaces also register a Django system check. The check reads workbook structure without mutating the workbook or replacing the mirror. It reports warnings for missing workbooks, sheets, tables, header rows, declared field headers, duplicate keys, and blank keys.

## Writes and conflicts

`create`, `update`, and `delete` write through to the workbook, then sync the mirror back from Excel. Unknown workbook columns are preserved.

Excel remains authoritative. Before a write, GeneralManager refreshes the row from the workbook. If Excel changed, removed the row, or already contains a created key, the write raises an Excel conflict error and the refreshed Excel state wins.

Successful syncs compare mirror snapshots and invalidate dependency-cache entries for created, updated, or deleted rows, so cached calculations that depend on Excel-backed values are refreshed.

## Shared cache mirrors

Parsed rows and the workbook fingerprint are stored as a complete snapshot through
Django's cache API. `Meta.cache_alias` selects a configured cache (default:
`"default"`). For example:

```python
class Meta:
    workbook = "/shared/data/products.xlsx"
    sheet = "Products"
    table = "ProductsTable"
    key = "sku"
    cache_alias = "default"
    cache_version = "1"
```

Use a shared backend such as Django's Redis cache in production. Configure the
**default cache** as shared too, because GeneralManager's dependency tracking
uses it. No Redis-specific methods are required by the Excel mirror. Redis client
installation and server configuration remain application deployment choices.

Cache identities include the resolved workbook path, interface name, table/range,
and field configuration. Use the same absolute workbook path and declarations on
all machines. Increment `cache_version` when changing custom parser/dumper
behavior without changing its import name. Parsed values must be serializable by
the chosen backend. Snapshots have no time-based expiry, but remain disposable:
eviction and rejected or failed cache writes are supported.

Workers check the file fingerprint before reading a snapshot. An unchanged file
is hashed but is not reparsed by openpyxl. Changed workbooks are parsed and
validated before publishing a replacement snapshot. Excel-backed manager fields
read through the mirror, so keeping a manager instance does not hide later edits.
Changes made directly in Excel are discovered on the next Excel read or explicit
`Product.sync_excel()`; schedule syncs if cached calculations must refresh even
when no Excel reads occur.

Local-memory caches work within each worker. The dummy backend works without
shared snapshots. Mirror cache errors are logged and fall back to local snapshots
and workbook reads. This does not suppress failures in the application's separate
dependency-invalidation infrastructure. If that infrastructure fails during sync,
the previous baseline is retained so the next sync can retry invalidation.
When a worker has no previous snapshot, synchronization conservatively invalidates
that manager's tracked results, including results for rows deleted from Excel.

## Multiple workers and workbook safety

A persistent `<workbook>.gm.lock` sidecar serializes cooperating GeneralManager
syncs and writes using operating-system file locks. It is independent of Django's
cache backend and releases when the owning process exits. Lock acquisition waits
up to 30 seconds, then raises `filelock.Timeout`. The workbook directory must
permit sidecar creation and temporary-file writes. Do not delete a sidecar while
workers are running; replacing it can defeat coordination.

On one machine, all workers use the same workbook. On multiple machines, mount the
same workbook directory at the same path and use a filesystem that supports
cross-machine file locking and atomic replacement. A shared Redis cache alone
does not make separate local copies of a workbook consistent. For storage without
those filesystem guarantees, route workbook operations through one machine.

Writes save to a temporary sibling file before replacing the workbook, so an
interrupted save does not truncate the original. Table deletions shift only cells
inside the table; table expansion refuses to overwrite occupied cells below it.
The saved version is checked against the workbook fingerprint before replacement.

Desktop Excel and other programs do not honor this sidecar lock. Fingerprint
checks detect many conflicting external edits, but cannot exclude an external
save in the final check/replace window. Coordinate human edits with application
writes when lost updates are unacceptable. openpyxl does not calculate formulas;
formula reads use the values last saved by Excel.
