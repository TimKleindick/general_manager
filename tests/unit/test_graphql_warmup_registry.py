from datetime import UTC, datetime, timedelta

from django.core.cache import cache
from django.test import SimpleTestCase

from general_manager.api.graphql_warmup_registry import (
    GraphQLWarmUpRecipe,
    delete_graphql_warmup_recipe,
    due_timeout_graphql_warmup_recipe_keys,
    get_graphql_warmup_recipe,
    graphql_warmup_recipe_keys,
    register_graphql_warmup_recipe,
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
