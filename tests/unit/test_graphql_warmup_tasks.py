from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from general_manager.api.graphql_warmup_tasks import (
    GRAPHQL_WARMUP_BEAT_SCHEDULE_KEY,
    configure_graphql_warmup_beat_schedule_from_settings,
    refresh_due_graphql_warmup_recipes_task,
    warm_up_graphql_properties_task,
    warm_up_graphql_recipes_task,
)


class GraphQLWarmUpBeatScheduleTests(SimpleTestCase):
    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_WARMUP_BEAT_ENABLED": True,
            "GRAPHQL_WARMUP_BEAT_INTERVAL_SECONDS": 13,
        }
    )
    def test_configure_graphql_warmup_beat_schedule_registers_task(self) -> None:
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

    @override_settings(GENERAL_MANAGER={"GRAPHQL_WARMUP_BEAT_ENABLED": False})
    def test_configure_graphql_warmup_beat_schedule_skips_when_disabled(self) -> None:
        fake_conf = SimpleNamespace(beat_schedule={})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.api.graphql_warmup_tasks.CELERY_AVAILABLE", True),
            patch("general_manager.api.graphql_warmup_tasks.current_app", fake_app),
        ):
            configured = configure_graphql_warmup_beat_schedule_from_settings()

        self.assertFalse(configured)
        self.assertEqual(fake_conf.beat_schedule, {})


class GraphQLWarmUpTaskTests(SimpleTestCase):
    def test_warm_up_graphql_properties_task_delegates_to_executor(self) -> None:
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

    def test_warm_up_graphql_recipes_task_isolates_recipe_failures(self) -> None:
        with patch(
            "general_manager.api.graphql_warmup_tasks.warm_up_graphql_recipe",
            side_effect=[True, RuntimeError("boom"), False],
        ):
            warmed = warm_up_graphql_recipes_task(["a", "b", "c"])

        self.assertEqual(warmed, 1)

    def test_refresh_due_graphql_warmup_recipes_task_delegates_to_executor(
        self,
    ) -> None:
        refresh = Mock(return_value=3)
        with patch(
            "general_manager.api.graphql_warmup_tasks.refresh_due_graphql_warmup_recipes",
            refresh,
        ):
            refreshed = refresh_due_graphql_warmup_recipes_task(limit=10)

        refresh.assert_called_once_with(limit=10)
        self.assertEqual(refreshed, 3)
