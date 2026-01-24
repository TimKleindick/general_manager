from __future__ import annotations

from typing import ClassVar
from unittest.mock import patch

from django.db.models import CharField
from django.test import override_settings

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.search.config import IndexConfig
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class _DummyTask:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def delay(self, manager_path: str, identification: dict) -> None:
        self.calls.append((manager_path, identification))


@override_settings(SEARCH_ASYNC=True)
class SearchAsyncTaskIntegrationTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=200)
                status = CharField(max_length=50)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

            class SearchConfig:
                indexes: ClassVar[list[IndexConfig]] = [
                    IndexConfig(name="global", fields=["name"])
                ]

        cls.general_manager_classes = [Project]
        cls.Project = Project
        GeneralManagerMeta.all_classes = cls.general_manager_classes

    def setUp(self) -> None:
        super().setUp()
        self.index_task = _DummyTask()
        self.delete_task = _DummyTask()
        self._patches = [
            patch("general_manager.search.async_tasks.CELERY_AVAILABLE", True),
            patch(
                "general_manager.search.async_tasks.index_instance_task",
                self.index_task,
            ),
            patch(
                "general_manager.search.async_tasks.delete_instance_task",
                self.delete_task,
            ),
        ]
        for patcher in self._patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in self._patches:
            patcher.stop()
        super().tearDown()

    def test_create_and_delete_dispatch_async_tasks(self) -> None:
        instance = self.Project.create(
            name="Alpha",
            status="public",
            ignore_permission=True,
        )
        assert self.index_task.calls
        instance.delete()
        assert self.delete_task.calls
