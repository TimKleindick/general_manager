# Measurement

GeneralManager builds on top of [Pint](https://pypi.org/project/Pint/) to handle physical units and currencies. Install the package with `pip install general_manager` and add `general_manager` to your `INSTALLED_APPS`.

## Working with Measurements

Import the `Measurement` class and create objects with a numeric value and unit:

```python
from general_manager.measurement import Measurement

length = Measurement(5, "meter")
price = Measurement(100, "EUR")
```

You can also parse strings via `from_string`:

```python
from general_manager.measurement import Measurement

speed = Measurement.from_string("10 km")
```

Use `.to()` for unit conversions. Currency conversions require an `exchange_rate`:

```python
from general_manager.measurement import Measurement

length = Measurement(5, "meter")
price = Measurement(100, "EUR")

length.to("cm")
price.to("USD", exchange_rate=1.2)
```

## Arithmetic operations

`Measurement` objects behave like numeric values and support basic math. Units must be compatible and currency amounts need the same currency.

```python
from general_manager.measurement import Measurement

length = Measurement(2, "m")
width = Measurement("50 cm")

total_length = length + width    # 2.5 m
diff = length - width            # 1.5 m
area = length * width            # 1 m ** 2
half = length / 2                # 1 m

from general_manager.measurement import Measurement

price = Measurement(15, "EUR")
tax = Measurement(3, "EUR")
total_price = price + tax        # 18 EUR
```

## Storing measurements in models

`MeasurementField` saves the magnitude in a base unit while remembering the original unit.

```python
from django.db import models
from general_manager.measurement import Measurement, MeasurementField

class Product(models.Model):
    weight = MeasurementField(base_unit="kg", null=True, blank=True)
    price = MeasurementField(base_unit="EUR", null=True, blank=True)
```

Assignments accept `Measurement` objects or strings:

```python
product.weight = Measurement(500, "g")
product.price = "12.5 EUR"
```

After adding a `MeasurementField`, run your usual `makemigrations` and `migrate` commands so the database columns are created.
