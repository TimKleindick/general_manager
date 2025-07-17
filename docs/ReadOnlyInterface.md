# ReadOnlyInterface

`ReadOnlyInterface` is intended for static data that should be stored in the database but never modified through the framework. Instances of a manager using this interface read their information from JSON provided on the manager class and the interface keeps the corresponding table in sync.

## Basic usage

A manager class must define the data as a list or JSON string on the `_data` attribute. The interface describes the Django model fields just like `DatabaseInterface`.

```python
from django.db.models import CharField
from general_manager.interface.readOnlyInterface import ReadOnlyInterface
from general_manager.manager import GeneralManager

class Country(GeneralManager):
    _data = [
        {"code": "US", "name": "United States"},
        {"code": "DE", "name": "Germany"},
    ]

    class Interface(ReadOnlyInterface):
        code = CharField(max_length=2, unique=True)
        name = CharField(max_length=50)
```

During start-up the framework checks that the database schema matches the interface. When the application runs the data is automatically synchronized with the table. New entries are created, changed rows are updated and obsolete ones are deactivated.

```python
# trigger synchroniation manually if needed
Country.Interface.syncData()
```

The interface exposes only read methods. Objects cannot be created or updated through the manager. Filtering works as with `DatabaseInterface`:

```python
all_countries = Country.all()
english_speaking = Country.filter(code__in=["US", "GB"])
```
