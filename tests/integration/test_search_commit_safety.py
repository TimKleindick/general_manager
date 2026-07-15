"""Integration tests for commit-safe data-change signal dispatch."""

from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.db import connections, models, transaction
from django.db.models import CharField

from general_manager.cache.signals import post_data_change, pre_data_change
from general_manager.interface import DatabaseInterface, ExistingModelInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class PostReceiverFailure(RuntimeError):
    """Expected receiver failure used to exercise transaction rollback."""


class ExpectedRollback(RuntimeError):
    """Expected explicit rollback used by transaction boundary tests."""


def _force_rollback() -> None:
    """Raise the test-only exception that marks an intentional rollback."""
    raise ExpectedRollback


class SearchCommitSafetyIntegrationTests(GeneralManagerTransactionTestCase):
    """Verify ORM writes and receiver side effects share commit boundaries."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create an isolated ORM-backed manager for transaction tests."""

        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=200)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

        cls.Project = Project
        cls.ProjectModel = Project.Interface._model
        cls.general_manager_classes = [Project]
        GeneralManagerMeta.all_classes = cls.general_manager_classes
        super().setUpClass()

    def _connect_post_receiver(self, receiver: object) -> None:
        """Connect one strong receiver and ensure test cleanup disconnects it."""
        post_data_change.connect(receiver, weak=False)
        self.addCleanup(post_data_change.disconnect, receiver)

    def test_post_receiver_exception_rolls_back_orm_mutation(self) -> None:
        """A failing post-change receiver rolls back the row it observed."""

        def fail_after_create(sender, **kwargs):
            if sender is self.Project and kwargs["action"] == "create":
                raise PostReceiverFailure

        self._connect_post_receiver(fail_after_create)

        with self.assertRaises(PostReceiverFailure):
            self.Project.create(name="rolled back", ignore_permission=True)

        self.assertFalse(self.ProjectModel.objects.filter(name="rolled back").exists())

    def test_receiver_on_commit_runs_after_successful_wrapper_commit(self) -> None:
        """A receiver callback runs only once the data-change atomic commits."""
        callbacks: list[str] = []
        aliases: list[str] = []

        def register_callback(sender, **kwargs):
            if sender is self.Project:
                alias = kwargs["database_alias"]
                aliases.append(alias)
                transaction.on_commit(
                    lambda: callbacks.append("committed"), using=alias
                )

        self._connect_post_receiver(register_callback)

        self.Project.create(name="committed", ignore_permission=True)

        self.assertEqual(aliases, ["default"])
        self.assertEqual(callbacks, ["committed"])
        self.assertTrue(self.ProjectModel.objects.filter(name="committed").exists())

    def test_receiver_on_commit_is_discarded_by_surrounding_rollback(self) -> None:
        """An outer transaction rollback discards the receiver callback and row."""
        callbacks: list[str] = []

        def register_callback(sender, **kwargs):
            if sender is self.Project:
                transaction.on_commit(
                    lambda: callbacks.append("committed"),
                    using=kwargs["database_alias"],
                )

        self._connect_post_receiver(register_callback)

        with self.assertRaises(ExpectedRollback):
            with transaction.atomic():
                self.Project.create(name="outer rollback", ignore_permission=True)
                _force_rollback()

        self.assertEqual(callbacks, [])
        self.assertFalse(
            self.ProjectModel.objects.filter(name="outer rollback").exists()
        )

    def test_receiver_on_commit_is_discarded_by_savepoint_rollback(self) -> None:
        """A savepoint rollback discards callbacks registered below it."""
        callbacks: list[str] = []

        def register_callback(sender, **kwargs):
            if sender is self.Project:
                transaction.on_commit(
                    lambda: callbacks.append("committed"),
                    using=kwargs["database_alias"],
                )

        self._connect_post_receiver(register_callback)

        with transaction.atomic():
            try:
                with transaction.atomic():
                    self.Project.create(
                        name="savepoint rollback", ignore_permission=True
                    )
                    _force_rollback()
            except ExpectedRollback:
                pass
            self.assertFalse(
                self.ProjectModel.objects.filter(name="savepoint rollback").exists()
            )

        self.assertEqual(callbacks, [])


