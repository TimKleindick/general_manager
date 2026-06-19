"""Tests for GraphQL property warm-up execution helpers."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_warmup import (
    enqueue_graphql_recipe_warmup,
    enqueue_graphql_warmup,
    refresh_due_graphql_warmup_recipes,
    warmable_graphql_properties,
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
    """Timeout-backed warm-up manager used by executor tests."""

    calls = 0

    def __init__(self, id: int) -> None:
        """Store identification for recipe reconstruction."""
        self.identification = {"id": id}
        self.id = id

    def __str__(self) -> str:
        """Return a deterministic representation for cache-key generation."""
        return f"WarmUpObject(**{{'id': {self.id}}})"

    @classmethod
    def all(cls) -> list["WarmUpObject"]:
        """Return warm-up candidates for all-entry enumeration."""
        return [cls(1), cls(2)]

    @graph_ql_property(cache="timeout", timeout=300, warm_up=True)
    def score(self) -> int:
        """Return a computed score and count evaluations."""
        type(self).calls += 1
        return self.id * 10

    class Interface:
        """Expose the warmable GraphQL property for tests."""

        @staticmethod
        def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
            """Return GraphQL properties declared on the test manager."""
            return {"score": WarmUpObject.score}


class DependencyWarmUpObject:
    """Dependency-backed warm-up manager used by recipe tests."""

    calls = 0

    def __init__(self, id: int) -> None:
        """Store identification for dependency recipe reconstruction."""
        self.identification = {"id": id}
        self.id = id

    @classmethod
    def all(cls) -> list["DependencyWarmUpObject"]:
        """Return one dependency-backed warm-up candidate."""
        return [cls(1)]

    @graph_ql_property(cache="dependency", warm_up=True)
    def score(self) -> int:
        """Return a dependency-backed score and count evaluations."""
        type(self).calls += 1
        return self.id * 100

    class Interface:
        """Expose dependency-backed warmable properties for tests."""

        @staticmethod
        def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
            """Return GraphQL properties declared on the test manager."""
            return {"score": DependencyWarmUpObject.score}


class FailingWarmUpObject:
    """Warm-up manager whose property raises during evaluation."""

    def __init__(self, id: int) -> None:
        """Store identification for failure logging."""
        self.identification = {"id": id}
        self.id = id

    @classmethod
    def all(cls) -> list["FailingWarmUpObject"]:
        """Return one failing warm-up candidate."""
        return [cls(1)]

    @graph_ql_property(cache="timeout", timeout=300, warm_up=True)
    def score(self) -> int:
        """Raise to exercise warm-up failure isolation."""
        raise RuntimeError("boom")

    class Interface:
        """Expose the failing warmable property for tests."""

        @staticmethod
        def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
            """Return GraphQL properties declared on the test manager."""
            return {"score": FailingWarmUpObject.score}


class NoInterfaceWarmUpObject:
    """Manager-like object without a GraphQL Interface."""

    pass


class GraphQLWarmUpExecutorTests(SimpleTestCase):
    """Verify all-entry warm-up, recipe warm-up, and enqueue behavior."""

    def setUp(self) -> None:
        """Reset cache state and evaluation counters before each test."""
        cache.clear()
        WarmUpObject.calls = 0
        DependencyWarmUpObject.calls = 0

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_executes_property_for_each_all_entry_and_records_recipes(
        self,
    ) -> None:
        """Warm-up enumerates all candidates and records recipes."""
        summary = warm_up_graphql_properties([WarmUpObject])

        self.assertEqual(summary.evaluated, 2)
        self.assertEqual(WarmUpObject.calls, 2)
        self.assertEqual(len(graphql_warmup_recipe_keys()), 2)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_recipe_reconstructs_instance_and_executes_property(self) -> None:
        """Recipe warm-up reconstructs timeout-backed manager instances."""
        warm_up_graphql_properties([WarmUpObject])
        cache_key = graphql_warmup_recipe_keys()[0]
        WarmUpObject.calls = 0

        warmed = warm_up_graphql_recipe(cache_key)

        self.assertTrue(warmed)
        self.assertEqual(WarmUpObject.calls, 1)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_refresh_due_timeout_recipes_updates_refresh_schedule(self) -> None:
        """Due timeout recipes refresh once and leave the due index."""
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

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": False})
    def test_disabled_warm_up_entry_points_skip_work(self) -> None:
        """The global gate disables all framework-owned warm-up entry points."""
        summary = warm_up_graphql_properties([WarmUpObject])

        self.assertEqual(summary.evaluated, 0)
        self.assertEqual(WarmUpObject.calls, 0)
        self.assertFalse(warm_up_graphql_recipe("missing"))
        self.assertEqual(refresh_due_graphql_warmup_recipes(), 0)
        self.assertFalse(enqueue_graphql_warmup([WarmUpObject]))
        self.assertFalse(enqueue_graphql_recipe_warmup(["cache-key"]))

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warmable_properties_handles_missing_interface_and_filters(self) -> None:
        """Warmable discovery skips managers and properties that do not qualify."""
        self.assertEqual(warmable_graphql_properties(NoInterfaceWarmUpObject), {})
        self.assertEqual(warmable_graphql_properties(WarmUpObject, ["missing"]), {})

        summary = warm_up_graphql_properties([WarmUpObject], property_names=["missing"])

        self.assertEqual(summary.evaluated, 0)
        self.assertEqual(WarmUpObject.calls, 0)

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_WARMUP_ENABLED": True,
            "GRAPHQL_WARMUP_BATCH_SIZE": "invalid",
        }
    )
    def test_warm_up_discovers_registered_managers_when_none_are_provided(self) -> None:
        """Warm-up uses the GraphQL registry when manager classes are omitted."""
        with patch.dict(
            GraphQL.manager_registry,
            {"WarmUpObject": WarmUpObject},
            clear=True,
        ):
            summary = warm_up_graphql_properties()

        self.assertEqual(summary.evaluated, 2)
        self.assertEqual(WarmUpObject.calls, 2)

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_WARMUP_ENABLED": True,
            "GRAPHQL_WARMUP_BATCH_SIZE": 1,
            "GRAPHQL_WARMUP_WARNING_ITEMS_PER_MANAGER": 1,
            "GRAPHQL_WARMUP_TIMEOUT_REFRESH_RATIO": "invalid",
        }
    )
    def test_warm_up_batches_and_warns_when_manager_crosses_threshold(self) -> None:
        """Warm-up batches candidates and logs when enumeration crosses a threshold."""
        with patch("general_manager.api.graphql_warmup.logger.warning") as warning:
            summary = warm_up_graphql_properties([WarmUpObject])

        self.assertEqual(summary.evaluated, 2)
        self.assertEqual(summary.recipes, 2)
        warning.assert_called_once()

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_records_no_recipe_for_local_manager_path(self) -> None:
        """Local manager classes are evaluated but not persisted as recipes."""

        class LocalWarmUpObject:
            """Local manager whose import path cannot be reconstructed."""

            calls = 0

            def __init__(self, id: int) -> None:
                """Store identification for local warm-up."""
                self.identification = {"id": id}
                self.id = id

            @classmethod
            def all(cls) -> list["LocalWarmUpObject"]:
                """Return one local warm-up candidate."""
                return [cls(1)]

            @graph_ql_property(cache="timeout", timeout=300, warm_up=True)
            def score(self) -> int:
                """Return a score and count local evaluations."""
                type(self).calls += 1
                return self.id

            class Interface:
                """Expose the local warmable property for tests."""

                @staticmethod
                def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
                    """Return GraphQL properties declared on the local manager."""
                    return {"score": LocalWarmUpObject.score}

        summary = warm_up_graphql_properties([LocalWarmUpObject])

        self.assertEqual(summary.evaluated, 1)
        self.assertEqual(summary.recipes, 0)
        self.assertEqual(LocalWarmUpObject.calls, 1)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_records_failures_and_continues(self) -> None:
        """Warm-up logs property failures and continues processing."""
        with patch("general_manager.api.graphql_warmup.logger.exception") as log:
            summary = warm_up_graphql_properties([FailingWarmUpObject])

        self.assertEqual(summary.evaluated, 0)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.recipes, 0)
        log.assert_called_once()

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_dependency_recipe_reconstructs_and_executes_property(self) -> None:
        """Dependency recipes re-run the descriptor path when re-warmed."""
        warm_up_graphql_properties([DependencyWarmUpObject])
        cache_key = graphql_warmup_recipe_keys()[0]
        DependencyWarmUpObject.calls = 0

        warmed = warm_up_graphql_recipe(cache_key)

        self.assertTrue(warmed)
        self.assertEqual(DependencyWarmUpObject.calls, 1)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_recipe_returns_false_for_missing_recipe_or_lock(self) -> None:
        """Recipe warm-up skips missing recipes and lock contention."""
        self.assertFalse(warm_up_graphql_recipe("missing"))

        warm_up_graphql_properties([WarmUpObject])
        cache_key = graphql_warmup_recipe_keys()[0]
        with patch(
            "general_manager.api.graphql_warmup.acquire_graphql_warmup_recipe_lock",
            return_value=None,
        ):
            self.assertFalse(warm_up_graphql_recipe(cache_key))

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_recipe_logs_and_returns_false_for_bad_recipe(self) -> None:
        """Recipe warm-up logs reconstruction failures and reports no work."""
        warm_up_graphql_properties([WarmUpObject])
        cache_key = graphql_warmup_recipe_keys()[0]
        recipe = get_graphql_warmup_recipe(cache_key)
        assert recipe is not None
        register_graphql_warmup_recipe(replace(recipe, manager_path="missing.Manager"))

        with patch("general_manager.api.graphql_warmup.logger.exception") as log:
            warmed = warm_up_graphql_recipe(cache_key)

        self.assertFalse(warmed)
        log.assert_called_once()

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_WARMUP_ENABLED": True,
            "GRAPHQL_WARMUP_REWARM_AFTER_INVALIDATION": False,
        }
    )
    def test_enqueue_recipe_warmup_respects_rewarm_setting_and_empty_keys(self) -> None:
        """Recipe enqueueing obeys the rewarm setting and ignores empty work."""
        self.assertFalse(enqueue_graphql_recipe_warmup(["cache-key"]))

        with override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True}):
            self.assertFalse(enqueue_graphql_recipe_warmup([]))

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_enqueue_entry_points_delegate_to_task_dispatchers(self) -> None:
        """Warm-up enqueue entry points delegate to task dispatch adapters."""
        with patch(
            "general_manager.api.graphql_warmup_tasks.dispatch_graphql_warmup",
            return_value=True,
        ) as dispatch_warmup:
            self.assertTrue(enqueue_graphql_warmup([WarmUpObject]))
        dispatch_warmup.assert_called_once_with([WarmUpObject])

        with patch(
            "general_manager.api.graphql_warmup_tasks.dispatch_graphql_recipe_warmup",
            return_value=True,
        ) as dispatch_recipe:
            self.assertTrue(enqueue_graphql_recipe_warmup(["a", "a", "b"]))
        dispatch_recipe.assert_called_once_with(("a", "b"))
