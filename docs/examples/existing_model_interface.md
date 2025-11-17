# Existing Model Interface Recipes

These examples show how to adopt `ExistingModelInterface` for legacy tables while keeping the GeneralManager ergonomics.

## Wrapping Django's built-in User model

The default Django auth model already includes history and activation-compatible fields (`is_active`, `last_login`, etc.). Import it and expose the fields you want to work with from the manager; the factory gives you consistent fixtures.

```python
from django.contrib.auth import get_user_model

from general_manager.interface import ExistingModelInterface
from general_manager.manager import GeneralManager, graph_ql_property


User = get_user_model()


class Account(GeneralManager):
    id: int
    username: str
    email: str | None

    class Interface(ExistingModelInterface):
        model = User

    class Factory:
        username = "legacy-user"

    @graph_ql_property
    def display_name(self) -> str:
        if self.email:
            return f"{self.username} ({self.email})"
        return self.username
```

`Account.create` and `Account.update` call through to the `auth_user` table, `Account.filter(is_active=True)` returns manager instances, and `Account.Factory.create()` seeds users for tests without duplicating schema.

## Adding validation to a legacy model

Combine existing business rules with new ones by declaring `Meta.rules`. The interface merges them and rewires `full_clean()` so the complete rule set runs everywhere.

```python
from django.db import models

from general_manager.interface import ExistingModelInterface
from general_manager.manager import GeneralManager
from general_manager.rule import Rule


class LegacyProject(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "projects"


class UniqueNameRule(Rule):
    """Reject placeholder project names."""

    def evaluate(self, obj: models.Model) -> bool:
        return obj.name != "TBD"

    def get_error_message(self) -> dict[str, list[str]]:
        return {"name": ["tbd_not_allowed"]}


class Project(GeneralManager):
    name: str
    is_active: bool

    class Interface(ExistingModelInterface):
        model = LegacyProject

        class Meta:
            rules = [UniqueNameRule()]
```

`Project.create(name="TBD")` now raises a validation error, while historical tracking and activation semantics continue to flow through to `LegacyProject`.