class SecondaryDatabaseCommitSafetyIntegrationTests(GeneralManagerTransactionTestCase):
    """Exercise data-change transaction boundaries on a configured DB alias."""

    databases: ClassVar[set[str]] = {"default", "secondary"}

    @classmethod
    def _restore_secondary_connection(cls) -> None:
        """Restore the connection handler state replaced by this test class."""
        if hasattr(connections._connections, "secondary"):
            connections["secondary"].close()
            del connections["secondary"]
        if cls._secondary_original_config is None:
            connections.databases.pop("secondary", None)
        else:
            connections.databases["secondary"] = cls._secondary_original_config
        if cls._secondary_had_cached_connection:
            connections._connections.secondary = (  # type: ignore[attr-defined]
                cls._secondary_original_connection
            )

    @classmethod
    def setUpClass(cls) -> None:
        """Configure an isolated in-memory secondary database and manager."""
        alias = "secondary"
        cls._secondary_original_config = connections.databases.get(alias)
        cls._secondary_had_cached_connection = hasattr(
            connections._connections,
            alias,
        )
        cls._secondary_original_connection = (
            getattr(connections._connections, alias)
            if cls._secondary_had_cached_connection
            else None
        )
        cls.addClassCleanup(cls._restore_secondary_connection)
        if cls._secondary_had_cached_connection:
            del connections[alias]
        connections.databases[alias] = {
            **connections.databases["default"],
            "NAME": ":memory:",
        }

        class SecondaryCommitRecord(models.Model):
            name = models.CharField(max_length=200)

            class Meta:
                app_label = "general_manager"

        class SecondaryCommitInterface(ExistingModelInterface):
            model = SecondaryCommitRecord
            database = alias

        class SecondaryCommitManager(GeneralManager):
            Interface = SecondaryCommitInterface

        cls.SecondaryCommitRecord = SecondaryCommitRecord
        cls.SecondaryCommitManager = SecondaryCommitManager
        cls.general_manager_classes = [SecondaryCommitManager]
        super().setUpClass()

        secondary = connections[alias]
        secondary.connect()
        with secondary.schema_editor() as editor:
            editor.create_model(ContentType)
            editor.create_model(Permission)
            editor.create_model(Group)
            editor.create_model(User)
            editor.create_model(SecondaryCommitRecord)
            editor.create_model(SecondaryCommitRecord.history.model)

    @classmethod
    def tearDownClass(cls) -> None:
        """Drop secondary schemas before the connection is restored."""
        secondary = connections["secondary"]
        try:
            with secondary.schema_editor() as editor:
                editor.delete_model(cls.SecondaryCommitRecord.history.model)
                editor.delete_model(cls.SecondaryCommitRecord)
                editor.delete_model(User)
                editor.delete_model(Group)
                editor.delete_model(Permission)
                editor.delete_model(ContentType)
        finally:
            super().tearDownClass()

    def _connect_receiver(self, signal, receiver: object) -> None:
        """Connect one strong signal receiver and disconnect it during cleanup."""
        signal.connect(receiver, weak=False)
        self.addCleanup(signal.disconnect, receiver)

    def test_secondary_write_signals_and_on_commit_use_configured_alias(self) -> None:
        """The write, both signals, and callback all use the secondary DB."""
        aliases: list[tuple[str, str]] = []
        callbacks: list[str] = []

        def record_pre(sender, **kwargs):
            if sender is self.SecondaryCommitManager:
                aliases.append(("pre", kwargs["database_alias"]))

        def register_post_callback(sender, **kwargs):
            if sender is self.SecondaryCommitManager:
                alias = kwargs["database_alias"]
                aliases.append(("post", alias))
                transaction.on_commit(
                    lambda: callbacks.append(alias),
                    using=alias,
                )

        self._connect_receiver(pre_data_change, record_pre)
        self._connect_receiver(post_data_change, register_post_callback)

        created = self.SecondaryCommitManager.create(
            name="secondary persisted",
            ignore_permission=True,
        )

        record_id = created.identification["id"]
        self.assertEqual(aliases, [("pre", "secondary"), ("post", "secondary")])
        self.assertEqual(callbacks, ["secondary"])
        self.assertTrue(
            self.SecondaryCommitRecord.objects.using("secondary")
            .filter(pk=record_id, name="secondary persisted")
            .exists()
        )
        self.assertFalse(
            self.SecondaryCommitRecord.objects.using("default")
            .filter(pk=record_id)
            .exists()
        )

    def test_post_failure_rolls_back_secondary_write_and_callback(self) -> None:
        """A post receiver failure rolls back secondary work and its callback."""
        callbacks: list[str] = []

        def register_post_callback(sender, **kwargs):
            if sender is self.SecondaryCommitManager:
                transaction.on_commit(
                    lambda: callbacks.append("committed"),
                    using=kwargs["database_alias"],
                )

        def fail_post(sender, **kwargs):
            del kwargs
            if sender is self.SecondaryCommitManager:
                raise PostReceiverFailure

        self._connect_receiver(post_data_change, register_post_callback)
        self._connect_receiver(post_data_change, fail_post)

        with self.assertRaises(PostReceiverFailure):
            self.SecondaryCommitManager.create(
                name="secondary rolled back",
                ignore_permission=True,
            )

        self.assertEqual(callbacks, [])
        self.assertFalse(
            self.SecondaryCommitRecord.objects.using("secondary")
            .filter(name="secondary rolled back")
            .exists()
        )
