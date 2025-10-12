# Installation

Install GeneralManager and its runtime dependencies in a Django project. The package depends on Django, graphene-like GraphQL tooling, and Pint for unit conversions.

## Requirements

- Python 3.12 or newer
- Django 4.2 or newer
- PostgreSQL or another database supported by Django (JSON fields are recommended for metadata)
- Optional: Redis for cross-process cache invalidation when you use Celery workers

## Install the package

```bash
pip install GeneralManager
```

If you manage dependencies with Poetry, add it with `poetry add GeneralManager`.

## Configure Django

1. Add the app to `INSTALLED_APPS`:
   ```python
   INSTALLED_APPS = [
       ...,
       "general_manager",
   ]
   ```
2. Ensure `TIME_ZONE`, `USE_TZ`, and database connection settings are correct. Managers rely on timezone-aware timestamps.
3. Configure Django caches. The default local memory cache works for development. For production, configure a shared backend so cache invalidation reaches all processes:
   ```python
   CACHES = {
       "default": {
           "BACKEND": "django.core.cache.backends.redis.RedisCache",
           "LOCATION": "redis://127.0.0.1:6379/1",
       }
   }
   ```
4. Install and configure your GraphQL stack. GeneralManager ships with schema helper for Graphene. Choose the integration that matches your project and follow the instructions in [GraphQL integration](concepts/graphql/index.md).

## Database migrations

Each DatabaseInterface can generate Django models. Include the auto-generated models module so Django picks it up:

```python
# apps/materials/apps.py
from django.apps import AppConfig

class MaterialsConfig(AppConfig):
    name = "apps.materials"

    def ready(self) -> None:
        import apps.materials.managers  # noqa: F401
```

Run the standard migration commands:

```bash
python manage.py makemigrations
python manage.py migrate
```

## Verification

After installing, execute the test suite to confirm everything works with your environment:

```bash
python -m pytest
```

If you encounter import errors or missing migrations, revisit the `INSTALLED_APPS` and module import steps above.
