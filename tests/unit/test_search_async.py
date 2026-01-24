from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from general_manager.search.async_tasks import dispatch_index_update


class SearchAsyncTests(SimpleTestCase):
    @override_settings(SEARCH_ASYNC=False)
    @patch("general_manager.search.async_tasks.index_instance_task")
    def test_dispatch_runs_inline_when_disabled(self, mock_task):
        dispatch_index_update(
            action="index",
            manager_path="tests.utils.simple_manager_interface.BaseTestInterface",
            identification={"id": 1},
        )
        mock_task.assert_called_once()

    @override_settings(SEARCH_ASYNC=True)
    @patch("general_manager.search.async_tasks.index_instance_task")
    def test_dispatch_runs_inline_when_no_celery(self, mock_task):
        with patch("general_manager.search.async_tasks.CELERY_AVAILABLE", False):
            dispatch_index_update(
                action="index",
                manager_path="tests.utils.simple_manager_interface.BaseTestInterface",
                identification={"id": 1},
            )
        mock_task.assert_called_once()
