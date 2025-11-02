# Frequently Asked Questions

## How do managers relate to Django models?

Managers declare fields using type hints. The nested interface class defines the Django model fields and handles ORM interactions. When a manager is instantiated, it wraps the interface and exposes typed attributes. The interface runs migrations and maintains the link to the database table.

## How do I make the Django User model work with GeneralManager?

Wrap it with `ExistingModelInterface`. Import `django.contrib.auth.get_user_model()`, declare a manager with the fields you want to expose, and set `model = get_user_model()` on the interface. `ExistingModelInterface` registers simple-history automatically, honours `is_active`, and wires in factories so you can call `User.create()` or `User.Factory.create()` directly. See the [Existing model interface recipes](examples/existing_model_interface.md) for a full snippet.

## Where should I register managers so Django finds the models?

Import the module containing your managers inside the `AppConfig.ready()` hook or `models.py`. This ensures Django evaluates the interface definitions during startup and generates migrations.

## How can I override permissions for a one-time maintenance task?

All CRUD methods accept `ignore_permission=True`. Use it sparingly, preferably inside Django management commands guarded by staff-only access.

```python
Project.create(ignore_permission=True, creator_id=system_user.id, name="Legacy")
```

## Can I access the underlying Django model instance?

Yes. Interfaces expose a `.model` attribute. You can also retrieve the model via `manager.Interface.model` or use the primary key stored in `manager.identification` to query the ORM directly. Keep direct ORM usage read-only so that the dependency tracker remains accurate.

## How do I add custom validation beyond rules?

Rules should cover most validation scenarios. For complex workflows, override `Interface.clean()` or hook into Django signals. When validation fails, raise `ValidationError` so the error propagates to GraphQL responses.

## What is the recommended way to test managers?

Use factory classes derived from `GeneralManager.Factory` or your own fixtures to create test data. Write tests around buckets and GraphQL resolvers, not only the Django model layer. See [Testing guides](howto/testing_guides.md) for detailed patterns.
