"""Tests for GraphQL warm-up Celery task adapters."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from general_manager.api.graphql_warmup_tasks import (
    GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY,
    configure_graphql_warmup_beat_schedule_from_settings,
    dispatch_graphql_recipe_warmup,
    dispatch_graphql_warmup,
    refresh_due_graphql_warmup_recipes_task,
    warm_up_graphql_properties_task,
    warm_up_graphql_recipes_task,
)


class GraphQLWarmUpBeatScheduleTests(SimpleTestCase):
    """Verify optional Celery Beat schedule configuration."""

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_WARMUP_BEAT_ENABLED": True,
            "GRAPHQL_WARMUP_BEAT_INTERVAL_SECONDS": 13,
        }
    )
    def test_configure_graphql_warmup_beat_schedule_registers_task(self) -> None:
        """Beat configuration registers the due-timeout refresh task."""
        fake_conf = SimpleNamespace(beat_schedule={})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch("general_manager.api.graphql_warmup_tasks.current_app", fake_app),
        ):
            configured = configure_graphql_warmup_beat_schedule_from_settings()

        self.assertTrue(configured)
        entry = fake_conf.beat_schedule[GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY]
        self.assertEqual(
            entry["task"],
            "general_manager.api.graphql_warmup_tasks."
            "refresh_due_graphql_warmup_recipes_task",
        )
        self.assertEqual(entry["schedule"], 13.0)
        self.assertEqual(entry["options"], {"queue": "graphql.warmup"})

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_WARMUP_BEAT_ENABLED": True,
            "GRAPHQL_WARMUP_BEAT_INTERVAL_SECONDS": 0,
        }
    )
    def test_configure_graphql_warmup_beat_schedule_uses_default_for_zero(
        self,
    ) -> None:
        """Non-positive beat intervals fall back to the documented default."""
        fake_conf = SimpleNamespace(beat_schedule={})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch("general_manager.api.graphql_warmup_tasks.current_app", fake_app),
        ):
            configure_graphql_warmup_beat_schedule_from_settings()

        entry = fake_conf.beat_schedule[GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY]
        self.assertEqual(entry["schedule"], 60.0)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_BEAT_ENABLED": True})
    def test_configure_graphql_warmup_beat_schedule_preserves_existing_entries(
        self,
    ) -> None:
        """Beat configuration preserves unrelated existing schedule entries."""
        existing_entry = {"task": "project.tasks.cleanup"}
        fake_conf = SimpleNamespace(beat_schedule={"project.cleanup": existing_entry})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch("general_manager.api.graphql_warmup_tasks.current_app", fake_app),
        ):
            configured = configure_graphql_warmup_beat_schedule_from_settings()

        self.assertTrue(configured)
        self.assertEqual(fake_conf.beat_schedule["project.cleanup"], existing_entry)
        self.assertIn(GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY, fake_conf.beat_schedule)

    @override_settings(
        GRAPHQL_WARMUP_BEAT_ENABLED=True,
        GENERAL_MANAGER={"GRAPHQL_WARMUP_BEAT_ENABLED": False},
    )
    def test_configure_graphql_warmup_beat_schedule_nested_settings_win(
        self,
    ) -> None:
        """Nested GeneralManager settings take precedence over top-level keys."""
        fake_conf = SimpleNamespace(beat_schedule={})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch("general_manager.api.graphql_warmup_tasks.current_app", fake_app),
        ):
            configured = configure_graphql_warmup_beat_schedule_from_settings()

        self.assertFalse(configured)
        self.assertEqual(fake_conf.beat_schedule, {})

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_BEAT_ENABLED": True})
    def test_configure_graphql_warmup_beat_schedule_replaces_malformed_schedule(
        self,
    ) -> None:
        """Non-mapping Beat schedule values are replaced with a fresh mapping."""
        fake_conf = SimpleNamespace(beat_schedule="not-a-schedule")
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch("general_manager.api.graphql_warmup_tasks.current_app", fake_app),
        ):
            configured = configure_graphql_warmup_beat_schedule_from_settings()

        self.assertTrue(configured)
        self.assertEqual(
            tuple(fake_conf.beat_schedule),
            (GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY,),
        )

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_BEAT_ENABLED": False})
    def test_configure_graphql_warmup_beat_schedule_skips_when_disabled(self) -> None:
        """Beat configuration is skipped when disabled in settings."""
        fake_conf = SimpleNamespace(beat_schedule={})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch("general_manager.api.graphql_warmup_tasks.current_app", fake_app),
        ):
            configured = configure_graphql_warmup_beat_schedule_from_settings()

        self.assertFalse(configured)
        self.assertEqual(fake_conf.beat_schedule, {})

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_BEAT_ENABLED": "false"})
    def test_configure_graphql_warmup_beat_schedule_treats_false_string_as_disabled(
        self,
    ) -> None:
        """String false values are parsed as disabled settings."""
        fake_conf = SimpleNamespace(beat_schedule={})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch("general_manager.api.graphql_warmup_tasks.current_app", fake_app),
        ):
            configured = configure_graphql_warmup_beat_schedule_from_settings()

        self.assertFalse(configured)
        self.assertEqual(fake_conf.beat_schedule, {})

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_BEAT_ENABLED": "yes"})
    def test_configure_graphql_warmup_beat_schedule_treats_true_string_as_enabled(
        self,
    ) -> None:
        """String true values are parsed as enabled settings."""
        fake_conf = SimpleNamespace(beat_schedule={})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch("general_manager.api.graphql_warmup_tasks.current_app", fake_app),
        ):
            configured = configure_graphql_warmup_beat_schedule_from_settings()

        self.assertTrue(configured)
        self.assertIn(GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY, fake_conf.beat_schedule)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_BEAT_ENABLED": "maybe"})
    def test_configure_graphql_warmup_beat_schedule_treats_unknown_string_as_enabled(
        self,
    ) -> None:
        """Unrecognized non-empty strings use normal truthiness."""
        fake_conf = SimpleNamespace(beat_schedule={})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch("general_manager.api.graphql_warmup_tasks.current_app", fake_app),
        ):
            configured = configure_graphql_warmup_beat_schedule_from_settings()

        self.assertTrue(configured)
        self.assertIn(GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY, fake_conf.beat_schedule)

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_BEAT_ENABLED": True})
    def test_configure_graphql_warmup_beat_schedule_skips_without_celery(self) -> None:
        """Beat configuration is skipped when Celery is unavailable."""
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", False),
            patch("general_manager.api.graphql_warmup_tasks.logger.warning") as warning,
        ):
            configured = configure_graphql_warmup_beat_schedule_from_settings()

        self.assertFalse(configured)
        warning.assert_called_once()

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_WARMUP_BEAT_ENABLED": True,
            "GRAPHQL_WARMUP_BEAT_INTERVAL_SECONDS": "invalid",
        }
    )
    def test_configure_graphql_warmup_beat_schedule_defaults_invalid_interval(
        self,
    ) -> None:
        """Invalid interval settings fall back to the default schedule."""
        fake_conf = SimpleNamespace(beat_schedule=None)
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch("general_manager.api.graphql_warmup_tasks.current_app", fake_app),
        ):
            configured = configure_graphql_warmup_beat_schedule_from_settings()

        self.assertTrue(configured)
        self.assertEqual(
            fake_conf.beat_schedule[GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY]["schedule"],
            60.0,
        )


class GraphQLWarmUpTaskTests(SimpleTestCase):
    """Verify task wrappers and dispatch helpers for GraphQL warm-up."""

    def test_warm_up_graphql_properties_task_delegates_to_executor(self) -> None:
        """The all-entry task resolves paths and returns executor counts."""
        summary = SimpleNamespace(evaluated=2, failed=0, recipes=2)
        with (
            patch(
                "general_manager.api.graphql_warmup_tasks.import_string",
                return_value=object,
            ),
            patch(
                "general_manager.api.graphql_warmup_tasks.warm_up_graphql_properties",
                return_value=summary,
            ) as warm_up,
        ):
            result = warm_up_graphql_properties_task(["tests.Manager"])

        warm_up.assert_called_once_with([object])
        self.assertEqual(result, {"evaluated": 2, "failed": 0, "recipes": 2})

    def test_warm_up_graphql_properties_task_uses_all_managers_without_paths(
        self,
    ) -> None:
        """The all-entry task passes None through to warm every manager."""
        summary = SimpleNamespace(evaluated=0, failed=0, recipes=0)
        with patch(
            "general_manager.api.graphql_warmup_tasks.warm_up_graphql_properties",
            return_value=summary,
        ) as warm_up:
            result = warm_up_graphql_properties_task()

        warm_up.assert_called_once_with(None)
        self.assertEqual(result, {"evaluated": 0, "failed": 0, "recipes": 0})

    def test_warm_up_graphql_properties_task_rejects_non_list_paths(self) -> None:
        """The all-entry task requires a concrete list of path strings."""
        with self.assertRaises(TypeError):
            warm_up_graphql_properties_task(("tests.Manager",))

    def test_warm_up_graphql_properties_task_rejects_non_string_paths(self) -> None:
        """The all-entry task rejects non-string path entries."""
        with self.assertRaises(TypeError):
            warm_up_graphql_properties_task(["tests.Manager", object()])

    def test_warm_up_graphql_recipes_task_isolates_recipe_failures(self) -> None:
        """Recipe task failures are isolated per cache key."""
        with patch(
            "general_manager.api.graphql_warmup_tasks.warm_up_graphql_recipe",
            side_effect=[True, RuntimeError("boom"), False],
        ):
            warmed = warm_up_graphql_recipes_task(["a", "b", "c"])

        self.assertEqual(warmed, 1)

    def test_warm_up_graphql_recipes_task_returns_zero_for_empty_input(self) -> None:
        """Recipe task returns zero when no cache keys are supplied."""
        with patch(
            "general_manager.api.graphql_warmup_tasks.warm_up_graphql_recipe"
        ) as warm_up_recipe:
            warmed = warm_up_graphql_recipes_task([])

        self.assertEqual(warmed, 0)
        warm_up_recipe.assert_not_called()

    def test_warm_up_graphql_recipes_task_rejects_non_list_keys(self) -> None:
        """Recipe task requires a concrete list of cache key strings."""
        with self.assertRaises(TypeError):
            warm_up_graphql_recipes_task(("a",))

    def test_warm_up_graphql_recipes_task_rejects_non_string_keys(self) -> None:
        """Recipe task rejects non-string cache key entries."""
        with self.assertRaises(TypeError):
            warm_up_graphql_recipes_task(["a", object()])

    def test_refresh_due_graphql_warmup_recipes_task_delegates_to_executor(
        self,
    ) -> None:
        """The due-refresh task delegates its limit to the executor."""
        refresh = Mock(return_value=3)
        with patch(
            "general_manager.api.graphql_warmup_tasks.refresh_due_graphql_warmup_recipes",
            refresh,
        ):
            refreshed = refresh_due_graphql_warmup_recipes_task(limit=10)

        refresh.assert_called_once_with(limit=10)
        self.assertEqual(refreshed, 3)

    def test_refresh_due_graphql_warmup_recipes_task_rejects_invalid_limits(
        self,
    ) -> None:
        """The due-refresh task rejects non-integer and boolean limits."""
        for invalid_limit in ("5", 1.5, True):
            with self.subTest(limit=invalid_limit):
                with self.assertRaises(TypeError):
                    refresh_due_graphql_warmup_recipes_task(limit=invalid_limit)

    def test_dispatch_graphql_warmup_skips_when_celery_unavailable(self) -> None:
        """All-entry dispatch returns false when Celery is unavailable."""
        with patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", False):
            self.assertFalse(dispatch_graphql_warmup())

    def test_dispatch_graphql_warmup_skips_empty_manager_iterable(self) -> None:
        """All-entry dispatch returns false when no manager classes are supplied."""
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch(
                "general_manager.api.graphql_warmup_tasks."
                "warm_up_graphql_properties_task.delay"
            ) as delay,
        ):
            self.assertFalse(dispatch_graphql_warmup([]))

        delay.assert_not_called()

    def test_dispatch_graphql_warmup_skips_unimportable_local_manager(self) -> None:
        """All-entry dispatch skips manager classes without import paths."""

        class LocalManager:
            """Local class that cannot be imported by a worker."""

            pass

        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch(
                "general_manager.api.graphql_warmup_tasks."
                "warm_up_graphql_properties_task.delay"
            ) as delay,
        ):
            self.assertFalse(dispatch_graphql_warmup([LocalManager]))

        delay.assert_not_called()

    def test_dispatch_graphql_warmup_enqueues_manager_paths(self) -> None:
        """All-entry dispatch enqueues importable manager paths."""
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch(
                "general_manager.api.graphql_warmup_tasks."
                "warm_up_graphql_properties_task.delay"
            ) as delay,
        ):
            dispatched = dispatch_graphql_warmup([GraphQLWarmUpTaskTests])

        self.assertTrue(dispatched)
        delay.assert_called_once_with(
            ["tests.unit.test_graphql_warmup_tasks.GraphQLWarmUpTaskTests"]
        )

    def test_dispatch_graphql_warmup_deduplicates_manager_paths(self) -> None:
        """All-entry dispatch deduplicates importable manager paths."""
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch(
                "general_manager.api.graphql_warmup_tasks."
                "warm_up_graphql_properties_task.delay"
            ) as delay,
        ):
            dispatched = dispatch_graphql_warmup(
                [GraphQLWarmUpTaskTests, GraphQLWarmUpTaskTests]
            )

        self.assertTrue(dispatched)
        delay.assert_called_once_with(
            ["tests.unit.test_graphql_warmup_tasks.GraphQLWarmUpTaskTests"]
        )

    def test_dispatch_graphql_warmup_rejects_non_class_entries(self) -> None:
        """All-entry dispatch rejects invalid manager iterable entries."""
        with patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True):
            with self.assertRaises(TypeError):
                dispatch_graphql_warmup([object()])

    def test_dispatch_graphql_warmup_returns_false_when_enqueue_fails(self) -> None:
        """All-entry dispatch logs and returns false on enqueue failure."""
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch(
                "general_manager.api.graphql_warmup_tasks."
                "warm_up_graphql_properties_task.delay",
                side_effect=RuntimeError("boom"),
            ),
            patch("general_manager.api.graphql_warmup_tasks.logger.exception") as log,
        ):
            self.assertFalse(dispatch_graphql_warmup())

        log.assert_called_once()

    def test_dispatch_graphql_recipe_warmup_skips_without_celery_or_keys(self) -> None:
        """Recipe dispatch skips missing Celery and empty work lists."""
        with patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", False):
            self.assertFalse(dispatch_graphql_recipe_warmup(["a"]))

        with patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True):
            self.assertFalse(dispatch_graphql_recipe_warmup([]))

    def test_dispatch_graphql_recipe_warmup_enqueues_distinct_keys(self) -> None:
        """Recipe dispatch deduplicates keys before enqueueing."""
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch(
                "general_manager.api.graphql_warmup_tasks."
                "warm_up_graphql_recipes_task.delay"
            ) as delay,
        ):
            dispatched = dispatch_graphql_recipe_warmup(["a", "a", "b"])

        self.assertTrue(dispatched)
        delay.assert_called_once_with(["a", "b"])

    def test_dispatch_graphql_recipe_warmup_rejects_single_string(self) -> None:
        """Recipe dispatch rejects a single string instead of key iterable."""
        with patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True):
            with self.assertRaises(TypeError):
                dispatch_graphql_recipe_warmup("abc")

    def test_dispatch_graphql_recipe_warmup_rejects_non_string_keys(self) -> None:
        """Recipe dispatch rejects non-string cache keys before enqueueing."""
        with patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True):
            with self.assertRaises(TypeError):
                dispatch_graphql_recipe_warmup(["a", object()])

    def test_dispatch_graphql_recipe_warmup_returns_false_when_enqueue_fails(
        self,
    ) -> None:
        """Recipe dispatch logs and returns false on enqueue failure."""
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch(
                "general_manager.api.graphql_warmup_tasks."
                "warm_up_graphql_recipes_task.delay",
                side_effect=RuntimeError("boom"),
            ),
            patch("general_manager.api.graphql_warmup_tasks.logger.exception") as log,
        ):
            self.assertFalse(dispatch_graphql_recipe_warmup(["a"]))

        log.assert_called_once()
