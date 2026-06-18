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
    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_ENABLED": True})
    def test_search_reconcile_enabled_reads_general_manager_setting(self) -> None:
        assert search_reconcile_enabled() is True

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_INTERVAL_SECONDS": 30})
    def test_search_reconcile_interval_seconds_reads_general_manager_setting(
        self,
    ) -> None:
        assert search_reconcile_interval_seconds() == 30

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_INTERVAL_SECONDS": "bad"})
    def test_search_reconcile_interval_seconds_falls_back_to_default(self) -> None:
        assert search_reconcile_interval_seconds() == 60


class SearchReconcileTaskTests(SimpleTestCase):
    def test_reconcile_search_indexes_task_calls_service(self) -> None:
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

    @override_settings(GENERAL_MANAGER={"SEARCH_RECONCILE_ENABLED": False})
    def test_configure_search_reconcile_beat_schedule_skips_when_disabled(self) -> None:
        configured = configure_search_reconcile_beat_schedule_from_settings()

        assert configured is False
