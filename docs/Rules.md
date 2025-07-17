# Rule Validation

The rule system allows you to validate instances of a `GeneralManager` subclass with small predicate functions such as lambdas. A rule returns a boolean value and automatically generates error messages if the condition fails.

## Using lambdas or regular functions

The callable passed to `Rule` can be a lambda or a named function. It must accept a single parameter representing the object under validation and return `True` or `False`.

```python
from general_manager.rule import Rule

def check_price(obj):
    return obj.price > 0

rule = Rule(check_price)
```

The rule system extracts variable names from the function body to generate helpful error messages.

### Type hints

Use generics to specify the expected model type. Forward references (as strings) are allowed if the class is defined later.
```python
Rule["Project"](lambda x: x.start_date < x.end_date)
```

## Attaching rules to a model

Rules are typically defined inside the `Meta` class of an interface. The following
snippet taken from the example project demonstrates two rules for the
`Project` model:

```python
from datetime import date
from typing import cast

from django.core.validators import RegexValidator
from django.db.models import CharField, constraints

from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.measurement import Measurement
from general_manager.rule import Rule

class Project(GeneralManager):
    ...

    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        number = CharField(max_length=7, validators=[RegexValidator(r"^AP\d{4,5}$")])
        ...

        class Meta:
            constraints = [
                constraints.UniqueConstraint(fields=["name", "number"], name="unique_booking")
            ]

            rules = [
                Rule["Project"](lambda x: cast(date, x.start_date) < cast(date, x.end_date)),
                Rule["Project"](lambda x: cast(Measurement, x.total_capex) >= "0 EUR"),
            ]
```
The string "Project" is a forward reference for type checking. If the class is already defined, you can also use the class directly: `Rule(Project)(...)`.

When a `Project` instance is created or updated, each rule is evaluated and can
prevent the operation if the condition is not met.

## Custom error messages

A rule may define its own error message using placeholders for the involved
variables:

```python
from general_manager.rule import Rule

rule = Rule(
    lambda x: x.quantity <= x.stock,
    custom_error_message="Ordered quantity ({quantity}) exceeds available stock ({stock}).",
)
```

Placeholders must match the names used inside the lambda expression. If a
placeholder is missing, `validateCustomErrorMessage()` raises a `ValueError`.

## Ignoring `None` values

The third parameter of `Rule` controls how `None` values are handled. When
`ignore_if_none=True` (the default), the rule returns `None` if any referenced
attribute is `None`:

```python
from general_manager.rule import Rule

rule = Rule(lambda x: x.price > 0, ignore_if_none=True)
```

If you want the rule to fail instead, set `ignore_if_none=False`.

## Evaluating rules manually

A rule can be evaluated independently of the model framework:

```python
from general_manager.rule import Rule

rule = Rule(lambda x: x.age >= 18)
result = rule.evaluate(user)
if not result:
    errors = rule.getErrorMessage()
```

`getErrorMessage()` returns a dictionary mapping each variable to a generated
message. If the rule passed or was skipped, `None` is returned.

## Extending rule behaviour

Custom handlers can be registered via the Django setting `RULE_HANDLERS`.
Each handler must subclass `BaseRuleHandler` and implements custom error
messages for expressions such as `len(x)` or `sum(x)`.

```python
# settings.py
RULE_HANDLERS = ["myapp.rules.MyHandler"]
```

The built-in handlers support `len()`, `sum()`, `max()` and `min()`.

