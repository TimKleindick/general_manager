# Cache Dependent Calculations

Use the caching utilities to memoise expensive calculations without sacrificing correctness.

## Step 1: Define a calculation manager

```python
from datetime import date

import graphene

from core.managers import DerivativeVolume, Project
from general_manager.api.graphql import GraphQL
from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input, graph_ql_property

class ProjectSummary(GeneralManager):
    project: Project
    date: date

    class Interface(CalculationInterface):
        project = Input(Project)
        date = Input(date)

    @graph_ql_property
    def total_volume(self) -> int:
        return sum(
            derivative.volume
            for derivative in self.project.derivative_list.filter(date=self.date)
        )
```

## Step 2: Choose a cache scope

`@graph_ql_property` methods use run-scoped caching by default on every manager type. The value is reused within one GraphQL request, calculation graph, bulk operation, or background run, then discarded.

Use either `@graph_ql_property` or `@graph_ql_property(...)`. The decorated
method must declare a return type annotation; missing annotations raise
`GraphQLPropertyReturnAnnotationError` during class definition. The annotation is
resolved on first metadata access with the resolver module globals and owner
class namespace for schema generation. If a forward reference, import cycle, or
local alias cannot be resolved, the property keeps working but caches no
resolved GraphQL type hint for that descriptor instance; recreate the class or
descriptor after correcting the annotation to resolve it. Resolution swallows
`AttributeError`, `KeyError`, `NameError`, `TypeError`, and `ValueError` from
Python type-hint evaluation and lets other exceptions propagate. A resolver
annotated as `-> None` resolves to `type(None)`, so `graphql_type_hint is None`
means resolution failed rather than "the resolver returns None".

Use dependency-aware caching only when a calculation result is stable enough to reuse across requests:

```python
@graph_ql_property(cache="dependency")
def expensive_summary(self) -> int:
    return self.project.derivative_list.filter(date=self.date).count()
```

Disable caching for cheap or intentionally volatile values:

```python
@graph_ql_property(cache="none")
def cheap_label(self) -> str:
    return f"{self.project.name}: {self.date:%Y-%m-%d}"
```

Use timeout caching when a value may be reused for a fixed interval:

```python
@graph_ql_property(cache="timeout", timeout=300)
def five_minute_summary(self) -> int:
    return self.project.derivative_list.count()
```

`cache="timeout"` requires a non-`None` `timeout`; supplying any non-`None`
`timeout` with another cache scope raises
`GraphQLPropertyTimeoutConfigurationError`. Zero, negative, boolean, float, and
other timeout values are not rejected by `graph_ql_property` itself and are
delegated to the shared cache decorator and cache backend. `warm_up=True` is
valid only with `cache="dependency"` or `cache="timeout"` and otherwise raises
`GraphQLPropertyWarmUpConfigurationError`; runtime truthiness is used for
`warm_up`, `sortable`, and `filterable` rather than coercing or validating exact
`bool` instances. Runtime cache values outside `"run"`, `"dependency"`,
`"timeout"`, and `"none"` are unsupported and propagate the shared cache
decorator's `ValueError`. Non-callable resolver inputs and unsupported decorator
forms raise ordinary Python `TypeError`s. Resolver exceptions and cache backend
errors propagate from property access. Configured decorator options are validated
when the returned decorator wraps a resolver function, not when
`graph_ql_property(...)` is called without a resolver. Passing `func=None`
explicitly is the same as omitting `func` and returns a configured decorator.
When multiple options are invalid, warm-up validation runs before timeout and
cache-decorator validation; unexpected timeouts are reported before unsupported
runtime cache values from the shared cache decorator.

`graph_ql_property` is the stable public API. The descriptor stores the metadata
used by schema generation as `sortable`, `filterable`, `query_annotation`,
`cache`, `timeout`, `warm_up`, and `graphql_type_hint`; treat those attributes as
read-only after class definition. `GraphQLPropertyReturnAnnotationError`,
`GraphQLPropertyTimeoutConfigurationError`, and
`GraphQLPropertyWarmUpConfigurationError` are public exception classes exported
from `general_manager.api`. `GraphQLProperty` itself remains an implementation
descriptor unless it appears in the public API registry.

