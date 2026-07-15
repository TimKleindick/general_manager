# Keep ORM writes and history on one database

Writable `DatabaseInterface` and `ExistingModelInterface` managers apply an
ordinary `create()` or `update()` as one transaction: the main row, history
actor and reason, and every `<relation>_id_list` change either commit together
or roll back together. The transaction uses the interface's configured database
alias.

## 1. Configure the database alias

Declare the alias in Django settings, then select it on the interface:

```python
# settings.py
DATABASES = {
    "default": {...},
    "archive": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "archive",
        "USER": "archive_app",
        "PASSWORD": "...",
        "HOST": "db.example.com",
    },
}
```

```python
# customers/managers.py
from general_manager.interface import ExistingModelInterface
from general_manager.manager import GeneralManager
from customers.models import LegacyCustomer


class Customer(GeneralManager):
    class Interface(ExistingModelInterface):
        model = LegacyCustomer
        database = "archive"
```

The same `database = "archive"` declaration applies to a nested
`DatabaseInterface` when GeneralManager owns the generated model.

## 2. Prepare history tracking

When the legacy model has no simple-history tracker, GeneralManager registers a
database-aware tracker during manager creation and includes local many-to-many
fields automatically.

When the legacy model already declares `HistoricalRecords`, GeneralManager
cannot replace its connected signal receivers. For a non-default alias, the
existing tracker must be declared with the current database-aware records class
before the manager is defined:

```python
# customers/models.py
from django.db import models

from general_manager.interface.utils.history import DatabaseAwareHistoricalRecords


class LegacyCustomer(models.Model):
    name = models.CharField(max_length=120)
    owners = models.ManyToManyField("auth.User", blank=True)
    history = DatabaseAwareHistoricalRecords(m2m_fields=["owners"])
```

!!! warning "Compatibility surface"

    `DatabaseAwareHistoricalRecords` is the configuration helper required by
    GeneralManager 0.63.1 for a pre-tracked model on a non-default alias, but it
    is not registered in `general_manager.interface`'s stable public export
    registry. Pin and test the GeneralManager version when using this current
    implementation path. A normal `HistoricalRecords` tracker remains accepted
    on the default alias.

If a pre-tracked model lacks the database-aware marker, manager class creation
raises `UnsafeHistoryConfigurationError` with the model, interface, and alias in
the message. That exception also currently has no stable package-level import;
let Django surface it as a startup configuration failure instead of importing
it for application control flow.

## 3. Write the row and relations together

```python
customer = Customer.create(
    creator_id=request.user.id,
    history_comment="created from onboarding",
    name="Acme",
    owners_id_list=[request.user.id],
)

customer.update(
    creator_id=request.user.id,
    history_comment="transferred ownership",
    owners_id_list=[new_owner.id],
)
```

Validation, saving, history-reason, or many-to-many failures propagate. For
ordinary ORM writes, any such exception rolls back the row, history rows, and
relation table on the configured alias. Upload-aware writes keep their separate
atomic upload and post-commit finalization contract.

## 4. Handle caller-owned rollbacks

GeneralManager avoids putting an uncommitted ORM row into its run-scoped read
cache while the configured connection is inside an application `atomic()`
block. After a rollback, construct a fresh manager from its ID. A manager object
updated in place may still hold values materialized inside the failed
transaction.

```python
from django.db import transaction

customer_id = customer.identification["id"]
try:
    with transaction.atomic(using="archive"):
        customer.update(name="Temporary")
        raise RuntimeError("cancel change")
except RuntimeError:
    customer = Customer(id=customer_id)

assert customer.name == "Acme"
```

See the [ORM interface model](../concepts/interfaces/db_based_interface.md), the
[existing-model history model](../concepts/interfaces/existing_model_interface.md),
the [cookbook recipe](../examples/existing_model_interface.md#route-atomic-writes-and-history-to-another-database),
and the [Core](../api/core.md) and [Interface](../api/interface.md) references.
