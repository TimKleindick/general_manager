# Testing Guides

Use the following strategies to test managers, permissions, and GraphQL APIs effectively.

## Unit tests

- Test manager behaviour in isolation by instantiating them with factories and calling methods such as `update`, `delete`, and computed properties.
- Evaluate rules by asserting that invalid payloads raise `ValidationError`.

## Permission tests

- Use the same user fixtures as your application. Check that unauthorised users receive `PermissionError`.
- For GraphQL, execute mutations with different users and assert that the `errors` field reflects permission denials.

## GraphQL integration tests

- Use `general_manager.utils.testing.GeneralManagerTransactionTestCase` to set up a test client with schema and user authentication.
- Create data with factories or manager methods in the test database.
- Snapshot responses when the schema is stable to detect unintended changes.
- Verify pagination metadata (`pageInfo`) when you change bucket filters.

## Startup hooks in tests

- `GeneralManagerTransactionTestCase` runs registered startup hooks for the classes in `general_manager_classes` during `setUp`.
- If you need to run hooks directly (for a subset of interfaces), call `general_manager.utils.testing.run_registered_startup_hooks`.

Example:

```python
from general_manager.utils.testing import run_registered_startup_hooks

run_registered_startup_hooks(interfaces=[MyManager.Interface])
```

or for managers:

```python
from general_manager.utils.testing import run_registered_startup_hooks

run_registered_startup_hooks(managers=[MyManager])
```

## Performance tests

- Populate data with factories and measure query times using Django's `assertNumQueries` context manager.
- Profile cached calculations by toggling the cache backend between local memory and Redis.

## Continuous integration

Add `python -m pytest` to your CI pipeline. Include coverage measurement to ensure critical code paths, such as permissions and calculations, remain tested as the project evolves.
