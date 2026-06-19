from dataclasses import replace
from datetime import timedelta

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from general_manager.api.graphql_warmup import (
    refresh_due_graphql_warmup_recipes,
    warm_up_graphql_properties,
    warm_up_graphql_recipe,
)
from general_manager.api.graphql_warmup_registry import (
    due_timeout_graphql_warmup_recipe_keys,
    get_graphql_warmup_recipe,
    graphql_warmup_recipe_keys,
    register_graphql_warmup_recipe,
)
from general_manager.api.property import GraphQLProperty, graph_ql_property


class WarmUpObject:
    calls = 0

    def __init__(self, id: int) -> None:
        self.identification = {"id": id}
        self.id = id

    def __str__(self) -> str:
        return f"WarmUpObject(**{{'id': {self.id}}})"

    @classmethod
    def all(cls) -> list["WarmUpObject"]:
        return [cls(1), cls(2)]

    @graph_ql_property(cache="timeout", timeout=300, warm_up=True)
    def score(self) -> int:
        type(self).calls += 1
        return self.id * 10

    class Interface:
        @staticmethod
        def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
            return {"score": WarmUpObject.score}


class GraphQLWarmUpExecutorTests(SimpleTestCase):
    def setUp(self) -> None:
        cache.clear()
        WarmUpObject.calls = 0

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_executes_property_for_each_all_entry_and_records_recipes(
        self,
    ) -> None:
        summary = warm_up_graphql_properties([WarmUpObject])

        self.assertEqual(summary.evaluated, 2)
        self.assertEqual(WarmUpObject.calls, 2)
        self.assertEqual(len(graphql_warmup_recipe_keys()), 2)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_recipe_reconstructs_instance_and_executes_property(self) -> None:
        warm_up_graphql_properties([WarmUpObject])
        cache_key = graphql_warmup_recipe_keys()[0]
        WarmUpObject.calls = 0

        warmed = warm_up_graphql_recipe(cache_key)

        self.assertTrue(warmed)
        self.assertEqual(WarmUpObject.calls, 1)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_refresh_due_timeout_recipes_updates_refresh_schedule(self) -> None:
        warm_up_graphql_properties([WarmUpObject])
        cache_key = graphql_warmup_recipe_keys()[0]
        recipe = get_graphql_warmup_recipe(cache_key)
        assert recipe is not None
        register_graphql_warmup_recipe(
            replace(recipe, refresh_at=timezone.now() - timedelta(seconds=1))
        )
        WarmUpObject.calls = 0

        refreshed = refresh_due_graphql_warmup_recipes()

        self.assertEqual(refreshed, 1)
        self.assertEqual(WarmUpObject.calls, 1)
        self.assertEqual(due_timeout_graphql_warmup_recipe_keys(), ())
