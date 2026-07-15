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

Import the wrapper with `from myapp.managers import User`; keep `myapp.models.User` for direct ORM usage only. GeneralManager auto-imports `myapp.managers` during startup so foreign keys pointing at `settings.AUTH_USER_MODEL` resolve to the wrapper once the app is loaded. In Django shell sessions, GeneralManager's shell command prefers wrapper import paths over raw model import paths when a model is linked through `_general_manager_class`, then appends any registered manager classes Django did not already auto-import.

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

`Project.create(name="TBD")` now raises a validation error, while historical
tracking and activation semantics continue to flow through to `LegacyProject`.
If Django's field validation also reports `name`, GeneralManager keeps both the
Django message and the rule message in the final `ValidationError`.

## Route atomic writes and history to another database

For a legacy model that already uses simple-history, select the same database
alias for the interface and a database-aware history tracker:

```python
# models.py
from django.db import models

from general_manager.interface.utils.history import DatabaseAwareHistoricalRecords


class LegacyContract(models.Model):
    title = models.CharField(max_length=120)
    reviewers = models.ManyToManyField("auth.User", blank=True)
    history = DatabaseAwareHistoricalRecords(m2m_fields=["reviewers"])


# managers.py
from general_manager.interface import ExistingModelInterface
from general_manager.manager import GeneralManager
from contracts.models import LegacyContract


class Contract(GeneralManager):
    class Interface(ExistingModelInterface):
        model = LegacyContract
        database = "archive"
```

With an `archive` entry in `DATABASES`, this directly usable write commits the
row, audit reason, and relation table together on that alias:

```python
contract = Contract.create(
    title="Supply agreement",
    reviewers_id_list=[reviewer.id],
    history_comment="imported",
    ignore_permission=True,
)
contract.update(
    title="Supply agreement v2",
    reviewers_id_list=[lead_reviewer.id],
    history_comment="review reassigned",
    ignore_permission=True,
)
```

Any validation, save, history-reason, or many-to-many exception rolls the whole
ordinary mutation back. Models without pre-existing history are registered
automatically. `DatabaseAwareHistoricalRecords` is the required 0.63.1 helper
for a pre-tracked model on a non-default alias, but its implementation-module
import is not part of the stable `general_manager.interface` export registry;
pin and test the dependency when using it. See the
[task guide](../howto/orm_atomic_writes.md) for rollback handling.
