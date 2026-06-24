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

You can also create and parse measurements directly when building calculated
values or tests:

```python
weight = Measurement(2.6, "kg")
price = Measurement.from_string("199.00 EUR")
ratio = Measurement.from_string("0.25") # dimensionless

total_weight = weight + Measurement(400, "gram")
discounted_price = price * Measurement(10, "percent")
discounted_price.to("EUR") # returns Measurement(19.9, 'EUR')
```

Addition and subtraction require compatible units. Currency values must use the
same currency code for arithmetic, and converting between currencies requires an
explicit exchange rate. The exchange rate is target currency per one source
currency unit:

```python
price.to("USD", exchange_rate=1.1)

# Raises MissingExchangeRateError because no exchange rate is provided.
price.to("USD")
```

`Measurement.from_string()` accepts either `<value> <unit>` or a single
dimensionless value. Empty-string and `"dimensionless"` units both expose the
canonical unit `"dimensionless"`. Invalid text raises
`InvalidMeasurementStringError` or
`InvalidDimensionlessValueError`; incompatible arithmetic raises one of the
measurement-specific `TypeError` or `ValueError` subclasses. Equivalent
measurements compare and hash by their canonical unit value, so
`Measurement(1, "kg")` and `Measurement(1000, "gram")` can be used
interchangeably as dictionary keys.

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