## Step 3: Verify invalidation

For dependency-aware properties, update a derivative that contributes to the summary. The dependency tracker captures the relationship and invalidates the cache entry automatically.

```python
DerivativeVolume(id=volume_id).update(quantity=42)
```

## Step 4: Warm selected properties proactively

Set `warm_up=True` only on properties that are safe and useful to compute
outside a user request. Warm-up is valid for dependency and timeout cache scopes:

```python
@graph_ql_property(cache="dependency", warm_up=True)
def expensive_summary(self) -> int:
    return self.project.derivative_list.filter(date=self.date).count()

@graph_ql_property(cache="timeout", timeout=300, warm_up=True)
def five_minute_summary(self) -> int:
    return self.project.derivative_list.count()
```

When enabled in settings, the framework can enumerate `Manager.all()`, execute
each opted-in property, and record warm-up recipes. Dependency entries can be
re-warmed after invalidation when a recipe exists. Timeout entries can be
refreshed before expiry by the built-in Celery Beat task or by a scheduler that
calls `refresh_due_graphql_warmup_recipes` directly. Schedulers that execute
management commands can run `graphql_warmup_refresh_due` instead.

Warm-up recipes are stored through the configured Django cache backend. The
registry uses only `get`, `set`, `add`, and `delete`; custom backends passed to
the lower-level registry helpers must provide those methods. `get(key, default)`
returns a cached object or `default`, `set(...)` and `delete(...)` return values
are ignored, and `add(key, value, timeout)` must return `True` only when it
stored the value because the key was absent. Recipe identification values and
recipe payloads need to be serializable by that cache backend.

Registering a recipe overwrites the stored payload for the same `cache_key`.
Every registered key is listed by `graphql_warmup_recipe_keys()`, which reads
the index only and does not filter missing or stale payloads. Timeout recipes
also enter a timeout index only when `cache="timeout"` and `refresh_at` is set.
`due_timeout_graphql_warmup_recipe_keys()` validates timeout-index entries,
prunes missing, non-recipe, version-incompatible, non-timeout, and
`refresh_at=None` recipes, then returns due keys ordered by
`(refresh_at, cache_key)`. `limit=0` returns no keys. Other dataclass field
values are trusted after construction. `GraphQLWarmUpRecipe.timeout` is a Django
cache timeout in seconds, and `refresh_at` should be timezone-aware. The
registry stores naive `refresh_at` values without validation, but due checks use
normal Python datetime comparison and can raise `TypeError` when naive and aware
values are mixed.

Recipe locks use cache `add(...)` for a best-effort per-recipe execution lock.
`acquire_graphql_warmup_recipe_lock(timeout=...)` treats `timeout` as the lock
TTL, not a wait budget. `release_graphql_warmup_recipe_lock(...)` silently
ignores missing, expired, or token-mismatched locks so it cannot delete another
worker's newer lock. Index updates use a short cache-backed lock and may raise
`GraphQLWarmUpRecipeLockTimeoutError` if the index lock cannot be acquired.
Index updates wait `DEFAULT_INDEX_LOCK_WAIT_SECONDS` seconds and store their lock
with `DEFAULT_INDEX_LOCK_TIMEOUT` seconds of cache TTL.

Lower-level warm-up execution helpers live in
`general_manager.api.graphql_warmup`. `warm_up_graphql_properties(None)` reads
manager classes from the GraphQL manager registry in registry order; passing an
iterable processes those manager classes in the supplied order, including
duplicates. Duplicate manager classes repeat property reads and recipe attempts,
though the configured property cache can avoid recomputing the raw resolver.
`property_names` is a global allow-list applied to every manager; unknown names,
non-GraphQL descriptors, properties without `warm_up=True`, and cache scopes
outside `"dependency"` and `"timeout"` are ignored. Names are matched against
the keys returned by `Interface.get_graph_ql_properties()`. A missing
`Interface` or missing `get_graph_ql_properties()` means no warmable properties;
errors raised by `get_graph_ql_properties()` propagate, and a non-mapping return
value raises `TypeError`.

