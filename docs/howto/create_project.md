# Create a Project Scaffold

This tutorial sets up a Django project that uses GeneralManager.

## Step 1: Start a Django project

```bash
django-admin startproject gm_demo
cd gm_demo
python manage.py startapp core
```

## Step 2: Install dependencies

```bash
pip install GeneralManager
```

Add `general_manager` to `INSTALLED_APPS`.

## Step 3: Configure settings

Update `settings.py`:

```python
INSTALLED_APPS = [
    ...,
    "general_manager",
    "core",
]

```

Point the default cache backend to Redis or local memory depending on your environment.

## Step 4: Create the first manager

Define a manager in `core/managers.py`:

```python
from django.db.models import CharField
from general_manager.interface.database_interface import DatabaseInterface
from general_manager.manager import GeneralManager

class Customer(GeneralManager):
    name: str

    class Interface(DatabaseInterface):
        name = CharField(max_length=120)
```

Import `core.managers` in `core/apps.py` so Django discovers the interface during startup.

## Step 5: Run migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

## Step 6: Verify

Open the Django shell and create a manager instance:

```python
from core.managers import Customer
Customer.create(name="First customer")
```

You now have a working project ready for more complex managers.
