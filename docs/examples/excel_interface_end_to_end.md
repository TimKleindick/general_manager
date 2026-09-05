# Excel Interface End to End

This recipe exposes a product table as a typed manager, reads it with normal
manager queries, and writes changes back to the workbook. The workbook must
already contain a `ProductsTable` table on the `Products` sheet with `sku`,
`name`, and `price` columns.

```python
from decimal import Decimal

from general_manager.interface import (
    ExcelCharField,
    ExcelDecimalField,
    ExcelInterface,
)
from general_manager.manager import GeneralManager


class Product(GeneralManager):
    sku: str
    name: str
    price: Decimal

    class Interface(ExcelInterface):
        sku = ExcelCharField(max_length=24, unique=True)
        name = ExcelCharField(max_length=120)
        price = ExcelDecimalField(max_digits=10, decimal_places=2)

        class Meta:
            workbook = "/shared/data/products.xlsx"
            sheet = "Products"
            table = "ProductsTable"
            key = "sku"
            cache_alias = "default"
            cache_version = "1"


# Reads synchronize when the workbook fingerprint has changed.
active_products = Product.filter(name__startswith="Active")
most_expensive = Product.all().sort("price", reverse=True).first()

if most_expensive is not None:
    most_expensive.update(price=Decimal("199.99"), ignore_permission=True)

created = Product.create(
    sku="SKU-1002",
    name="Active replacement",
    price=Decimal("49.50"),
    ignore_permission=True,
)

# Force discovery of edits made directly in Excel and inspect the row delta.
delta = Product.sync_excel()
print(delta.created, delta.updated, delta.deleted)
```

Use `Product.Interface.sync_from_excel(force=True)` when code needs the
interface-level result without the manager helper. A later
`Product(sku="SKU-1002")` field read observes the refreshed mirror rather than
values cached on an older manager instance.

If another writer changes the workbook between a manager read and an update or
delete, the operation raises `ExcelWriteConflictError`; refresh the manager and
retry using the current Excel values. A create whose key already exists in Excel
also raises that conflict. Invalid cells raise `ExcelValidationError`, while
missing headers, duplicate keys, and blank keys raise `ExcelStructureError`.

For multiple workers, mount the same workbook path everywhere, use a shared
Django cache for the mirror and dependency tracking, and keep the workbook's
`.gm.lock` sidecar writable. External desktop Excel sessions do not honor the
sidecar lock, so coordinate overlapping human and application edits.

Use `ExcelIntegerField` for integer columns or `ExcelField` with a custom
`python_type`, `parser`, and `dumper` when the workbook value needs application
specific conversion.
