# Units and Currency

GeneralManager wraps [Pint](https://pint.readthedocs.io/) to provide unit-aware values. The `Measurement` class lives in `general_manager.measurement.measurement` and supports arithmetic, comparison, and formatting.

## Creating measurements

```python
from general_manager.measurement import Measurement

length = Measurement(5, "meter")
price = Measurement.from_string("19.99 EUR")
```

Measurements keep both the magnitude and the original unit. Convert units with `.to()`:

```python
length_cm = length.to("centimeter")
price_usd = price.to("USD", exchange_rate=1.08)
freight_eur = Measurement(100, "USD/t").to("EUR/t", exchange_rate=0.92)
```

## Arithmetic and comparisons

- Addition and subtraction require compatible units or the same currency.
- Multiplication and division combine units as expected (e.g., metres × metres → square metres).
- Equality accepts another `Measurement` only. Parse convenience text explicitly
  with `Measurement.from_string("50 cm")`. Compatible Measurement equality,
  hashing, and relational ordering use the same canonical converted magnitude;
  temperature values use the documented comparison bin so equal values never
  sort differently.

```python
width = Measurement.from_string("50 cm")
area = length * width           # 2.5 m ** 2
is_longer = length > width       # True
```

## Serialisation

For application JSON, choose an explicit representation. `Measurement` exposes
`magnitude` and `unit`; it does not provide a `.serialize()` method:

```python
payload = {"value": str(length.magnitude), "unit": length.unit}
```

The string magnitude preserves decimal precision. Generated GraphQL measurement
objects instead expose `value` as a Float and `unit` as text; that transport
can lose Decimal precision. Use the
[dataframe helpers](../../howto/dataframe_measurements.md) for paired columns.
