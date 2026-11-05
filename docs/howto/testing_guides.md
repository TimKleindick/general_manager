# Testing Guides

Test the application behavior that matters to callers: a valid write persists,
an invalid write fails without partial changes, an unauthorized operation is
denied, and a query returns the intended data shape.

## Choose the boundary

| What you want to establish | Exercise |
| --- | --- |
| A user may create or change a record | The normal manager write with that user's `creator_id` |
| Invalid domain data cannot be saved | A manager write that violates the field or Rule constraint |
| A client sees only authorized records and fields | Generated GraphQL with the client's actual user context |
| A custom endpoint protects a Python read | That endpoint's explicit row and field permission checks |
| A calculation returns the right value | The calculation with known inputs and source data |

Use factories to arrange fixture data, then perform the operation under test
through its real boundary. Factory success does not prove write permission,
and a plain Python bucket read does not prove GraphQL authorization.

For managers already declared in an installed application, use your normal
Django application test setup and migrations. The GeneralManager-specific
harness below is useful when a test defines managers dynamically and needs
their generated models, schema registrations, and startup hooks initialized.
Start with the [quickstart](../quickstart.md) and
[permission tutorial](add_permission_rule.md) for the application these
strategies build on.

## Unit tests

- Test manager behaviour in isolation by instantiating them with factories and calling methods such as `update`, `delete`, and computed properties.
- Evaluate rules by asserting that invalid payloads raise `ValidationError`.

## Permission tests

- Use the same user fixtures as your application. Check that unauthorised users receive `PermissionError`.
- For GraphQL, execute mutations with different users and assert that top-level GraphQL `errors` reflects permission denials; it is not a mutation payload field.

## GraphQL integration tests

- Use `general_manager.utils.testing.GeneralManagerTransactionTestCase` to prepare dynamically declared managers and the GraphQL test client. Authenticate the client explicitly with the intended user.
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

- Populate data with factories and assert query counts using Django's `assertNumQueries` context manager. Use profiling to measure elapsed time.
- Profile cached calculations by toggling the cache backend between local memory and Redis.

## Continuous integration

Add `python -m pytest` to your CI pipeline. Include coverage measurement to ensure critical code paths, such as permissions and calculations, remain tested as the project evolves.
