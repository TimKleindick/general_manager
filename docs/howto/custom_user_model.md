# Connect a Custom User Model

This guide shows how to:

- create a custom Django `AUTH_USER_MODEL`
- wrap it with a same-name GeneralManager `User`
- expose a stable import path for shells and app code
- make other managers resolve foreign keys back to the GeneralManager wrapper

The key pattern is:

- `myapp.models.User` is the real Django ORM model
- `myapp.managers.User` is the GeneralManager wrapper
- application code should import `myapp.managers.User` when it wants GeneralManager behavior

## 1. Define the Django user model

Create the Django auth model in `models.py`.

```python
from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    nickname = models.CharField(max_length=64, blank=True)
```

If you need a lower-level auth model, `AbstractBaseUser` also works. GeneralManager's history typing supports swappable auth models based on `AbstractBaseUser`.

## 2. Point Django at the custom user model

In your Django settings:

```python
AUTH_USER_MODEL = "accounts.User"
```

Set this before creating migrations for apps that reference the user model.

## 3. Add the GeneralManager wrapper in `managers.py`

Create a manager-focused module and keep the wrapper named `User`.

```python
from __future__ import annotations

from django.conf import settings

from general_manager.interface import ExistingModelInterface
from general_manager.manager import GeneralManager


class User(GeneralManager):
    id: int
    username: str
    email: str | None
    nickname: str
    is_active: bool

    class Interface(ExistingModelInterface):
        model = settings.AUTH_USER_MODEL

    class Factory:
        username = "demo-user"
        email = "demo@example.com"
        nickname = "Demo"
```

Use `ExistingModelInterface` here because the Django model already exists and GeneralManager should wrap it instead of generating a new table.

## 4. Import the wrapper through the manager module

Use this import in shells, services, GraphQL code, and other app code when you want GeneralManager behavior:

```python
from accounts.managers import User
```

Avoid importing `accounts.models.User` when you expect GeneralManager APIs like:

- `User.create(...)`
- `User.filter(...)`
- `User.Factory.create()`

`manage.py shell` and similar tools naturally surface Django models from `models.py`, so `accounts.managers.User` should be your canonical GeneralManager import path.

## 5. Let GeneralManager register wrappers at startup

GeneralManager auto-imports `<app>.managers` during startup. The simplest project layout is:

```text
accounts/
  apps.py
  models.py
  managers.py
```

If you keep managers somewhere else, import that module from `AppConfig.ready()` yourself.

Example:

```python
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self) -> None:
        from . import managers  # noqa: F401
```

Early registration matters because GeneralManager uses the wrapped model's `_general_manager_class` link to make foreign keys resolve to manager types instead of raw Django model instances.

## 6. Reference the custom user model from other managers

Use `settings.AUTH_USER_MODEL` in Django-model fields the same way you normally would.

```python
from __future__ import annotations

from django.conf import settings
from django.db import models

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from accounts.managers import User


class Ticket(GeneralManager):
    id: int
    title: str
    owner: User

    class Interface(DatabaseInterface):
        title = models.CharField(max_length=100)
        owner = models.ForeignKey(
            settings.AUTH_USER_MODEL,
            on_delete=models.CASCADE,
            related_name="tickets",
        )
```

Once the `accounts.managers` module has been loaded, `ticket.owner` resolves to the GeneralManager `User` wrapper and not the raw Django model instance.

## 7. Understand create/auth caveats

`User.create()` uses GeneralManager's normal ORM write path. That means:

- it writes through the wrapped model
- it does not automatically switch to Django's `create_user()` helper
- password handling is your responsibility unless you add project-specific logic

If you need hashed passwords, either:

- set them through the Django model manager directly, or
- add a project-specific workflow that calls `set_password()` before save

Treat the wrapper as an orchestration layer over your auth model, not as a replacement for Django's auth-manager semantics.

## 8. Recommended test coverage

Add one focused integration test that proves:

- `AUTH_USER_MODEL` points to your custom `accounts.User`
- `accounts.managers.User` is the GeneralManager wrapper
- `accounts.models.User` and `accounts.managers.User` share the same class name but are different Python classes
- `User.filter(...)` and `User.Factory.create()` work
- another manager with `ForeignKey(settings.AUTH_USER_MODEL)` resolves the relation back to the wrapper

## Related Docs

- [Existing Model Interfaces](../concepts/interfaces/existing_model_interface.md)
- [Existing Model Interface Recipes](../examples/existing_model_interface.md)
- [FAQ](../faq.md)