`GraphQLWarmUpSummary.evaluated` counts successful property reads, not manager
instances. `failed` counts property reads that raised and were logged while the
run continued. `recipes` counts recipe payloads built for persistence. Manager
enumeration errors, recipe construction errors, cache/registry write failures,
non-mapping instance `identification`, invalid manager surfaces, and settings
access errors propagate from all-entry warm-up. When `GRAPHQL_WARMUP_ENABLED` is
false or missing, all-entry warm-up returns a zero summary. Local or nested
manager classes are evaluated and counted, but their successful property reads
do not create recipes because workers cannot import those classes later.

`warm_up_graphql_recipe(cache_key)` returns `False` when warm-up is disabled, the
recipe is missing or incompatible, the per-recipe lock is held, the manager no
longer exposes the recipe property, or reconstruction/evaluation/persistence
fails inside the guarded attempt. Guarded failures are logged with the cache key.
Lock acquisition and release failures propagate. Dependency recipes re-run the
descriptor path inside a `CalculationRunContext`; timeout recipes refresh the
cached value directly and record a new timeout recipe. Cache key type validation
runs before the enabled-setting check, so invalid cache key types raise
`TypeError` even when warm-up is disabled.

`refresh_due_graphql_warmup_recipes(limit)` returns the number of due recipes
that refreshed successfully. `None` has no cap, positive integers cap the sorted
due-key list from the registry, and `0` or negative values refresh no recipes.
Non-integer and boolean limits raise `TypeError`. Limit validation runs before
the enabled-setting check, so invalid limits raise even when warm-up is disabled.

`enqueue_graphql_warmup(...)` and `enqueue_graphql_recipe_warmup(...)` are
convenience wrappers around the task adapter. A `True` result means the adapter
accepted the enqueue request; it does not mean background work has completed.
Recipe enqueueing removes duplicate keys while preserving first-seen order,
returns `False` when warm-up or re-warm-after-invalidation is disabled, and
raises `TypeError` for a single string or non-string cache key entries. The
settings gates are checked before recipe keys are consumed, so disabled
enqueueing returns `False` without validating keys. When enabled, key iterables
are consumed once before dispatch and iterator errors propagate. Unexpected task
adapter exceptions propagate; enqueue failures handled by the adapter are
returned as `False`.

Warm-up can be expensive because it starts from `.all()`. Keep automatic startup
warm-up disabled unless the deployment has a worker or startup budget for it,
and monitor warning logs for large manager enumerations.

```python
GENERAL_MANAGER = {
    "GRAPHQL_WARMUP_ENABLED": True,
    "GRAPHQL_WARMUP_STARTUP_ENABLED": True,
    "GRAPHQL_WARMUP_STARTUP_MODE": "enqueue",
    "GRAPHQL_WARMUP_BEAT_ENABLED": True,
    "GRAPHQL_WARMUP_BEAT_INTERVAL_SECONDS": 60,
}
```

The optional Celery task adapter is exposed from
`general_manager.api.graphql_warmup_tasks`. Call
`configure_graphql_warmup_beat_schedule_from_settings()` during Celery app
startup when the deployment wants GeneralManager to install the periodic
due-timeout refresh task. The helper reads either nested `GENERAL_MANAGER`
settings or top-level Django settings; nested keys take precedence when both are
present. It returns `True` after writing or replacing the Beat entry and `False`
when Beat is disabled, Celery is not importable, or the module-level Celery
`current_app` is `None`. Existing Beat schedule mappings are preserved and the
GeneralManager entry is replaced in place; malformed non-mapping schedule values
are treated as an empty schedule. The entry uses task
`general_manager.api.graphql_warmup_tasks.refresh_due_graphql_warmup_recipes_task`,
a float seconds schedule, no args or kwargs, and `options={"queue":
"graphql.warmup"}`. Celery configuration access and assignment errors are not
swallowed. Boolean settings accept normal truthiness plus string false values
`""`, `"0"`, `"false"`, `"no"`, `"off"`, `"none"`, and `"null"`, and string
true values `"1"`, `"true"`, `"yes"`, and `"on"`; unrecognized non-empty strings
are treated as enabled.

