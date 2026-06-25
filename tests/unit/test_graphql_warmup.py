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
    graphql_warmup_enabled,
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


class AlternateWarmUpObject:
    """Second timeout-backed manager for shared property-name filtering tests."""

    calls = 0

    def __init__(self, id: int) -> None:
        """Store identification for recipe reconstruction."""
        self.identification = {"id": id}
        self.id = id

    def __str__(self) -> str:
        """Return a deterministic representation for cache-key generation."""
        return f"AlternateWarmUpObject(**{{'id': {self.id}}})"

    @classmethod
    def all(cls) -> list["AlternateWarmUpObject"]:
        """Return one warm-up candidate."""
        return [cls(1)]

    @graph_ql_property(cache="timeout", timeout=300, warm_up=True)
    def score(self) -> int:
        """Return a computed score and count evaluations."""
        type(self).calls += 1
        return self.id * 20

    class Interface:
        """Expose the warmable GraphQL property for tests."""

        @staticmethod
        def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
            """Return GraphQL properties declared on the test manager."""
            return {"score": AlternateWarmUpObject.score}


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


class NoGetterInterfaceWarmUpObject:
    """Manager-like object whose Interface lacks a property getter."""

    @classmethod
    def all(cls) -> list["NoGetterInterfaceWarmUpObject"]:
        """Return no warm-up candidates."""
        return []

    class Interface:
        """Interface without get_graph_ql_properties."""


class BrokenInterfaceWarmUpObject:
    """Manager-like object whose Interface raises during property discovery."""

    @classmethod
    def all(cls) -> list["BrokenInterfaceWarmUpObject"]:
        """Return no warm-up candidates."""
        return []

    class Interface:
        """Interface with a broken property getter."""

        @staticmethod
        def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
            """Raise to exercise discovery error propagation."""
            raise RuntimeError


class MalformedInterfaceWarmUpObject:
    """Manager-like object whose Interface returns a non-mapping registry."""

    @classmethod
    def all(cls) -> list["MalformedInterfaceWarmUpObject"]:
        """Return no warm-up candidates."""
        return []

    class Interface:
        """Interface with malformed property metadata."""

        @staticmethod
        def get_graph_ql_properties() -> list[object]:
            """Return malformed property metadata."""
            return []


class RegistryNameWarmUpObject:
    """Manager-like object whose property registry key differs from the attribute."""

    @classmethod
    def all(cls) -> list["RegistryNameWarmUpObject"]:
        """Return no warm-up candidates."""
        return []

    @graph_ql_property(cache="timeout", timeout=300, warm_up=True)
    def score(self) -> int:
        """Return a score for registry-name filtering tests."""
        return 1

    class Interface:
        """Expose a GraphQL property under a custom registry key."""

        @staticmethod
        def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
            """Return GraphQL properties by registry key."""
            return {"registeredScore": RegistryNameWarmUpObject.score}


class InvalidIdentificationWarmUpObject:
    """Warm-up manager with an invalid recipe identification payload."""

    identification = "not-a-mapping"

    @classmethod
    def all(cls) -> list["InvalidIdentificationWarmUpObject"]:
        """Return one warm-up candidate with invalid identification."""
        return [cls()]

    @graph_ql_property(cache="timeout", timeout=300, warm_up=True)
    def score(self) -> int:
        """Return a score so recipe creation is reached."""
        return 1

    class Interface:
        """Expose the warmable GraphQL property for invalid identification tests."""

        @staticmethod
        def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
            """Return GraphQL properties declared on the test manager."""
            return {"score": InvalidIdentificationWarmUpObject.score}


class NonStringIdentificationKeyWarmUpObject(InvalidIdentificationWarmUpObject):
    """Warm-up manager with non-string identification mapping keys."""

    def __init__(self) -> None:
        """Store invalid non-string identification keys."""
        self.identification = {1: "not-a-kwarg"}

    @classmethod
    def all(cls) -> list["NonStringIdentificationKeyWarmUpObject"]:
        """Return one warm-up candidate with invalid identification keys."""
        return [cls()]

    class Interface:
        """Expose the warmable GraphQL property for invalid key tests."""

        @staticmethod
        def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
            """Return GraphQL properties declared on the test manager."""
            return {"score": NonStringIdentificationKeyWarmUpObject.score}


