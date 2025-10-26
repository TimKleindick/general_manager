# Validation and Clean Hooks

Factories respect the same validation pipeline as your application code. Understanding how `clean()` and rules interact ensures your fixtures remain realistic.

## Interface validation

`InterfaceBase` calls `parse_input_fields_to_identification()` to convert raw inputs into validated types. During `create` and `update`, interfaces execute `full_clean()` on the underlying Django model, invoke rules declared in `Meta.rules`, and run custom `clean()` methods.

## Factory interaction

- `AutoFactory._model_creation()` assigns field values, runs `full_clean()`, and saves the model. Validation errors bubble up, making faulty fixtures obvious.
- Override `AutoFactory._adjustmentMethod` to perform additional preprocessing (for example, generating related objects) before the model is saved.

## Best practices

1. Mirror production defaults. If a field has a rule requiring positive values, set a default in the factory that satisfies it.
2. Wrap factory calls in helper functions that also create related managers, ensuring rules that reference relationships pass in tests.
3. Test validation logic directly by asserting that factories raise `ValidationError` when provided with invalid data.

Example:

```python
import pytest
from django.core.exceptions import ValidationError

def test_negative_budget_is_rejected(project_factory):
    with pytest.raises(ValidationError):
        project_factory(total_capex="-1 EUR")
```

By keeping factories aligned with interface validation, your tests will catch regressions early.
