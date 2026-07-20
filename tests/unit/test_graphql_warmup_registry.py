"""Tests for the cache-backed GraphQL warm-up recipe registry."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import pickle

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
        self.timeouts: list[tuple[str, int | None]] = []

    def add(self, key: str, value: object, timeout: int | None = None) -> bool:
        """Add a value only when the key is absent."""
        self.events.append(("add", key))
        self.timeouts.append((key, timeout))
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

    def test_round_trips_current_and_historical_recipe_search_dates(self) -> None:
        """Recipe persistence retains the snapshot needed by a future worker."""
        snapshot = datetime(2022, 1, 1, tzinfo=UTC)
        current = GraphQLWarmUpRecipe(
            cache_key="current",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 1},
            cache="dependency",
            timeout=None,
            refresh_at=None,
            search_date=None,
        )
        historical = GraphQLWarmUpRecipe(
            cache_key="historical",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 1},
            cache="dependency",
            timeout=None,
            refresh_at=None,
            search_date=snapshot,
        )

        register_graphql_warmup_recipe(current)
        register_graphql_warmup_recipe(historical)

        self.assertEqual(get_graphql_warmup_recipe("current"), current)
        self.assertEqual(get_graphql_warmup_recipe("historical"), historical)

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

    def test_register_overwrites_recipe_and_removes_stale_timeout_index(self) -> None:
        """Re-registering a key replaces the payload and reconciles indexes."""
        timeout_recipe = GraphQLWarmUpRecipe(
            cache_key="same",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 1},
            cache="timeout",
            timeout=300,
            refresh_at=datetime(2026, 6, 19, tzinfo=UTC),
        )
        dependency_recipe = GraphQLWarmUpRecipe(
            cache_key="same",
            manager_path="tests.unit.test_graphql_warmup_registry.Manager",
            property_name="score",
            identification={"id": 2},
            cache="dependency",
            timeout=None,
            refresh_at=None,
        )

        register_graphql_warmup_recipe(timeout_recipe)
        register_graphql_warmup_recipe(dependency_recipe)

        self.assertEqual(get_graphql_warmup_recipe("same"), dependency_recipe)
        self.assertEqual(graphql_warmup_recipe_keys(), ("same",))
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
            version=1,
        )

        register_graphql_warmup_recipe(recipe)

        self.assertIsNone(get_graphql_warmup_recipe("stale"))

    def test_ignores_pickled_version_one_recipe_with_legacy_slot_state(self) -> None:
        """Adding recipe fields cannot shift the old version slot."""
        from general_manager.api import graphql_warmup_registry as registry

        @dataclass(frozen=True, slots=True)
        class LegacyGraphQLWarmUpRecipe:
            cache_key: str
            manager_path: str
            property_name: str
            identification: dict[str, object]
            cache: str
            timeout: int | None
            refresh_at: datetime | None
            version: int = 1

        LegacyGraphQLWarmUpRecipe.__module__ = registry.__name__
        LegacyGraphQLWarmUpRecipe.__name__ = "GraphQLWarmUpRecipe"
        LegacyGraphQLWarmUpRecipe.__qualname__ = "GraphQLWarmUpRecipe"
        current_recipe_class = registry.GraphQLWarmUpRecipe
        registry.GraphQLWarmUpRecipe = LegacyGraphQLWarmUpRecipe  # type: ignore[assignment]
        try:
            payload = pickle.dumps(
                LegacyGraphQLWarmUpRecipe(
                    cache_key="legacy",
                    manager_path=("tests.unit.test_graphql_warmup_registry.Manager"),
                    property_name="score",
                    identification={"id": 1},
                    cache="dependency",
                    timeout=None,
                    refresh_at=None,
                )
            )
        finally:
            registry.GraphQLWarmUpRecipe = current_recipe_class

        legacy_recipe = pickle.loads(payload)  # noqa: S301
        self.assertIsInstance(legacy_recipe, GraphQLWarmUpRecipe)
        backend = RecordingCacheBackend()
        backend.store["general_manager:graphql_warmup:recipe:legacy"] = legacy_recipe

        self.assertIsNone(get_graphql_warmup_recipe("legacy", cache_backend=backend))

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

    def test_due_timeout_recipes_orders_by_refresh_time_then_cache_key(self) -> None:
        """Due timeout keys are ordered deterministically."""
        now = datetime(2026, 6, 19, tzinfo=UTC)
        recipes = (
            GraphQLWarmUpRecipe(
                cache_key="b",
                manager_path="tests.unit.test_graphql_warmup_registry.Manager",
                property_name="score",
                identification={"id": 1},
                cache="timeout",
                timeout=300,
                refresh_at=now - timedelta(seconds=1),
            ),
            GraphQLWarmUpRecipe(
                cache_key="a",
                manager_path="tests.unit.test_graphql_warmup_registry.Manager",
                property_name="score",
                identification={"id": 2},
                cache="timeout",
                timeout=300,
                refresh_at=now - timedelta(seconds=1),
            ),
            GraphQLWarmUpRecipe(
                cache_key="older",
                manager_path="tests.unit.test_graphql_warmup_registry.Manager",
                property_name="score",
                identification={"id": 3},
                cache="timeout",
                timeout=300,
                refresh_at=now - timedelta(seconds=5),
            ),
        )
        for recipe in recipes:
            register_graphql_warmup_recipe(recipe)

        self.assertEqual(
            due_timeout_graphql_warmup_recipe_keys(now=now),
            ("older", "a", "b"),
        )
        self.assertEqual(
            due_timeout_graphql_warmup_recipe_keys(now=now, limit=0),
            (),
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

    def test_recipe_lock_timeout_is_cache_ttl(self) -> None:
        """The public timeout argument is passed to cache.add as the TTL."""
        cache_backend = RecordingCacheBackend()

        lock = acquire_graphql_warmup_recipe_lock(
            "ttl",
            timeout=12,
            cache_backend=cache_backend,
        )

        self.assertIsNotNone(lock)
        self.assertIn(
            ("add", "general_manager:graphql_warmup:lock:ttl"),
            cache_backend.events,
        )
        self.assertIn(
            ("general_manager:graphql_warmup:lock:ttl", 12),
            cache_backend.timeouts,
        )

    def test_invalid_index_payload_reads_as_empty(self) -> None:
        """Malformed index payloads are treated as empty indexes."""
        cache.set(RECIPE_INDEX_KEY, ["not", "a", "frozenset"])

        self.assertEqual(graphql_warmup_recipe_keys(), ())

    def test_invalid_index_members_are_discarded(self) -> None:
        """Malformed index members are ignored before callers sort keys."""
        cache.set(RECIPE_INDEX_KEY, frozenset(("valid-key", 42)))

        self.assertEqual(graphql_warmup_recipe_keys(), ("valid-key",))

    def test_recipe_keys_include_stale_index_members(self) -> None:
        """The main index listing does not validate backing recipe payloads."""
        cache.set(RECIPE_INDEX_KEY, frozenset(("missing",)))

        self.assertEqual(graphql_warmup_recipe_keys(), ("missing",))

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
