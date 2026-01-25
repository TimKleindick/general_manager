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
        """
        Initialize the dummy task and prepare storage for recorded delayed calls.
        
        The `calls` attribute is a list that will store tuples of `(manager_path, identification)` for each simulated delayed invocation.
        """
        self.calls: list[tuple[str, dict]] = []

    def delay(self, manager_path: str, identification: dict) -> None:
        """
        Record a delayed task invocation by appending a (manager_path, identification) tuple to self.calls.
        
        Parameters:
            manager_path (str): Dotted import path identifying the manager for the task.
            identification (dict): Mapping that identifies the target instance (for example primary key fields).
        """
        self.calls.append((manager_path, identification))


@override_settings(SEARCH_ASYNC=True)
class SearchAsyncTaskIntegrationTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """
        Prepare test fixtures by defining and registering a temporary GeneralManager subclass named `Project`.
        
        Defines a `Project` manager with a simple Interface (fields `name` and `status`), permissive ManagerBasedPermission for all CRUD actions, and a SearchConfig with a single "global" index on `name`. The class is stored on `cls` as `Project`, listed in `cls.general_manager_classes`, and assigned to `GeneralManagerMeta.all_classes` for test discovery.
        """
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
        """
        Prepare the test environment by creating dummy async task handlers and patching the module-level async task objects and Celery availability.
        
        This sets self.index_task and self.delete_task to _DummyTask instances, replaces
        general_manager.search.async_tasks.index_instance_task and delete_instance_task with those
        dummies, and forces general_manager.search.async_tasks.CELERY_AVAILABLE to True so that
        async dispatch code paths are exercised during tests.
        """
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
        """
        Stop any patches started by setUp and perform parent-class teardown.
        
        Stops each patcher stored in self._patches and then calls the superclass tearDown to complete standard cleanup.
        """
        for patcher in self._patches:
            patcher.stop()
        super().tearDown()

    def test_create_and_delete_dispatch_async_tasks(self) -> None:
        """
        Verify that creating and deleting a Project dispatch asynchronous index and delete tasks.
        
        Creates a Project instance (ignoring permission checks) and asserts an index task was queued, then deletes the instance and asserts a delete task was queued.
        """
        instance = self.Project.create(
            name="Alpha",
            status="public",
            ignore_permission=True,
        )
        assert self.index_task.calls
        instance.delete()
        assert self.delete_task.calls