```python
from celery import Celery
from general_manager.api.graphql_warmup_tasks import (
    configure_graphql_warmup_beat_schedule_from_settings,
)

app = Celery("project")

@app.on_after_configure.connect
def configure_graphql_warmup(sender, **kwargs):
    configure_graphql_warmup_beat_schedule_from_settings()
```

The task functions can also be queued directly:

```python
from general_manager.api.graphql_warmup_tasks import (
    dispatch_graphql_recipe_warmup,
    dispatch_graphql_warmup,
)

dispatch_graphql_warmup()
dispatch_graphql_recipe_warmup(["cache-key-a", "cache-key-b"])
```

`dispatch_graphql_warmup(None)` enqueues a worker task that warms every
registered manager. Passing manager classes limits the run to importable classes;
local and nested classes are skipped because workers cannot import them.
Duplicate manager import paths are removed while preserving first-seen order.
The function returns `False` when Celery is unavailable, the supplied iterable is
empty, all supplied managers were skipped, or enqueueing failed. Non-class
entries raise `TypeError` before enqueueing. Iteration errors from the supplied
iterable propagate.

`dispatch_graphql_recipe_warmup(...)` removes duplicate cache keys while
preserving order, returns `False` for empty work or enqueue failures, and
otherwise queues `warm_up_graphql_recipes_task`. Passing a single string instead
of an iterable of cache key strings raises `TypeError`, as do non-string
entries. Iteration errors from the supplied iterable propagate.

`warm_up_graphql_properties_task(manager_paths)` resolves dotted manager paths,
then returns a dictionary with `evaluated`, `failed`, and `recipes` counts.
Import errors and executor errors propagate to Celery. `manager_paths=None`
means all registered managers; an empty list means no selected managers.
`manager_paths` must be `None` or a list of strings. The returned dictionary has
only the `evaluated`, `failed`, and `recipes` keys.

`warm_up_graphql_recipes_task(cache_keys)` requires a list of strings, attempts
keys in order, logs per-key failures with the cache key, continues after failed
keys, and returns the number that reported a successful refresh.
`refresh_due_graphql_warmup_recipes_task(limit)` delegates to the timeout recipe
registry; `None` means no cap, and `0` or a negative value refreshes no recipes.
Non-integer limits and boolean limits raise `TypeError`.

Applications that use another scheduler can keep Beat disabled and run:

```bash
python manage.py graphql_warmup
python manage.py graphql_warmup_refresh_due --limit 1000
```

`graphql_warmup_refresh_due` refreshes only timeout-backed recipes whose
scheduled refresh time is due. Omitting `--limit` refreshes every due recipe, a
positive integer caps the sorted due-key list, and `0` or a negative value
delegates to the executor and refreshes none. Command-line values are parsed as
integers by Django's argument parser; the option default is `None` and its help
text is `Maximum number of due recipes to refresh.` Programmatic
`call_command(...)` use must pass `limit=None` or a non-boolean integer;
invalid values raise before the refresh executor is called with
`CommandError` with the message `GraphQL warm-up refresh limit must be an
integer or omitted.` Registry, lock, and recipe refresh failures from the
executor are not wrapped by the command. Successful runs print a styled success
line, `GraphQL warm-up refreshed <count> recipe(s).`, using `recipe` only when
the refreshed count is exactly `1`.

## Step 5: Monitor cache usage

Enable Django cache logging or use Redis monitoring tools to ensure cache hits increase and invalidations behave as expected.

## Working-set reuse

For bulk-style calculations, build a run-scoped index directly from the source
bucket. The framework derives a stable run-local key from the bucket and key
spec, so repeated lookups in the same `CalculationRunContext` reuse the same
index without application-specific cache keys. The run-cache identity includes
the bucket object, key spec, whether the lookup is unique or multi-row, and
`max_rows`; equivalent bucket instances do not share an index entry. Indexes are
not invalidated inside the same calculation run if dependencies later change.
Start a new `CalculationRunContext` to observe newly loaded or invalidated
source data. Calling `index_by(...)` or `index_many(...)` outside an active run
creates a temporary run for that one call, so separate calls do not reuse an
index unless you wrap them in a shared `CalculationRunContext`. Cache hits return
the same mutable dictionary object stored for the run; treat it as read-only
because mutating it changes later same-run hits. Failed index construction does
not store a cache entry, so later same-run calls retry the build. Validation
order is `max_rows`, then `key_spec`, then bucket iteration. During iteration,
the row guardrail is checked before key resolution for each row, so guardrail
errors take precedence over duplicate, missing-field, or unhashable-key errors
on the first row past the limit.

