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
```

## Arithmetic and comparisons

- Addition and subtraction require compatible units or the same currency.
- Multiplication and division combine units as expected (e.g., metres × metres → square metres).
- Comparisons compare magnitudes after converting to a common unit.

```python
width = Measurement("50 cm")
area = length * width           # 2.5 m ** 2
is_longer = length > width       # True
```

## Serialisation

Use `.serialize()` when you need structured data for JSON responses. The method returns a dictionary with `value` and `unit` keys.
