# Performance and Caching

Factories generate many objects quickly—use them wisely to keep tests fast and deterministic.

## AutoFactory shortcuts

`general_manager.factory.auto_factory.AutoFactory` inspects interface metadata to populate sensible defaults. It derives values for regular fields, handles special fields via `handle_custom_fields()`, and assigns many-to-many relations after saved creates. Build calls return unsaved models and do not write many-to-many relations.

```python
class ProjectFactory(AutoFactory):
    interface = Project.Interface

project = ProjectFactory()
```

## Batch creation

When you need multiple objects, use `Factory.create_batch()` and wrap the call in `django.db.transaction.atomic()` to reduce database overhead. AutoFactory automatically fills many-to-many relations after creation; `Factory.build()` skips those writes because the returned model is unsaved.

## Caching interactions

Factories interact with the dependency tracker when they call manager APIs. In test suites, disable expensive caches by configuring Django's cache backend to use the local memory backend. For stress tests, enable Redis so you can measure invalidation throughput.

## Reusing fixtures

Store common factory setups in `conftest.py` or dedicated fixture modules. Combine factories with buckets to eagerly load related managers and avoid N+1 queries in tests.

## Profiling tips

- Use pytest's `--durations` flag to identify slow tests and refactor overly complex factory graphs.
- Mock external services inside factory hooks to avoid network overhead.
- Keep measurement-heavy factories lean by reusing precomputed values when possible.
