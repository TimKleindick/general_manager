from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from general_manager.search.tasks import (
    SEARCH_RECONCILE_BEAT_SCHEDULE_KEY,
    configure_search_reconcile_beat_schedule_from_settings,
    reconcile_search_indexes_task,
    search_reconcile_enabled,
    search_reconcile_interval_seconds,
)


class SearchReconcileSettingsTests(SimpleTestCase):
    @override_settings(GENERAL_MANAGER={})
    def test_search_reconcile_enabled_defaults_false(self) -> None:
        """Missing reconciliation enabled setting defaults to false."""
        assert search_reconcile_enabled() is False

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_ENABLED": True})
    def test_search_reconcile_enabled_reads_general_manager_setting(self) -> None:
        """Read the reconciliation enabled flag from GENERAL_MANAGER settings."""
        assert search_reconcile_enabled() is True

    @override_settings(
        SEARCH_RECONCILE_ENABLED=True,
        GENERAL_MANAGER={"SEARCH_RECONCILE_ENABLED": False},
    )
    def test_search_reconcile_enabled_general_manager_setting_wins(self) -> None:
        """GENERAL_MANAGER reconciliation settings override top-level settings."""
        assert search_reconcile_enabled() is False

    @override_settings(GENERAL_MANAGER={})
    def test_search_reconcile_interval_seconds_defaults_to_sixty(self) -> None:
        """Missing reconciliation interval defaults to 60 seconds."""
        assert search_reconcile_interval_seconds() == 60

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_INTERVAL_SECONDS": 30})
    def test_search_reconcile_interval_seconds_reads_general_manager_setting(
        self,
    ) -> None:
        """Read the reconciliation interval from GENERAL_MANAGER settings."""
        assert search_reconcile_interval_seconds() == 30

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_INTERVAL_SECONDS": "bad"})
    def test_search_reconcile_interval_seconds_falls_back_to_default(self) -> None:
        """Fall back to the default interval when the setting is invalid."""
        assert search_reconcile_interval_seconds() == 60

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_INTERVAL_SECONDS": False})
    def test_search_reconcile_interval_seconds_falls_back_for_bool(self) -> None:
        """Boolean values are invalid interval settings."""
        assert search_reconcile_interval_seconds() == 60

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_INTERVAL_SECONDS": 0})
    def test_search_reconcile_interval_seconds_uses_minimum_for_zero(self) -> None:
        """Clamp a zero reconciliation interval to one second."""
        assert search_reconcile_interval_seconds() == 1

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_INTERVAL_SECONDS": -10})
    def test_search_reconcile_interval_seconds_uses_minimum_for_negative(self) -> None:
        """Clamp a negative reconciliation interval to one second."""
        assert search_reconcile_interval_seconds() == 1


class SearchReconcileTaskTests(SimpleTestCase):
    def test_reconcile_search_indexes_task_calls_service(self) -> None:
        """Return reconciliation counts from the Celery task wrapper."""
        with patch(
            "general_manager.search.tasks.reconcile_search_indexes"
        ) as reconcile:
            reconcile.return_value.reconciled = 2
            reconcile.return_value.failed = 0
            reconcile.return_value.documents = 7
            result = reconcile_search_indexes_task()

        assert result == {"reconciled": 2, "failed": 0, "documents": 7}

    @override_settings(
        GENERAL_MANAGER={
            "SEARCH_RECONCILE_ENABLED": True,
            "SEARCH_RECONCILE_INTERVAL_SECONDS": 60,
        }
    )
    def test_configure_search_reconcile_beat_schedule_registers_task(self) -> None:
        """Register the configured reconciliation task in Celery Beat."""
        fake_conf = SimpleNamespace(beat_schedule={})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.search.tasks.CELERY_AVAILABLE", True),
            patch("general_manager.search.tasks.current_app", fake_app),
        ):
            configured = configure_search_reconcile_beat_schedule_from_settings()

        assert configured is True
        entry = fake_conf.beat_schedule[SEARCH_RECONCILE_BEAT_SCHEDULE_KEY]
        assert (
            entry["task"]
            == "general_manager.search.tasks.reconcile_search_indexes_task"
        )
        assert entry["schedule"] == 60.0
        assert entry["options"] == {"queue": "search.reconciliation"}

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_ENABLED": True})
    def test_configure_search_reconcile_beat_schedule_preserves_existing_entries(
        self,
    ) -> None:
        """Preserve unrelated Beat schedule entries while installing search."""
        existing_entry = {"task": "project.cleanup"}
        fake_conf = SimpleNamespace(beat_schedule={"project.cleanup": existing_entry})
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.search.tasks.CELERY_AVAILABLE", True),
            patch("general_manager.search.tasks.current_app", fake_app),
        ):
            configured = configure_search_reconcile_beat_schedule_from_settings()

        assert configured is True
        assert fake_conf.beat_schedule["project.cleanup"] == existing_entry
        assert SEARCH_RECONCILE_BEAT_SCHEDULE_KEY in fake_conf.beat_schedule

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_ENABLED": True})
    def test_configure_search_reconcile_beat_schedule_replaces_malformed_schedule(
        self,
    ) -> None:
        """Treat non-mapping Beat schedules as empty schedules."""
        fake_conf = SimpleNamespace(beat_schedule="not-a-schedule")
        fake_app = SimpleNamespace(conf=fake_conf)
        with (
            patch("general_manager.search.tasks.CELERY_AVAILABLE", True),
            patch("general_manager.search.tasks.current_app", fake_app),
        ):
            configured = configure_search_reconcile_beat_schedule_from_settings()

        assert configured is True
        assert tuple(fake_conf.beat_schedule) == (SEARCH_RECONCILE_BEAT_SCHEDULE_KEY,)

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_ENABLED": False})
    def test_configure_search_reconcile_beat_schedule_skips_when_disabled(self) -> None:
        """Skip Celery Beat registration when reconciliation is disabled."""
        configured = configure_search_reconcile_beat_schedule_from_settings()

        assert configured is False

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_ENABLED": True})
    def test_configure_search_reconcile_beat_schedule_skips_without_celery(
        self,
    ) -> None:
        """Return false when Celery is unavailable."""
        with patch("general_manager.search.tasks.CELERY_AVAILABLE", False):
            configured = configure_search_reconcile_beat_schedule_from_settings()

        assert configured is False
