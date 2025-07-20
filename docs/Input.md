# Input

The `Input` class describes the expected type for a field and optionally defines valid choices and dependencies. It is primarily used in interfaces to configure GeneralManager initialization (keyword) arguments.

## Basic usage

Create an `Input` by passing the target type. You may provide an iterable of possible values or a callable that returns them.

```python
from datetime import date

from general_manager.interface.calculationInterface import CalculationInterface
from general_manager.manager import Input

class MyInterface(CalculationInterface):
    quantity = Input(int)
    color = Input(str, possible_values=["red", "green", "blue"])
```

When `possible_values` is a callable you can also declare dependencies. They are automatically extracted from the callable signature if not provided explicitly.

```python
from yourapp.managers import Project

def allowed_dates(project: Project) -> list[date]:
    return [v.date for v in project.derivativevolume_list]

class ProjectCommercial(CalculationInterface):
    project = Input(Project)
    date = Input(date, possible_values=allowed_dates, depends_on=["project"])
```

## Casting values

`Input.cast()` converts incoming data to the configured type. The method handles common conversions automatically:

- `date` and `datetime` values accept ISO formatted strings and convert between each other.
- `GeneralManager` subclasses can be created from dictionaries or integers representing their `id`.
- `Measurement` values may be provided as strings (for example `"10 kg"`).

If the value already matches the target type it is returned unchanged.

```python
from general_manager.manager import Input

input_obj = Input(int)
number = input_obj.cast("123")    # returns 123
```

