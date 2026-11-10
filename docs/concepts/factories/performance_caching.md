# Performance and Caching

Factories generate many objects quickly—use them wisely to keep tests fast and deterministic.

## AutoFactory shortcuts

`general_manager.factory.auto_factory.AutoFactory` inspects interface metadata to populate sensible defaults. It derives values for regular fields, handles special fields via `handle_custom_fields()`, and assigns many-to-many relations after saved creates. Build calls return unsaved models and do not write many-to-many relations.

For the Project from the [quickstart](../../quickstart.md), start with its
already generated factory:

```python
from projects.managers import Project

project = Project.Factory.create(name="Apollo")
```

Create strategy saves the model and returns its manager wrapper. It is fixture
creation, not an authorized application write; build strategy returns an unsaved
Django model instead.

## Batch creation

When you need multiple objects, use `Factory.create_batch()` and wrap the call in `django.db.transaction.atomic()` to reduce database overhead. AutoFactory automatically fills many-to-many relations after creation; `Factory.build()` skips those writes because the returned model is unsaved.

## Caching interactions

Factories interact with the dependency tracker when they call manager APIs. A local-memory cache is useful for isolated tests but still caches results;
it does not disable caching. Choose `cache="none"` on the operation or a
suitable test cache configuration when reuse itself is unwanted. For stress tests, enable Redis so you can measure invalidation throughput.

## Reusing fixtures

Store common factory setups in `conftest.py` or dedicated fixture modules. Combine factories with buckets to eagerly load related managers and avoid N+1 queries in tests.

## Profiling tips

- Use pytest's `--durations` flag to identify slow tests and refactor overly complex factory graphs.
- Mock external services inside factory hooks to avoid network overhead.
- Keep measurement-heavy factories lean by reusing precomputed values when possible.
