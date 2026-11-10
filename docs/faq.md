# Frequently Asked Questions

## How do managers relate to Django models?

Managers declare fields using type hints. The nested interface class defines the Django model fields and handles ORM interactions. When a manager is instantiated, it wraps the interface and exposes typed attributes. Django's migration commands create and evolve the table; the interface maintains
the runtime connection to it. See [migration guidance](howto/migrations_gotchas.md).

## How do I make the Django User model work with GeneralManager?

Wrap `settings.AUTH_USER_MODEL` with `ExistingModelInterface` from a manager-focused module such as `myapp/managers.py`. Keep the Django model in `models.py`, define the GeneralManager wrapper as `User` in `managers.py`, and import `myapp.managers.User` in shells and application code. GeneralManager auto-imports `<app>.managers` modules during startup, so the wrapper registers early enough for foreign-key relations to resolve back to the manager. See [Connect a Custom User Model](howto/custom_user_model.md) for the full setup, and the [Existing model interface recipes](examples/existing_model_interface.md) for a shorter snippet.

## Where should I register managers so Django finds the models?

Put managers in `<app>.managers` when possible. GeneralManager auto-imports that module during startup. If you split managers across several modules, import them from the installed
app's `managers.py` or `managers/__init__.py`. This keeps discovery in the
framework's normal startup phase.

## How can I override permissions for a one-time maintenance task?

The write methods `create`, `update`, and `delete` accept
`ignore_permission=True`. It bypasses their permission gate, not validation.
Restrict such calls to trusted application maintenance code; a Django management
command has no logged-in request user or automatic staff-only access check.

```python
Project.create(ignore_permission=True, creator_id=system_user.id, name="Legacy")
```

## Can I access the underlying Django model instance?

For ORM interfaces, `Project.Interface._model` is the generated or wrapped
Django **model class**; it is not a model instance and is not named `.model`.
Use normal manager reads for application logic. Advanced integrations can query
that model class with the manager's identification, but direct ORM access does
not provide the complete manager permission, dependency, history, and event
lifecycle. Data migrations instead need Django's historical
`apps.get_model()` registry. See [migration guidance](howto/migrations_gotchas.md#historical-data).

## How do I add custom validation beyond rules?

Attach a predicate `Rule` through the ORM interface's `Meta.rules`; the normal
write path runs it alongside Django model validation. `Interface.clean()` is
not a general extension hook. When wrapping an existing Django model, its own
`clean()` participates in `full_clean()`. Put orchestration involving several
writes in an application service with an explicit transaction, and raise
Django `ValidationError` for safe field or domain failures. See
[validation rules](howto/write_validation_rules.md) and
[atomic writes](howto/orm_atomic_writes.md).

## What is the recommended way to test managers?

Use a manager's generated factory, such as `Project.Factory`, or your own
fixtures to create test data. Write tests around buckets and GraphQL resolvers, not only the Django model layer. See [Testing guides](howto/testing_guides.md) for detailed patterns.
