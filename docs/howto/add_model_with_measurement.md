# Add a Manager with Measurements

Learn how to add a manager that stores measurement values with automatic unit conversion.

## Step 1: Declare the manager

```python
# core/managers.py
from django.db.models import CharField

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.measurement import Measurement, MeasurementField

class InventoryItem(GeneralManager):
    name: str
    weight: Measurement
    price: Measurement

    class Interface(DatabaseInterface):
        name = CharField(max_length=120)
        weight = MeasurementField(base_unit="kg", null=True, blank=True)
        price = MeasurementField(base_unit="EUR", null=True, blank=True)
```

## Step 2: Run migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

## Step 3: Create data

```python
item = InventoryItem.create(
    creator_id=1,  # replace with your user ID
    name="Battery pack",
    weight="2.6 kg",
    price="199.00 EUR",
)
```

## Step 4: Convert units

When you read data from the manager, you receive `Measurement` objects:

```python
item.weight # returns Measurement(2.6, 'kg')
item.weight.to("gram") # returns Measurement(2600.0, 'gram')
item.price.to("USD", exchange_rate=1.1) # returns Measurement(218.9, 'USD')
```

## Step 5: Expose via GraphQL

The GraphQL schema exposes measurements as objects. Clients can request measurements directly and specify the desired unit.

```graphql
query {
  inventoryItemList {
    name
    weight(targetUnit: "gram") {
      value
      unit
    }
    price {
      value
      unit
    }
  }
}
```

You now have a measurement-aware manager ready for reporting and analytics.
