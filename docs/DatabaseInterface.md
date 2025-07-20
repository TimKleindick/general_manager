# DatabaseInterface

`DatabaseInterface` connects a manager with a Django model and provides create, update and deactivate operations. It is the most common interface for working with persistent data.

## Basic usage

Define the model fields inside the interface class. Managers inherit automatic CRUD methods from `GeneralManager` that delegate to the interface.

```python
from django.contrib.auth.models import User
from django.db.models import CharField, ForeignKey, ManyToManyField
from django.db import models

from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.manager import GeneralManager

class Book(GeneralManager):
    title: str
    author: User

    class Interface(DatabaseInterface):
        title = CharField(max_length=50)
        author = ForeignKey(User, on_delete=models.CASCADE)
        readers = ManyToManyField(User, blank=True)
```

Creating a new instance automatically validates the model and records the user who made the change:

```python
book = Book.create(
    creator_id=request.user.id,
    history_comment="initial import",
    title="My Book",
    author=request.user,
)
```

Updating or deactivating an existing manager works the same way:

```python
manager = Book(existing_book_id)
manager.update(creator_id=user.id, title="Updated")
manager.deactivate(creator_id=user.id, history_comment="outdated")
```

Many-to-many fields are passed using the `<field>_id_list` convention. The interface handles sorting these values and applying them after saving the instance.

GraphQL properties defined on the manager can be marked as `filterable` or `sortable`. If a property also defines a `query_annotation`, DatabaseBucket will annotate the queryset so filtering and ordering happen in the database. Without an annotation the property value is evaluated in Python which may be slower for large result sets.

## Rules

`DatabaseInterface` supports rule validation. Rules allow you to validate incoming data before it is saved. See [Rule Validation](Rules.md) for more details.
