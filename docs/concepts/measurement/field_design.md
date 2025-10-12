# Persisting Measurements

`MeasurementField` stores measurements in Django models. It persists the magnitude in a configurable base unit while tracking the input unit for display.

## Declaring fields

```python
from django.db import models
from general_manager.measurement import MeasurementField

class Product(models.Model):
    weight = MeasurementField(base_unit="kg", null=True, blank=True)
    price = MeasurementField(base_unit="EUR", null=True, blank=True)
```

Assignments accept `Measurement` instances or human-readable strings:

```python
product.weight = Measurement(750, "gram")
product.price = "19.99 EUR"
```

## Base units

Choose base units that match your reporting needs. All stored values convert to the base unit automatically. When reading from the database, the original unit is restored so you can display the same unit the user provided.

## Validation and forms

`MeasurementField` plugs into Django forms and admin just like other fields. Invalid strings raise `ValidationError` with descriptive messages. To customise form widgets, subclass `MeasurementFormField` or provide your own input parsing logic.

## Migrations

Because measurements store magnitudes as decimals, they migrate cleanly across databases. If you change the base unit, run a data migration to rescale stored values.
