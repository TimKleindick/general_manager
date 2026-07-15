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
from general_manager.search.config import IndexConfig, SearchConfigSpec
from general_manager.search.indexer import SearchDeleteTarget
from general_manager.search.models import SearchIndexState
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class PostReceiverFailure(RuntimeError):
    """Expected receiver failure used to exercise transaction rollback."""


class ExpectedRollback(RuntimeError):
    """Expected explicit rollback used by transaction boundary tests."""


class UnexpectedBrokerFailure(Exception):
    """Queue failure outside the search bridge's expected exception taxonomy."""


class UnexpectedSearchExtensionFailure(Exception):
    """User search-extension failure outside the expected exception taxonomy."""

    def __init__(self) -> None:
        """Initialize the test-only extension error."""
        super().__init__("custom search extension failed")


def _force_rollback() -> None:
    """Raise the test-only exception that marks an intentional rollback."""
    raise ExpectedRollback


_TEST_SEARCH_CONFIG = SearchConfigSpec(
    indexes=(IndexConfig(name="global", fields=["name"]),)
)


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

    def test_search_create_dispatches_only_after_outer_commit(self) -> None:
        """Direct create indexing must not escape a surrounding transaction."""
        from unittest.mock import patch

        with (
            patch(
                "general_manager.search.invalidation.get_search_config",
                return_value=_TEST_SEARCH_CONFIG,
            ),
            patch(
                "general_manager.search.invalidation.dispatch_index_update"
            ) as dispatch,
        ):
            with transaction.atomic():
                self.Project.create(name="deferred search", ignore_permission=True)
                dispatch.assert_not_called()

            dispatch.assert_called_once()
            self.assertEqual(dispatch.call_args.kwargs["index_name"], "global")
            self.assertEqual(
                dispatch.call_args.kwargs["instance"].identification,
                dispatch.call_args.kwargs["identification"],
            )

    def test_search_create_dispatch_is_discarded_by_outer_rollback(self) -> None:
        """A rolled-back create has neither a row nor an indexing callback."""
        from unittest.mock import patch

        with (
            patch(
                "general_manager.search.invalidation.get_search_config",
                return_value=_TEST_SEARCH_CONFIG,
            ),
            patch(
                "general_manager.search.invalidation.dispatch_index_update"
            ) as dispatch,
        ):
            with self.assertRaises(ExpectedRollback):
                with transaction.atomic():
                    self.Project.create(
                        name="search rolled back", ignore_permission=True
                    )
                    _force_rollback()

        dispatch.assert_not_called()
        self.assertFalse(
            self.ProjectModel.objects.filter(name="search rolled back").exists()
        )

    def test_search_create_dispatch_is_discarded_by_savepoint_rollback(self) -> None:
        """A nested savepoint rollback discards only its search callback."""
        from unittest.mock import patch

        with (
            patch(
                "general_manager.search.invalidation.get_search_config",
                return_value=_TEST_SEARCH_CONFIG,
            ),
            patch(
                "general_manager.search.invalidation.dispatch_index_update"
            ) as dispatch,
        ):
            with transaction.atomic():
                try:
                    with transaction.atomic():
                        self.Project.create(
                            name="nested search rollback", ignore_permission=True
                        )
                        _force_rollback()
                except ExpectedRollback:
                    pass

        dispatch.assert_not_called()

    def test_search_update_dispatches_only_after_outer_commit(self) -> None:
        """Direct updates dispatch exact-pair indexing after commit."""
        from unittest.mock import patch

        instance = self.Project.create(name="before", ignore_permission=True)
        with (
            patch(
                "general_manager.search.invalidation.get_search_config",
                return_value=_TEST_SEARCH_CONFIG,
            ),
            patch(
                "general_manager.search.invalidation.dispatch_index_update"
            ) as dispatch,
        ):
            with transaction.atomic():
                instance.update(name="after", ignore_permission=True)
                dispatch.assert_not_called()

            dispatch.assert_called_once()
            self.assertEqual(dispatch.call_args.kwargs["index_name"], "global")
            self.assertEqual(dispatch.call_args.kwargs["action"], "index")

    def test_unexpected_enqueue_failure_is_suppressed_after_default_commit(
        self,
    ) -> None:
        """Arbitrary broker errors cannot escape after business data commits."""
        from unittest.mock import patch

        class SearchConfig:
            indexes = _TEST_SEARCH_CONFIG.indexes

        self.Project.SearchConfig = SearchConfig
        self.addCleanup(delattr, self.Project, "SearchConfig")
        with (
            patch(
                "general_manager.search.invalidation.dispatch_index_update",
                side_effect=UnexpectedBrokerFailure("broker unavailable"),
            ),
            patch(
                "general_manager.search.invalidation.acknowledge_search_index_dirty"
            ) as acknowledge,
            self.assertLogs("general_manager.search.invalidation", level="WARNING"),
        ):
            created = self.Project.create(
                name="committed despite broker", ignore_permission=True
            )

        self.assertTrue(
            self.ProjectModel.objects.filter(
                pk=created.identification["id"],
                name="committed despite broker",
            ).exists()
        )
        state = SearchIndexState.objects.get(index_name="global")
        self.assertIsNotNone(state.dirty_since)
        acknowledge.assert_not_called()

    def test_unexpected_document_id_failure_does_not_abort_delete(self) -> None:
        """A custom document-ID exception leaves fallback dirty work behind."""
        from unittest.mock import patch

        instance = self.Project.create(
            name="delete despite config", ignore_permission=True
        )
        identification = dict(instance.identification)

        class SearchConfig:
            indexes = _TEST_SEARCH_CONFIG.indexes

            @staticmethod
            def document_id(_instance: object) -> str:
                raise UnexpectedSearchExtensionFailure

        self.Project.SearchConfig = SearchConfig
        self.addCleanup(delattr, self.Project, "SearchConfig")
        with (
            patch(
                "general_manager.search.invalidation.dispatch_delete_documents"
            ) as dispatch,
            patch(
                "general_manager.search.invalidation.acknowledge_search_index_dirty"
            ) as acknowledge,
            self.assertLogs("general_manager.search.invalidation", level="WARNING"),
        ):
            instance.delete(ignore_permission=True)

        self.assertFalse(
            self.ProjectModel.objects.filter(pk=identification["id"]).exists()
        )
        state = SearchIndexState.objects.get(index_name="global")
        self.assertIsNotNone(state.dirty_since)
        dispatch.assert_not_called()
        acknowledge.assert_not_called()

    def test_unexpected_metadata_copy_failure_keeps_create_and_marks_fallback(
        self,
    ) -> None:
        """Create metadata failures schedule marker-only recovery work."""
        from unittest.mock import patch

        class SearchConfig:
            indexes = _TEST_SEARCH_CONFIG.indexes

        self.Project.SearchConfig = SearchConfig
        self.addCleanup(delattr, self.Project, "SearchConfig")
        with (
            patch(
                "general_manager.search.invalidation.deepcopy",
                side_effect=UnexpectedSearchExtensionFailure(),
            ),
            patch(
                "general_manager.search.invalidation.dispatch_index_update"
            ) as dispatch,
            patch(
                "general_manager.search.invalidation.acknowledge_search_index_dirty"
            ) as acknowledge,
            self.assertLogs("general_manager.search.invalidation", level="WARNING"),
        ):
            created = self.Project.create(
                name="create despite metadata", ignore_permission=True
            )

        self.assertTrue(
            self.ProjectModel.objects.filter(pk=created.identification["id"]).exists()
        )
        state = SearchIndexState.objects.get(index_name="global")
        self.assertIsNotNone(state.dirty_since)
        dispatch.assert_not_called()
        acknowledge.assert_not_called()

    def test_search_delete_dispatches_captured_custom_id_after_commit(self) -> None:
        """Delete callbacks use immutable IDs and never reconstruct deleted rows."""
        from unittest.mock import patch

        instance = self.Project.create(name="to delete", ignore_permission=True)
        identification = dict(instance.identification)
        manager_path = f"{self.Project.__module__}.{self.Project.__name__}"
        target = SearchDeleteTarget(
            manager_class=self.Project,
            manager_path=manager_path,
            index_name="global",
            document_id=f"project-{identification['id']}",
        )
        with (
            patch(
                "general_manager.search.invalidation.get_search_config",
                return_value=_TEST_SEARCH_CONFIG,
            ),
            patch(
                "general_manager.search.invalidation.capture_delete_targets",
                return_value=(target,),
            ),
            patch(
                "general_manager.search.invalidation.dispatch_index_update"
            ) as index_dispatch,
            patch(
                "general_manager.search.invalidation.dispatch_delete_documents"
            ) as delete_dispatch,
        ):
            with transaction.atomic():
                instance.delete(ignore_permission=True)
                delete_dispatch.assert_not_called()
            delete_dispatch.assert_called_once()
            index_dispatch.assert_not_called()

        manager_path, targets = delete_dispatch.call_args.args
        self.assertTrue(manager_path.endswith(".Project"))
        self.assertEqual(
            targets,
            (
                {
                    "index_name": "global",
                    "document_id": f"project-{identification['id']}",
                },
            ),
        )


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

    def test_search_marker_and_dispatch_wait_for_secondary_commit(self) -> None:
        """Cross-database control-plane work starts after source commit."""
        from unittest.mock import patch

        with (
            patch(
                "general_manager.search.invalidation.get_search_config",
                return_value=_TEST_SEARCH_CONFIG,
            ),
            patch(
                "general_manager.search.invalidation.mark_search_index_dirty",
                return_value=None,
            ) as mark_dirty,
            patch(
                "general_manager.search.invalidation.dispatch_index_update"
            ) as dispatch,
        ):
            with transaction.atomic(using="secondary"):
                self.SecondaryCommitManager.create(
                    name="secondary search", ignore_permission=True
                )
                mark_dirty.assert_not_called()
                dispatch.assert_not_called()

            mark_dirty.assert_called_once_with(
                self.SecondaryCommitManager,
                "global",
            )
            dispatch.assert_called_once()

    def test_unexpected_enqueue_failure_is_suppressed_after_secondary_commit(
        self,
    ) -> None:
        """Secondary on-commit callbacks fence arbitrary broker exceptions."""
        from unittest.mock import patch

        with (
            patch(
                "general_manager.search.invalidation.get_search_config",
                return_value=_TEST_SEARCH_CONFIG,
            ),
            patch(
                "general_manager.search.invalidation.mark_search_index_dirty",
                return_value=None,
            ),
            patch(
                "general_manager.search.invalidation.dispatch_index_update",
                side_effect=UnexpectedBrokerFailure("broker unavailable"),
            ),
            patch(
                "general_manager.search.invalidation.acknowledge_search_index_dirty"
            ) as acknowledge,
            self.assertLogs("general_manager.search.invalidation", level="WARNING"),
        ):
            created = self.SecondaryCommitManager.create(
                name="secondary committed despite broker",
                ignore_permission=True,
            )

        self.assertTrue(
            self.SecondaryCommitRecord.objects.using("secondary")
            .filter(
                pk=created.identification["id"],
                name="secondary committed despite broker",
            )
            .exists()
        )
        acknowledge.assert_not_called()

    def test_secondary_metadata_failure_marks_only_after_source_commit(self) -> None:
        """Metadata failures retain nondefault marker-only fallback semantics."""
        from unittest.mock import patch

        with (
            patch(
                "general_manager.search.invalidation.get_search_config",
                return_value=_TEST_SEARCH_CONFIG,
            ),
            patch(
                "general_manager.search.invalidation.deepcopy",
                side_effect=UnexpectedSearchExtensionFailure(),
            ),
            patch(
                "general_manager.search.invalidation.mark_search_index_dirty",
                return_value=None,
            ) as mark_dirty,
            patch(
                "general_manager.search.invalidation.dispatch_index_update"
            ) as dispatch,
            self.assertLogs("general_manager.search.invalidation", level="WARNING"),
        ):
            with transaction.atomic(using="secondary"):
                created = self.SecondaryCommitManager.create(
                    name="secondary metadata fallback",
                    ignore_permission=True,
                )
                mark_dirty.assert_not_called()
                dispatch.assert_not_called()

            mark_dirty.assert_called_once_with(
                self.SecondaryCommitManager,
                "global",
            )
            dispatch.assert_not_called()

        self.assertTrue(
            self.SecondaryCommitRecord.objects.using("secondary")
            .filter(pk=created.identification["id"])
            .exists()
        )
