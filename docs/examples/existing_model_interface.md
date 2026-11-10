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

Declare a predicate in `Meta.rules` to add validation to a wrapped model. This
independent example assumes an installed `projects` application that owns the
following legacy model and uses `Project` as its GeneralManager wrapper.

Define the Django model in `projects/models.py`:

```python
from django.db import models


class LegacyProject(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "projects"


```

Define the wrapper in `projects/managers.py` so GeneralManager discovers it:

```python
from general_manager import GeneralManager
from general_manager.interface import ExistingModelInterface
from general_manager.rule import Rule
from projects.models import LegacyProject


class Project(GeneralManager):
    name: str
    is_active: bool

    class Interface(ExistingModelInterface):
        model = LegacyProject

        class Meta:
            rules = [
                Rule["Project"](
                    lambda project: project.name != "TBD",
                    custom_error_message="Choose a descriptive project name.",
                ),
            ]
```

Create and apply migrations after registering the wrapper, including its
history model:

```bash
python manage.py makemigrations projects
python manage.py migrate
```

Then open `python manage.py shell` and try an authenticated write. Choose a fresh
username if you repeat the example:

```python
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from projects.managers import Project

author = get_user_model().objects.create_user(username="legacy-rule-author")
try:
    Project.create(creator_id=author.pk, name="TBD")
except ValidationError as error:
    print(error.message_dict["name"])
else:
    raise AssertionError("The placeholder name must fail validation")
```

The `name` errors include `Choose a descriptive project name.`; no project row
is created. Historical tracking and activation semantics continue to flow
through to `LegacyProject`. If Django's field validation also reports `name`,
GeneralManager keeps both the Django message and the rule message in the final
`ValidationError`.

## Route atomic writes and history to another database

For a legacy model that already uses simple-history, select the same database
alias for the interface and a database-aware history tracker:

```python
# models.py
from django.conf import settings
from django.db import models

from general_manager.interface.utils.history import DatabaseAwareHistoricalRecords


class LegacyContract(models.Model):
    title = models.CharField(max_length=120)
    reviewers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True)
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
from django.contrib.auth import get_user_model

users = get_user_model().objects.using("archive")
reviewer = users.get(pk=41)  # Pre-existing user on the archive database.
lead_reviewer = users.get(pk=42)  # Pre-existing user on the archive database.

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

To retain the reason for a hard delete, capture the identifier before deleting
the manager. The history row remains queryable after the live row is gone:

```python
contract_id = contract.identification["id"]
contract.delete(
    history_comment="manual cleanup",
    ignore_permission=True,
)

delete_history = (
    LegacyContract.history.filter(id=contract_id)
    .order_by("-history_date")
    .first()
)
assert delete_history is not None
assert delete_history.history_change_reason == "manual cleanup (deleted)"
```

This `LegacyContract` has no `is_active` field, so the example hard-deletes the
row. If the wrapped model has `is_active`, deletion is soft by default and the
stored reason ends in `(deactivated)` instead.