The stable public API is `Bucket.index_by(...)`, `Bucket.index_many(...)`, and
the exported bucket-index exception classes. The lower-level
`general_manager.bucket.indexing` helpers are importable support functions, not
documented public API exports. The exception types are public; constructor
arguments and message text are diagnostic details rather than stable inspection
APIs. Internal named exception subclasses used to implement those base error
types are not part of the public export contract unless they appear in the
public API registry.

```python
@graph_ql_property
def volume(self) -> int:
    rows = DerivativeVolume.filter(
        derivative=self.derivative,
        revision=self.revision,
        search_date=self.search_date,
    )
    rows_by_date = rows.index_by("volume_date")
    return rows_by_date[self.target_date].quantity
```

Use `index_many("field_name")` when more than one row can share a key:

```python
@graph_ql_property
def daily_quantities(self) -> dict[date, int]:
    rows = DerivativeVolume.filter(
        derivative=self.derivative,
        revision=self.revision,
        search_date=self.search_date,
    )
    rows_by_date = rows.index_many("volume_date")
    return {
        volume_date: sum(row.quantity for row in volume_rows)
        for volume_date, volume_rows in rows_by_date.items()
    }
```

`index_by(...)` raises `DuplicateBucketIndexKeyError` when duplicate keys are
found. Empty buckets return `{}`. Composite keys use tuples of field names, for
example `rows.index_by(("project", "date"))`. `None` is a valid key value;
missing fields raise `MissingBucketIndexKeyError`, unsupported key specs raise
`UnsupportedBucketIndexKeySpecError`, unhashable keys raise
`UnhashableBucketIndexKeyError`, and indexes that exceed their row guardrail
raise `BucketIndexTooLargeError`. The guardrail defaults to 1000 rows; pass
`max_rows=None` only for trusted bounded inputs, or a larger integer when the
expected input size is known. `max_rows=0` or a negative value fails as soon as
any row is read. Runtime callers must pass an integer or `None`; booleans,
strings, floats, and other values raise `TypeError`.
Field names are resolved with attribute lookup (`getattr`) only. Empty string
field names are accepted and passed through like any other attribute name.
Mapping keys are not used unless the row also exposes them as attributes. Any
`AttributeError` from `getattr`, including one raised inside a descriptor or
property getter, is reported as `MissingBucketIndexKeyError`. Exceptions raised
while iterating the source bucket propagate unchanged. Non-`AttributeError`
exceptions raised by descriptors or property getters also propagate unchanged.

Use returned keys as lookup identities, not as a stable serialized format.
Already hashable scalar values are preserved, managers are keyed by class and
sorted identification pairs, dictionaries become sorted tuples of frozen
key/value pairs using a string sort key so mixed comparable types can be ordered,
and dictionary entries with equal string sort keys keep the source mapping's
iteration order because Python sorting is stable. Lists and tuples become
tuples, sets become frozensets, and arbitrary hashable objects, including
frozensets and hashable dataclass instances, are returned unchanged. Subclasses
of `dict`, `list`, `tuple`, and `set` follow their parent container behavior.
Other custom mapping and sequence implementations are treated as ordinary
objects rather than containers. Nested supported containers are recursively
converted into hashable identities for the current Python process. Errors raised
while reading a `GeneralManager` instance's identification or iterating its
`.items()` propagate unchanged. Malformed non-mapping identification values fail
through their normal Python errors. Unhashable identification contents raise
`UnhashableBucketIndexKeyError` when freezing reaches them.

Use `ensure_calculation_run_context` around custom bulk jobs or background tasks
that should share the same run cache but may already execute inside a GraphQL
request context.

Most application code should use the bucket methods directly. Lower-level
helpers can inspect `current_calculation_run_context` when they need to adapt
to an already active run.
