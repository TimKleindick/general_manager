"""Tests for the cache-backed GraphQL warm-up recipe registry."""

from datetime import UTC, datetime, timedelta

from django.core.cache import cache
from django.test import SimpleTestCase

from general_manager.api.graphql_warmup_registry import (
    GraphQLWarmUpRecipe,
    GraphQLWarmUpRecipeLock,
    RECIPE_INDEX_KEY,
    TIMEOUT_RECIPE_INDEX_KEY,
    acquire_graphql_warmup_recipe_lock,
    delete_graphql_warmup_recipe,
    due_timeout_graphql_warmup_recipe_keys,
    get_graphql_warmup_recipe,
    get_graphql_warmup_recipes,
    graphql_warmup_recipe_keys,
    register_graphql_warmup_recipe,
    release_graphql_warmup_recipe_lock,
)


class RecordingCacheBackend:
    """Cache backend that records calls made during index updates."""

    def __init__(self) -> None:
        """Initialize in-memory state and call history."""
        self.store: dict[str, object] = {}
        self.events: list[tuple[str, str]] = []

    def add(self, key: str, value: object, timeout: int | None = None) -> bool:
        """Add a value only when the key is absent."""
        del timeout
        self.events.append(("add", key))
        if key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key: str, default: object = None) -> object:
        """Return a stored value and record the read."""
        self.events.append(("get", key))
        return self.store.get(key, default)

    def set(self, key: str, value: object, timeout: int | None = None) -> None:
        """Store a value and record the write."""
        del timeout
        self.events.append(("set", key))
        self.store[key] = value

    def delete(self, key: str) -> None:
        """Delete a value and record the deletion."""
        self.events.append(("delete", key))
        self.store.pop(key, None)


class ReacquiredLockCacheBackend:
    """Cache backend that simulates lock expiry and reacquisition during release."""

    def __init__(self, lock: GraphQLWarmUpRecipeLock) -> None:
        """Seed the backend with the old lock token."""
        self.lock = lock
        self.store = {lock.key: lock.token}
        self.first_get = True
        self.deleted: list[str] = []

    def get(self, key: str, default: object = None) -> object:
        """Return the old token once while replacing it with a newer token."""
        if key == self.lock.key and self.first_get:
            self.first_get = False
            self.store[key] = "new-token"
            return self.lock.token
        return self.store.get(key, default)

    def delete(self, key: str) -> None:
        """Record lock deletion attempts."""
        self.deleted.append(key)
        self.store.pop(key, None)


