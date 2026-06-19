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


class GraphQLWarmUpRegistryTests(SimpleTestCase):
    def setUp(self) -> None:
        cache.clear()

    def test_registers_and_reads_recipe_by_cache_key(self) -> None:
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
        cache.set(RECIPE_INDEX_KEY, ["not", "a", "frozenset"])

        self.assertEqual(graphql_warmup_recipe_keys(), ())