class GraphQLWarmUpExecutorTests(SimpleTestCase):
    """Verify all-entry warm-up, recipe warm-up, and enqueue behavior."""

    def setUp(self) -> None:
        """Reset cache state and evaluation counters before each test."""
        cache.clear()
        WarmUpObject.calls = 0
        DependencyWarmUpObject.calls = 0
        AlternateWarmUpObject.calls = 0

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
    def test_warm_up_reuses_generator_property_names_for_each_manager(self) -> None:
        names = (name for name in ["score"])

        summary = warm_up_graphql_properties(
            [WarmUpObject, AlternateWarmUpObject],
            property_names=names,
        )

        self.assertEqual(summary.evaluated, 3)
        self.assertEqual(WarmUpObject.calls, 2)
        self.assertEqual(AlternateWarmUpObject.calls, 1)

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
        self.assertEqual(warmable_graphql_properties(NoGetterInterfaceWarmUpObject), {})
        self.assertEqual(warmable_graphql_properties(WarmUpObject, ["missing"]), {})

        summary = warm_up_graphql_properties([WarmUpObject], property_names=["missing"])

        self.assertEqual(summary.evaluated, 0)
        self.assertEqual(WarmUpObject.calls, 0)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warmable_properties_propagates_broken_interface_getter(self) -> None:
        """Errors raised by property discovery propagate."""
        with self.assertRaises(RuntimeError):
            warmable_graphql_properties(BrokenInterfaceWarmUpObject)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warmable_properties_rejects_malformed_property_registry(self) -> None:
        """Property discovery requires a mapping return value."""
        with self.assertRaises(TypeError):
            warmable_graphql_properties(MalformedInterfaceWarmUpObject)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warmable_property_names_match_registry_keys(self) -> None:
        """Property filtering uses get_graph_ql_properties mapping keys."""
        self.assertEqual(
            tuple(
                warmable_graphql_properties(
                    RegistryNameWarmUpObject,
                    ["registeredScore"],
                )
            ),
            ("registeredScore",),
        )
        self.assertEqual(
            warmable_graphql_properties(RegistryNameWarmUpObject, ["score"]),
            {},
        )

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

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_processes_duplicate_manager_classes_in_order(self) -> None:
        """Duplicate manager classes repeat property reads in input order."""
        summary = warm_up_graphql_properties([WarmUpObject, WarmUpObject])

        self.assertEqual(summary.evaluated, 4)
        self.assertEqual(WarmUpObject.calls, 2)
        self.assertEqual(summary.recipes, 4)

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
            summary = warm_up_graphql_properties([FailingWarmUpObject, WarmUpObject])

        self.assertEqual(summary.evaluated, 2)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.recipes, 2)
        self.assertEqual(WarmUpObject.calls, 2)
        log.assert_called_once()

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_propagates_recipe_registration_errors(self) -> None:
        """Registry write failures propagate from all-entry warm-up."""
        with patch(
            "general_manager.api.graphql_warmup.register_graphql_warmup_recipe",
            side_effect=RuntimeError("cache down"),
        ):
            with self.assertRaises(RuntimeError):
                warm_up_graphql_properties([WarmUpObject])

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_rejects_non_mapping_identification(self) -> None:
        """Invalid instance identification raises a deliberate TypeError."""
        with self.assertRaises(TypeError):
            warm_up_graphql_properties([InvalidIdentificationWarmUpObject])

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_warm_up_rejects_non_string_identification_keys(self) -> None:
        """Recipe identification keys must be valid kwargs."""
        with self.assertRaises(TypeError):
            warm_up_graphql_properties([NonStringIdentificationKeyWarmUpObject])

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
    def test_warm_up_recipe_rejects_non_string_cache_key(self) -> None:
        """Recipe warm-up rejects invalid cache key types."""
        with self.assertRaises(TypeError):
            warm_up_graphql_recipe(object())

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": False})
    def test_warm_up_recipe_validates_cache_key_before_disabled_gate(self) -> None:
        """Recipe warm-up validates cache keys before checking enabled state."""
        with self.assertRaises(TypeError):
            warm_up_graphql_recipe(object())

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

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_refresh_due_graphql_warmup_recipes_limit_zero_refreshes_none(self) -> None:
        """A zero due-refresh limit returns no work even when recipes are due."""
        warm_up_graphql_properties([WarmUpObject])
        cache_key = graphql_warmup_recipe_keys()[0]
        recipe = get_graphql_warmup_recipe(cache_key)
        assert recipe is not None
        register_graphql_warmup_recipe(
            replace(recipe, refresh_at=timezone.now() - timedelta(seconds=1))
        )
        WarmUpObject.calls = 0

        self.assertEqual(refresh_due_graphql_warmup_recipes(limit=0), 0)
        self.assertEqual(WarmUpObject.calls, 0)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_refresh_due_graphql_warmup_recipes_rejects_invalid_limits(self) -> None:
        """Due-refresh rejects non-integer and boolean limits."""
        for invalid_limit in ("1", 1.5, False):
            with self.subTest(limit=invalid_limit):
                with self.assertRaises(TypeError):
                    refresh_due_graphql_warmup_recipes(limit=invalid_limit)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": False})
    def test_refresh_due_graphql_warmup_recipes_validates_limit_before_disabled_gate(
        self,
    ) -> None:
        """Due-refresh validates limits before checking enabled state."""
        with self.assertRaises(TypeError):
            refresh_due_graphql_warmup_recipes(limit=True)

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

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": False})
    def test_enqueue_recipe_warmup_does_not_consume_keys_when_disabled(self) -> None:
        """Recipe enqueueing checks settings before validating cache keys."""
        self.assertFalse(enqueue_graphql_recipe_warmup("abc"))

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_enqueue_recipe_warmup_rejects_invalid_cache_keys(self) -> None:
        """Recipe enqueueing rejects strings and non-string key entries."""
        with self.assertRaises(TypeError):
            enqueue_graphql_recipe_warmup("abc")

        with self.assertRaises(TypeError):
            enqueue_graphql_recipe_warmup(["a", object()])

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_enqueue_recipe_warmup_propagates_iterator_errors(self) -> None:
        """Recipe enqueueing propagates errors raised while consuming keys."""

        def broken_keys():
            """Yield one key and then fail."""
            yield "a"
            raise RuntimeError

        with self.assertRaises(RuntimeError):
            enqueue_graphql_recipe_warmup(broken_keys())

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

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_ENABLED": True})
    def test_enqueue_warmup_propagates_dispatcher_exceptions(self) -> None:
        """Unexpected task adapter errors propagate from enqueue wrappers."""
        with patch(
            "general_manager.api.graphql_warmup_tasks.dispatch_graphql_warmup",
            side_effect=RuntimeError("adapter failed"),
        ):
            with self.assertRaises(RuntimeError):
                enqueue_graphql_warmup([WarmUpObject])

    @override_settings(GENERAL_MANAGER={})
    def test_graphql_warmup_enabled_defaults_false(self) -> None:
        """Missing warm-up enabled setting defaults to false."""
        self.assertFalse(graphql_warmup_enabled())
