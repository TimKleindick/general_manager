# Generate Bulk Test Data

Factories make it easy to seed development databases or create fixtures for end-to-end tests.

## Step 1: Define factories
Factories are automatically generated based on the manager's interface. But you can customise generation by defining a nested `Factory` class.

```python
from datetime import date
from general_manager.factory import (
    LazyMeasurement,
    LazyDeltaDate,
    LazyProjectName,
)
from general_manager.measurement import (
    MeasurementField,
    Measurement,
)
from general_manager.interface.databaseInterface import DatabaseInterface
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
            name = LazyProjectName()
            end_date = LazyDeltaDate(365 * 6, "start_date")
            total_capex = LazyMeasurement(75_000, 1_000_000, "EUR")
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

## Step 4: Integrate with pytest fixtures

```python
@pytest.fixture
def project_factory() -> Callable[..., Project]:
    def _factory(**kwargs):
        return Project.Factory(**kwargs)
    return _factory
```

Use the fixture in tests to create data on demand.

## Step 5: Tear down

Use database transactions or pytest's `django_db(reset_sequences=True)` marker to keep the test environment clean after bulk creation.
