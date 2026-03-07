# Frequently Asked Questions

## How do managers relate to Django models?

Managers declare fields using type hints. The nested interface class defines the Django model fields and handles ORM interactions. When a manager is instantiated, it wraps the interface and exposes typed attributes. The interface runs migrations and maintains the link to the database table.

## How do I make the Django User model work with GeneralManager?

Wrap `settings.AUTH_USER_MODEL` with `ExistingModelInterface` from a manager-focused module such as `myapp/managers.py`. Keep the Django model in `models.py`, define the GeneralManager wrapper as `User` in `managers.py`, and import `myapp.managers.User` in shells and application code. GeneralManager auto-imports `<app>.managers` modules during startup, so the wrapper registers early enough for foreign-key relations to resolve back to the manager. See [Connect a Custom User Model](howto/custom_user_model.md) for the full setup, and the [Existing model interface recipes](examples/existing_model_interface.md) for a shorter snippet.

## Where should I register managers so Django finds the models?

Put managers in `<app>.managers` when possible. GeneralManager auto-imports that module during startup. If you use a different module layout, import the module containing your managers inside `AppConfig.ready()` so Django evaluates the interface definitions before dependent relations are introspected.

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
