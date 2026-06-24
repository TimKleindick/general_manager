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

`GeneralManagerTransactionTestCase` subclasses Graphene-Django's transaction
test case and prepares GeneralManager state around each test class. Set
`general_manager_classes` to the managers under test. During class setup the
harness resets the GraphQL registry, optionally installs a fallback app lookup
using `fallback_app`, creates missing model/history tables for the declared
manager interfaces, initializes GeneralManager/GraphQL/remote API registrations,
and records created tables for teardown. During `setUp` it installs a
`LoggingCache` as the default cache and runs registered startup hooks for the
declared managers.

Use `fallback_app = None` when the test should not patch Django's containing app
lookup. Otherwise `create_fallback_get_app(fallback_app)` returns a lookup
function that first asks Django for the containing app config and then falls back
to `global_apps.get_app_config(fallback_app)`. If neither lookup succeeds it
returns `None`.

The test case also exposes cache assertions:

- `assert_cache_miss()` expects a default-cache miss followed by a cache write,
  then clears the operation log.
- `assert_cache_hit()` expects a default-cache hit and no cached-value write,
  ignoring dependency-coordination cache keys, then clears the operation log.
- `cache_ops()` returns the recorded cache operations.
- `reset_cache_ops()` clears the operation log.

## Startup hooks in tests

- `GeneralManagerTransactionTestCase` runs registered startup hooks for the classes in `general_manager_classes` during `setUp`.
- If you need to run hooks directly (for a subset of interfaces), call `general_manager.utils.testing.run_registered_startup_hooks`.

`run_registered_startup_hooks(managers=..., interfaces=...)` accepts manager
classes, interface classes, or both. Manager inputs contribute their nested
`Interface` class when it subclasses `InterfaceBase`; non-interface values are
ignored. Duplicate interfaces are skipped, capabilities are initialized before
hooks run, and hooks execute grouped by dependency resolver with dependency
ordering applied inside each group. The function returns the collected interface
classes in collection order. Hook exceptions and dependency-cycle errors
propagate to the caller.

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
