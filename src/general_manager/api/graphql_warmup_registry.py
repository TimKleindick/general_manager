"""Cache-backed recipe registry for GraphQL property warm-up."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
import threading
import time
import uuid

from django.core.cache import cache as django_cache

GraphQLWarmUpCacheScope = Literal["dependency", "timeout"]
GraphQLWarmUpIdentification = dict[str, object]

RECIPE_VERSION = 1
KEY_PREFIX = "general_manager:graphql_warmup"
RECIPE_INDEX_KEY = f"{KEY_PREFIX}:recipes"
TIMEOUT_RECIPE_INDEX_KEY = f"{KEY_PREFIX}:timeout_recipes"
LOCK_PREFIX = f"{KEY_PREFIX}:lock"
INDEX_LOCK_PREFIX = f"{LOCK_PREFIX}:index"
DEFAULT_RECIPE_LOCK_TIMEOUT = 300
DEFAULT_INDEX_LOCK_TIMEOUT = 30
DEFAULT_INDEX_LOCK_WAIT_SECONDS = 1.0
_LOCAL_LOCKS_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.Lock] = {}


class GraphQLWarmUpCacheBackend(Protocol):
    """
    Cache backend operations required by the warm-up recipe registry.

    `get(key, default)` returns a cached object or `default`. `set(...)` and
    `delete(...)` return values are ignored. `add(key, value, timeout)` must
    return `True` only when it stored the value because the key was absent, and
    `False` on contention.
    """

    def get(self, key: str, default: object = None) -> object: ...

    def set(self, key: str, value: object, timeout: int | None = None) -> object: ...

    def add(self, key: str, value: object, timeout: int | None = None) -> bool: ...

    def delete(self, key: str) -> object: ...


@dataclass(frozen=True, slots=True)
class GraphQLWarmUpRecipe:
    """
    Information required to reconstruct one warmed GraphQL property entry.

    `cache_key` is the warmed value's cache key. `manager_path`,
    `property_name`, and `identification` are reconstruction data consumed by the
    warm-up executor. Identification values only need to be serializable by the
    configured Django cache backend. `timeout` is a Django cache timeout in
    seconds and is meaningful only for `cache="timeout"`. `refresh_at` should be
    timezone-aware for timeout recipes. The registry stores naive `refresh_at`
    values without validation; due checks compare them with normal Python
    datetime rules and may raise `TypeError` if callers mix naive and aware
    values. Timeout recipes with `refresh_at=None` are not considered due.
    """

    cache_key: str
    manager_path: str
    property_name: str
    identification: GraphQLWarmUpIdentification
    cache: GraphQLWarmUpCacheScope
    timeout: int | None
    refresh_at: datetime | None
    version: int = RECIPE_VERSION


@dataclass(frozen=True, slots=True)
class GraphQLWarmUpRecipeLock:
    """
    Token proving ownership of one recipe warm-up attempt.

    `key` is the cache lock key and `token` is the value that must still be
    stored there before release deletes the lock.
    """

    key: str
    token: str


class GraphQLWarmUpRecipeLockTimeoutError(TimeoutError):
    """Raised when an index update lock cannot be acquired within the wait budget."""

    def __init__(self, lock_key: str) -> None:
        """Build an error message for a cache lock acquisition timeout."""
        super().__init__(f"Timed out acquiring GraphQL warm-up lock: {lock_key}")


def register_graphql_warmup_recipe(
    recipe: GraphQLWarmUpRecipe,
    *,
    cache_backend: GraphQLWarmUpCacheBackend = django_cache,
) -> None:
    """
    Persist one warm-up recipe and update registry indexes.

    Registering an existing `cache_key` overwrites the stored recipe. The key is
    always present in the main recipe index after registration. It is present in
    the timeout index only when `recipe.cache == "timeout"` and
    `recipe.refresh_at is not None`; otherwise any stale timeout-index reference
    for the same key is removed.

    Raises:
        GraphQLWarmUpRecipeLockTimeoutError: If an index update lock cannot be
            acquired.
        Exception: Propagates cache backend `set`, `add`, `get`, or `delete`
            failures.
    """
    cache_backend.set(_recipe_key(recipe.cache_key), recipe, None)
    _add_index_member(RECIPE_INDEX_KEY, recipe.cache_key, cache_backend=cache_backend)
    if recipe.cache == "timeout" and recipe.refresh_at is not None:
        _add_index_member(
            TIMEOUT_RECIPE_INDEX_KEY,
            recipe.cache_key,
            cache_backend=cache_backend,
        )
    else:
        _remove_index_member(
            TIMEOUT_RECIPE_INDEX_KEY,
            recipe.cache_key,
            cache_backend=cache_backend,
        )


def get_graphql_warmup_recipe(
    cache_key: str,
    *,
    cache_backend: GraphQLWarmUpCacheBackend = django_cache,
) -> GraphQLWarmUpRecipe | None:
    """
    Return the recipe for `cache_key`, if it exists and matches this version.

    Missing, malformed, and version-incompatible cache payloads return `None`
    without pruning index entries. For this reader, malformed means the cache
    payload is not a `GraphQLWarmUpRecipe` instance. Dataclass field values are
    trusted after construction; invalid field combinations may fail later when
    a caller uses the recipe.
    """
    recipe = cache_backend.get(_recipe_key(cache_key))
    if not isinstance(recipe, GraphQLWarmUpRecipe):
        return None
    if recipe.version != RECIPE_VERSION:
        return None
    return recipe


def get_graphql_warmup_recipes(
    cache_keys: Iterable[str],
    *,
    cache_backend: GraphQLWarmUpCacheBackend = django_cache,
) -> dict[str, GraphQLWarmUpRecipe]:
    """
    Return existing recipes for `cache_keys` keyed by cache key.

    Duplicate requested keys are read at most once. Missing, non-recipe, and
    version-incompatible payloads are omitted.
    """
    recipes: dict[str, GraphQLWarmUpRecipe] = {}
    for cache_key in tuple(dict.fromkeys(cache_keys)):
        recipe = get_graphql_warmup_recipe(cache_key, cache_backend=cache_backend)
        if recipe is not None:
            recipes[cache_key] = recipe
    return recipes


def graphql_warmup_recipe_keys(
    *,
    cache_backend: GraphQLWarmUpCacheBackend = django_cache,
) -> tuple[str, ...]:
    """
    Return known recipe cache keys in deterministic order.

    This reads the main index only. Stale entries whose recipe payload is missing
    or version-incompatible are not filtered here.
    """
    return tuple(sorted(_read_index(RECIPE_INDEX_KEY, cache_backend=cache_backend)))


def due_timeout_graphql_warmup_recipe_keys(
    *,
    now: datetime | None = None,
    limit: int | None = None,
    cache_backend: GraphQLWarmUpCacheBackend = django_cache,
) -> tuple[str, ...]:
    """
    Return timeout recipe keys whose refresh time has arrived.

    `now` defaults to the current UTC time. `limit=None` returns every due key;
    otherwise the non-negative limit is applied after sorting by
    `(refresh_at, cache_key)`, so `limit=0` returns an empty tuple. Entries in
    the timeout index are validated against their stored recipe: missing,
    non-recipe, version-incompatible, non-timeout, and `refresh_at=None` entries
    are removed from the timeout index and not returned. Other dataclass field
    values are trusted here. Naive datetimes are compared using normal Python
    datetime rules and may raise `TypeError` when compared with aware `now`
    values.

    Raises:
        GraphQLWarmUpRecipeLockTimeoutError: If pruning a stale timeout-index
            member cannot acquire the index lock. Index updates wait
            `DEFAULT_INDEX_LOCK_WAIT_SECONDS` seconds and store the lock with
            `DEFAULT_INDEX_LOCK_TIMEOUT` seconds of cache TTL.
        Exception: Propagates cache backend failures.
    """
    current_time = now or datetime.now(UTC)
    due: list[tuple[datetime, str]] = []
    for cache_key in _read_index(TIMEOUT_RECIPE_INDEX_KEY, cache_backend=cache_backend):
        recipe = get_graphql_warmup_recipe(cache_key, cache_backend=cache_backend)
        if recipe is None or recipe.cache != "timeout" or recipe.refresh_at is None:
            _remove_index_member(
                TIMEOUT_RECIPE_INDEX_KEY,
                cache_key,
                cache_backend=cache_backend,
            )
            continue
        if recipe.refresh_at <= current_time:
            due.append((recipe.refresh_at, cache_key))
    ordered = tuple(cache_key for _refresh_at, cache_key in sorted(due))
    if limit is None:
        return ordered
    return ordered[: max(0, limit)]


def delete_graphql_warmup_recipe(
    cache_key: str,
    *,
    cache_backend: GraphQLWarmUpCacheBackend = django_cache,
) -> None:
    """
    Remove one recipe and all index references to it.

    Missing recipes are ignored. Index references are removed idempotently.

    Raises:
        GraphQLWarmUpRecipeLockTimeoutError: If an index update lock cannot be
            acquired. Index updates wait `DEFAULT_INDEX_LOCK_WAIT_SECONDS`
            seconds and store the lock with `DEFAULT_INDEX_LOCK_TIMEOUT`
            seconds of cache TTL.
        Exception: Propagates cache backend failures.
    """
    cache_backend.delete(_recipe_key(cache_key))
    _remove_index_member(RECIPE_INDEX_KEY, cache_key, cache_backend=cache_backend)
    _remove_index_member(
        TIMEOUT_RECIPE_INDEX_KEY,
        cache_key,
        cache_backend=cache_backend,
    )


def acquire_graphql_warmup_recipe_lock(
    cache_key: str,
    *,
    timeout: int = DEFAULT_RECIPE_LOCK_TIMEOUT,
    cache_backend: GraphQLWarmUpCacheBackend = django_cache,
) -> GraphQLWarmUpRecipeLock | None:
    """
    Acquire a best-effort per-recipe execution lock.

    `timeout` is the cache lock TTL in seconds, not an acquisition wait budget.
    The function performs one cache `add(...)` attempt and returns `None` on
    contention.

    Raises:
        Exception: Propagates cache backend `add` failures.
    """
    lock_key = _lock_key(cache_key)
    token = uuid.uuid4().hex
    if not cache_backend.add(lock_key, token, timeout):
        return None
    return GraphQLWarmUpRecipeLock(key=lock_key, token=token)


def release_graphql_warmup_recipe_lock(
    lock: GraphQLWarmUpRecipeLock,
    *,
    cache_backend: GraphQLWarmUpCacheBackend = django_cache,
) -> None:
    """
    Release a recipe lock without deleting another worker's newer lock.

    Missing, expired, or token-mismatched locks are ignored. Redis-like backends
    with `client.get_client()` and `make_key(...)` use a compare-and-delete Lua
    script; other backends use a process-local mutex and token re-check before
    `delete(...)`.
    """
    _delete_lock_if_owned(lock, cache_backend=cache_backend)


def _recipe_key(cache_key: str) -> str:
    """Return the cache key used for one recipe payload."""
    return f"{KEY_PREFIX}:recipe:{cache_key}"


def _lock_key(cache_key: str) -> str:
    """Return the cache key used for one recipe lock."""
    return f"{LOCK_PREFIX}:{cache_key}"


def _read_index(
    index_key: str,
    *,
    cache_backend: GraphQLWarmUpCacheBackend,
) -> frozenset[str]:
    """Read an index payload, ignoring malformed values."""
    value = cache_backend.get(index_key, frozenset())
    if not isinstance(value, frozenset):
        return frozenset()
    return frozenset(member for member in value if isinstance(member, str))


def _write_index(
    index_key: str,
    values: Iterable[str],
    *,
    cache_backend: GraphQLWarmUpCacheBackend,
) -> None:
    """Write an index payload as an immutable set."""
    cache_backend.set(index_key, frozenset(values), None)


def _add_index_member(
    index_key: str,
    member: str,
    *,
    cache_backend: GraphQLWarmUpCacheBackend,
) -> None:
    """Add one member to an index under an index-level cache lock."""
    with _locked_index_update(index_key, cache_backend=cache_backend):
        _write_index(
            index_key,
            _read_index(index_key, cache_backend=cache_backend) | frozenset((member,)),
            cache_backend=cache_backend,
        )


def _remove_index_member(
    index_key: str,
    member: str,
    *,
    cache_backend: GraphQLWarmUpCacheBackend,
) -> None:
    """Remove one member from an index under an index-level cache lock."""
    with _locked_index_update(index_key, cache_backend=cache_backend):
        _write_index(
            index_key,
            _read_index(index_key, cache_backend=cache_backend) - frozenset((member,)),
            cache_backend=cache_backend,
        )


@contextmanager
def _locked_index_update(
    index_key: str,
    *,
    cache_backend: GraphQLWarmUpCacheBackend,
) -> Iterator[None]:
    """Acquire and release the cache lock guarding one index update."""
    lock = _acquire_cache_lock(
        f"{INDEX_LOCK_PREFIX}:{index_key}",
        timeout=DEFAULT_INDEX_LOCK_TIMEOUT,
        wait_seconds=DEFAULT_INDEX_LOCK_WAIT_SECONDS,
        cache_backend=cache_backend,
    )
    try:
        yield
    finally:
        release_graphql_warmup_recipe_lock(lock, cache_backend=cache_backend)


def _acquire_cache_lock(
    lock_key: str,
    *,
    timeout: int,
    wait_seconds: float,
    cache_backend: GraphQLWarmUpCacheBackend,
) -> GraphQLWarmUpRecipeLock:
    """Acquire a cache-backed lock, waiting briefly for contention to clear."""
    token = uuid.uuid4().hex
    deadline = time.monotonic() + wait_seconds
    while True:
        if cache_backend.add(lock_key, token, timeout):
            return GraphQLWarmUpRecipeLock(key=lock_key, token=token)
        if time.monotonic() >= deadline:
            raise GraphQLWarmUpRecipeLockTimeoutError(lock_key)
        time.sleep(0.01)


def _delete_lock_if_owned(
    lock: GraphQLWarmUpRecipeLock,
    *,
    cache_backend: GraphQLWarmUpCacheBackend,
) -> None:
    """Delete a lock only when the backend still stores the caller's token."""
    if _delete_redis_lock_if_owned(lock, cache_backend=cache_backend):
        return
    with _local_lock_for(lock.key):
        if cache_backend.get(lock.key) != lock.token:
            return
        if cache_backend.get(lock.key) != lock.token:
            return
        cache_backend.delete(lock.key)


def _delete_redis_lock_if_owned(
    lock: GraphQLWarmUpRecipeLock,
    *,
    cache_backend: GraphQLWarmUpCacheBackend,
) -> bool:
    """Use Redis compare-and-delete when the cache backend exposes a client."""
    client_holder = getattr(cache_backend, "client", None)
    get_client = getattr(client_holder, "get_client", None)
    if get_client is None:
        return False
    make_key = getattr(cache_backend, "make_key", lambda key: key)
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] "
        "then return redis.call('del', KEYS[1]) else return 0 end"
    )
    try:
        get_client(write=True).eval(script, 1, make_key(lock.key), lock.token)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return True


def _local_lock_for(lock_key: str) -> threading.Lock:
    """Return a process-local fallback mutex for non-Redis cache backends."""
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(lock_key, threading.Lock())