class GraphQLWarmUpRegistryTests(SimpleTestCase):
    """Verify recipe persistence, indexes, and lock behavior."""

    def setUp(self) -> None:
        """Clear the shared cache before each registry test."""
        cache.clear()

    def test_registers_and_reads_recipe_by_cache_key(self) -> None:
        """Recipes can be registered, read back, and listed in the index."""
        recipe = GraphQLWarmUpRecipe(
            cache_key="abc",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 1},
            cache="dependency",
            timeout=None,
            refresh_at=None,
        )

        register_graphql_warmup_recipe(recipe)

        self.assertEqual(get_graphql_warmup_recipe("abc"), recipe)
        self.assertEqual(graphql_warmup_recipe_keys(), ("abc",))

    def test_due_timeout_recipes_filter_by_refresh_at(self) -> None:
        """Due timeout lookup returns only recipes whose refresh time has arrived."""
        now = datetime(2026, 6, 19, tzinfo=UTC)
        due = GraphQLWarmUpRecipe(
            cache_key="due",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 1},
            cache="timeout",
            timeout=300,
            refresh_at=now - timedelta(seconds=1),
        )
        later = GraphQLWarmUpRecipe(
            cache_key="later",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 2},
            cache="timeout",
            timeout=300,
            refresh_at=now + timedelta(seconds=60),
        )
        register_graphql_warmup_recipe(due)
        register_graphql_warmup_recipe(later)

        self.assertEqual(due_timeout_graphql_warmup_recipe_keys(now=now), ("due",))

    def test_delete_removes_recipe_from_indexes(self) -> None:
        """Deleting a recipe removes it from all registry indexes."""
        recipe = GraphQLWarmUpRecipe(
            cache_key="gone",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 1},
            cache="timeout",
            timeout=300,
            refresh_at=datetime(2026, 6, 19, tzinfo=UTC),
        )
        register_graphql_warmup_recipe(recipe)

        delete_graphql_warmup_recipe("gone")

        self.assertIsNone(get_graphql_warmup_recipe("gone"))
        self.assertEqual(graphql_warmup_recipe_keys(), ())
        self.assertEqual(due_timeout_graphql_warmup_recipe_keys(), ())

    def test_rejects_stale_recipe_versions(self) -> None:
        """Recipes from older schema versions are ignored."""
        recipe = GraphQLWarmUpRecipe(
            cache_key="stale",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 1},
            cache="dependency",
            timeout=None,
            refresh_at=None,
            version=0,
        )

        register_graphql_warmup_recipe(recipe)

        self.assertIsNone(get_graphql_warmup_recipe("stale"))

    def test_reads_existing_recipes_for_distinct_cache_keys(self) -> None:
        """Bulk recipe lookup deduplicates requested cache keys."""
        recipe = GraphQLWarmUpRecipe(
            cache_key="known",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 1},
            cache="dependency",
            timeout=None,
            refresh_at=None,
        )
        register_graphql_warmup_recipe(recipe)

        self.assertEqual(
            get_graphql_warmup_recipes(["known", "known", "missing"]),
            {"known": recipe},
        )

    def test_due_timeout_recipes_prunes_stale_index_members_and_applies_limit(
        self,
    ) -> None:
        """Due timeout lookup prunes stale index entries and honors limits."""
        now = datetime(2026, 6, 19, tzinfo=UTC)
        first = GraphQLWarmUpRecipe(
            cache_key="first",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 1},
            cache="timeout",
            timeout=300,
            refresh_at=now - timedelta(seconds=10),
        )
        second = GraphQLWarmUpRecipe(
            cache_key="second",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 2},
            cache="timeout",
            timeout=300,
            refresh_at=now - timedelta(seconds=5),
        )
        register_graphql_warmup_recipe(first)
        register_graphql_warmup_recipe(second)
        cache.set(TIMEOUT_RECIPE_INDEX_KEY, frozenset(("first", "missing", "second")))

        self.assertEqual(
            due_timeout_graphql_warmup_recipe_keys(now=now, limit=1),
            ("first",),
        )
        self.assertEqual(
            cache.get(TIMEOUT_RECIPE_INDEX_KEY),
            frozenset(("first", "second")),
        )

    def test_recipe_lock_acquire_and_release_are_token_safe(self) -> None:
        """Recipe locks cannot be released by callers with the wrong token."""
        lock = acquire_graphql_warmup_recipe_lock("locked")
        assert lock is not None

        self.assertIsNone(acquire_graphql_warmup_recipe_lock("locked"))
        release_graphql_warmup_recipe_lock(GraphQLWarmUpRecipeLock(lock.key, "wrong"))
        self.assertIsNone(acquire_graphql_warmup_recipe_lock("locked"))

        release_graphql_warmup_recipe_lock(lock)
        next_lock = acquire_graphql_warmup_recipe_lock("locked")
        self.assertIsNotNone(next_lock)
        assert next_lock is not None
        release_graphql_warmup_recipe_lock(next_lock)

    def test_invalid_index_payload_reads_as_empty(self) -> None:
        """Malformed index payloads are treated as empty indexes."""
        cache.set(RECIPE_INDEX_KEY, ["not", "a", "frozenset"])

        self.assertEqual(graphql_warmup_recipe_keys(), ())

    def test_recipe_registration_synchronizes_index_updates(self) -> None:
        """Recipe registration acquires an index lock before read-modify-write."""
        cache_backend = RecordingCacheBackend()
        recipe = GraphQLWarmUpRecipe(
            cache_key="locked-index",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 1},
            cache="dependency",
            timeout=None,
            refresh_at=None,
        )

        register_graphql_warmup_recipe(recipe, cache_backend=cache_backend)

        lock_adds = [
            key
            for event, key in cache_backend.events
            if event == "add" and ":index:" in key
        ]
        self.assertGreaterEqual(len(lock_adds), 1)

    def test_release_recipe_lock_does_not_delete_reacquired_lock(self) -> None:
        """Lock release does not delete a newer token acquired after expiry."""
        lock = GraphQLWarmUpRecipeLock(
            "general_manager:graphql_warmup:lock:race",
            "old-token",
        )
        cache_backend = ReacquiredLockCacheBackend(lock)

        release_graphql_warmup_recipe_lock(lock, cache_backend=cache_backend)

        self.assertEqual(cache_backend.store[lock.key], "new-token")
        self.assertEqual(cache_backend.deleted, [])
