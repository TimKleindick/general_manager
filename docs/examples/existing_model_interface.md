# Existing Model Interface Recipes

These examples show how to adopt `ExistingModelInterface` for legacy tables while keeping the GeneralManager ergonomics.

## Wrapping a swappable Django User model

Keep the Django auth model in `models.py` and expose the GeneralManager wrapper from `managers.py`. That lets both classes be named `User` while giving shells and application code a canonical manager import path.

```python
from django.conf import settings

from general_manager.interface import ExistingModelInterface
from general_manager.manager import GeneralManager, graph_ql_property


class User(GeneralManager):
    id: int
    username: str
    email: str | None

    class Interface(ExistingModelInterface):
        model = settings.AUTH_USER_MODEL

    class Factory:
        username = "legacy-user"
        email = "legacy@example.com"
        password = "not-hashed"

    @graph_ql_property
    def display_name(self) -> str:
        if self.email:
            return f"{self.username} ({self.email})"
        return self.username
```

Import the wrapper with `from myapp.managers import User`; keep `myapp.models.User` for direct ORM usage only. GeneralManager auto-imports `myapp.managers` during startup so foreign keys pointing at `settings.AUTH_USER_MODEL` resolve to the wrapper once the app is loaded.

`User.create` and `User.update` call through to the configured auth table, `User.filter(is_active=True)` returns manager instances, and `User.Factory.create()` seeds users for tests without duplicating schema.

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
