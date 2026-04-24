# Generate Bulk Test Data

Factories make it easy to seed development databases or create fixtures for end-to-end tests.

## Step 1: Define factories
Factories are automatically generated based on the manager's interface. But you can customise generation by defining a nested `Factory` class.

```python
from datetime import date
from general_manager.factory import (
    lazy_measurement,
    lazy_delta_date,
    lazy_project_name,
)
from general_manager.measurement import (
    MeasurementField,
    Measurement,
)
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from django.db.models import (
    CharField,
    DateField,
)
from typing import Optional

class Project(GeneralManager):
    name: str
    start_date: Optional[date]
    end_date: Optional[date]
    total_capex: Optional[Measurement]

    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        start_date = DateField(null=True, blank=True)
        end_date = DateField(null=True, blank=True)
        total_capex = MeasurementField(base_unit="EUR", null=True, blank=True)

        class Factory:
            name = lazy_project_name()
            end_date = lazy_delta_date(365 * 6, "start_date")
            total_capex = lazy_measurement(75_000, 1_000_000, "EUR")
```

## Step 2: Create batches

```python
Project.Factory.create_batch(20)
InventoryItem.Factory.create_batch(50)
```

Many-to-many relations are populated automatically with sensible defaults.

## Step 3: Customise values

Override attributes when calling the factory:

```python
Project.Factory(name="Launch Project", start_date=date.today())
```

For complex scenarios, define `@classmethod` helpers that produce pre-wired object graphs (projects with members, factories with related measurements, etc.).

## Use `_adjustmentMethod` for complex data creation

When a single factory call needs to fan out into multiple records, or when the final payload depends on derived values, define `Factory._adjustmentMethod`.

`_adjustmentMethod` receives the keyword arguments passed to the factory after relation values have been normalized. It must return either:

- one `dict[str, Any]` for a single record
- a `list[dict[str, Any]]` for multiple records

`Factory.create(...)` validates and saves every returned record, then wraps the saved models back into their `GeneralManager` class. `Factory.build(...)` runs the same adjustment logic but returns unsaved model instances.

Example:

```python
from typing import Any
from django.db.models import CharField, PositiveIntegerField
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager

class Fleet(GeneralManager):
    label: str
    capacity: int

    class Interface(DatabaseInterface):
        label = CharField(max_length=64)
        capacity = PositiveIntegerField()

        class Factory:
            @staticmethod
            def _adjustmentMethod(
                *,
                label: str = "Fleet",
                capacity: int = 0,
                count: int = 1,
                **extra: Any,
            ) -> list[dict[str, Any]]:
                records: list[dict[str, Any]] = []
                for index in range(count):
                    record = {
                        "label": f"{label}-{index}",
                        "capacity": capacity + index,
                    }
                    if "changed_by" in extra:
                        record["changed_by"] = extra["changed_by"]
                    records.append(record)
                return records
```

```python
fleets = Fleet.Factory.create(label="North", capacity=10, count=3)

assert [fleet.label for fleet in fleets] == [
    "North-0",
    "North-1",
    "North-2",
]
assert [fleet.capacity for fleet in fleets] == [10, 11, 12]
```

Use this hook when the number of records is dynamic, when values need to be generated from a shared seed, or when the created objects must stay internally consistent. Keep `_adjustmentMethod` focused on shaping record dictionaries; validation still happens later through the normal model `full_clean()` and save flow.

## Step 4: Integrate with pytest fixtures

```python
@pytest.fixture
def project_factory() -> Callable[..., Project]:
    def _factory(**kwargs):
        return Project.Factory(**kwargs)
    return _factory
```

Use the fixture in tests to create data on demand.

## Seed a manager landscape from the command line

Use `seed_manager_landscape` when you want a local or demo database to contain a minimum number of rows for one or more managers that already expose factories.

The command is explicit by default. Select managers with `--manager`, or pass `--all` to target every manager discovered by GeneralManager that has `Factory.create_batch`.
When `--count` is omitted, each selected manager targets 1 row by default,
including when using `--all`. When `--batch-size` is omitted, batches default to
100 rows per transaction; this only changes how larger missing counts are split
into transactions.

```bash
python manage.py seed_manager_landscape \
  --manager Project \
  --manager InventoryItem \
  --count 10 \
  --target InventoryItem=50 \
  --batch-size 25
```

Targets are minimum totals. If `Project` already has 12 rows, `--count 10` creates no additional projects. If `InventoryItem` has 20 rows, `--target InventoryItem=50` creates 30 more.

Use `--dry-run` to inspect ordering and missing dependencies without writing data:

```bash
python manage.py seed_manager_landscape --manager Project --count 10 --dry-run
```

The command orders selected managers so required database relations are seeded first when both sides are selected. It does not automatically add unselected dependencies; select those managers explicitly when your factories require existing related data.

By default, seeding stops at the first failure and reports the manager and batch size. Use `--continue-on-error` to continue with later managers and receive a summary at the end. Successful batches that already committed remain in place, and the summary includes partial progress for the failed manager.

## Step 5: Tear down

Use database transactions or pytest's `django_db(reset_sequences=True)` marker to keep the test environment clean after bulk creation.
