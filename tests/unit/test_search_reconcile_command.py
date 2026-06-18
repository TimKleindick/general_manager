from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class SearchReconcileCommandTests(SimpleTestCase):
    def test_search_reconcile_once_runs_service(self) -> None:
        stdout = StringIO()
        with patch(
            "general_manager.management.commands.search_reconcile.reconcile_search_indexes"
        ) as reconcile:
            reconcile.return_value.reconciled = 1
            reconcile.return_value.failed = 0
            reconcile.return_value.documents = 3

            call_command("search_reconcile", "--once", stdout=stdout)

        reconcile.assert_called_once_with(force=False, max_states=None)
        assert "Reconciled 1 search index states" in stdout.getvalue()

    def test_search_reconcile_force_passes_force(self) -> None:
        with patch(
            "general_manager.management.commands.search_reconcile.reconcile_search_indexes"
        ) as reconcile:
            reconcile.return_value.reconciled = 0
            reconcile.return_value.failed = 0
            reconcile.return_value.documents = 0

            call_command("search_reconcile", "--once", "--force")

        reconcile.assert_called_once_with(force=True, max_states=None)

    def test_search_reconcile_max_states_passes_limit(self) -> None:
        with patch(
            "general_manager.management.commands.search_reconcile.reconcile_search_indexes"
        ) as reconcile:
            reconcile.return_value.reconciled = 0
            reconcile.return_value.failed = 0
            reconcile.return_value.documents = 0

            call_command("search_reconcile", "--once", "--max-states", "2")

        reconcile.assert_called_once_with(force=False, max_states=2)

    def test_search_reconcile_watch_repeats_until_interrupted(self) -> None:
        calls = []

        def _reconcile(*, force=False, max_states=None):
            del force, max_states
            result = type(
                "Result",
                (),
                {"reconciled": 0, "failed": 0, "documents": 0},
            )()
            calls.append(result)
            return result

        with (
            patch(
                "general_manager.management.commands.search_reconcile.reconcile_search_indexes",
                side_effect=_reconcile,
            ),
            patch(
                "general_manager.management.commands.search_reconcile.time.sleep",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            call_command("search_reconcile", "--watch", "--interval", "1")

        assert len(calls) == 1
