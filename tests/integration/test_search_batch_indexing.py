from __future__ import annotations

from typing import ClassVar
from unittest.mock import Mock

from django.apps import apps
from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.db import connections, models
from django.test import TransactionTestCase

from general_manager.apps import GeneralmanagerConfig
from general_manager.interface import DatabaseInterface, ExistingModelInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import (
    MissingBatchManagerError,
    SearchIndexer,
)


class SearchBatchIndexingDatabaseRoutingTests(TransactionTestCase):
    """Exercise the exact-index batch path through a real secondary ORM alias."""

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
        """Configure an isolated secondary database and real ORM manager."""
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
        super().setUpClass()
        cls._original_all_classes = list(GeneralManagerMeta.all_classes)
        cls._original_pending_graphql = list(
            GeneralManagerMeta.pending_graphql_interfaces
        )

        class SearchBatchOwnerInterface(DatabaseInterface):
            name = models.CharField(max_length=64)
            database = "secondary"

            class Meta:
                app_label = "general_manager"

        class SearchBatchOwner(GeneralManager):
            __module__ = "general_manager.models"
            Interface = SearchBatchOwnerInterface

            class SearchConfig:
                indexes: ClassVar[list[IndexConfig]] = [
                    IndexConfig(name="global", fields=["name"])
                ]

        class CustomPrimaryKeyRecord(models.Model):
            __module__ = "general_manager.models"
            code = models.CharField(max_length=32, primary_key=True)
            name = models.CharField(max_length=64)

            class Meta:
                app_label = "general_manager"

        class CustomPrimaryKeyInterface(ExistingModelInterface):
            model = CustomPrimaryKeyRecord
            database = "secondary"

        class CustomPrimaryKeyOwner(GeneralManager):
            __module__ = "general_manager.models"
            Interface = CustomPrimaryKeyInterface

            class SearchConfig:
                indexes: ClassVar[list[IndexConfig]] = [
                    IndexConfig(name="global", fields=["name"])
                ]

        cls.SearchBatchRecord = SearchBatchOwner.Interface._model
        cls.SearchBatchManager = SearchBatchOwner
        cls.CustomPrimaryKeyRecord = CustomPrimaryKeyRecord
        cls.CustomPrimaryKeyManager = CustomPrimaryKeyOwner
        GeneralManagerMeta.all_classes = [SearchBatchOwner, CustomPrimaryKeyOwner]
        GeneralmanagerConfig.initialize_general_manager_classes(
            [SearchBatchOwner, CustomPrimaryKeyOwner],
            [SearchBatchOwner, CustomPrimaryKeyOwner],
        )

        secondary = connections[alias]
        secondary.connect()
        with secondary.schema_editor() as editor:
            editor.create_model(ContentType)
            editor.create_model(Permission)
            editor.create_model(Group)
            editor.create_model(User)
            editor.create_model(cls.SearchBatchRecord)
            editor.create_model(cls.CustomPrimaryKeyRecord)

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the secondary table before restoring global test state."""
        try:
            with connections["secondary"].schema_editor() as editor:
                editor.delete_model(cls.CustomPrimaryKeyRecord)
                editor.delete_model(cls.SearchBatchRecord)
                editor.delete_model(User)
                editor.delete_model(Group)
                editor.delete_model(Permission)
                editor.delete_model(ContentType)
        finally:
            model = cls.SearchBatchRecord
            history_model = model.history.model
            custom_model = cls.CustomPrimaryKeyRecord
            custom_history_model = custom_model.history.model
            for registered_model in (
                model,
                history_model,
                custom_model,
                custom_history_model,
            ):
                app_label = registered_model._meta.app_label
                model_key = registered_model.__name__.lower()
                apps.all_models[app_label].pop(model_key, None)
                apps.get_app_config(app_label).models.pop(model_key, None)
            apps.clear_cache()
            GeneralManagerMeta.all_classes = cls._original_all_classes
            GeneralManagerMeta.pending_graphql_interfaces = (
                cls._original_pending_graphql
            )
            super().tearDownClass()

    def setUp(self) -> None:
        """Create three owners directly on the configured secondary alias."""
        super().setUp()
        records = [
            self.SearchBatchRecord(name="Alpha"),
            self.SearchBatchRecord(name="Beta"),
            self.SearchBatchRecord(name="Gamma"),
        ]
        self.SearchBatchRecord.objects.using("secondary").bulk_create(records)
        self.records = records
        custom_records = [
            self.CustomPrimaryKeyRecord(code="gamma", name="Gamma"),
            self.CustomPrimaryKeyRecord(code="alpha", name="Alpha"),
            self.CustomPrimaryKeyRecord(code="beta", name="Beta"),
        ]
        self.CustomPrimaryKeyRecord.objects.using("secondary").bulk_create(
            custom_records
        )
        self.custom_records = custom_records

    def test_batch_bulk_loads_once_from_configured_alias_and_restores_order(
        self,
    ) -> None:
        """One secondary query hydrates unique ids in first-requested order."""
        backend = Mock()
        requested = (
            {"id": self.records[2].pk},
            {"id": self.records[0].pk},
            {"id": self.records[1].pk},
            {"id": self.records[2].pk},
        )

        with (
            self.assertNumQueries(0, using="default"),
            self.assertNumQueries(1, using="secondary"),
        ):
            count = SearchIndexer(backend).index_manager_index_batch(
                self.SearchBatchManager,
                "global",
                requested,
            )

        assert count == 3
        backend.ensure_index.assert_called_once()
        backend.upsert.assert_called_once()
        assert backend.upsert.call_args.args[0] == "global"
        documents = backend.upsert.call_args.args[1]
        assert [document.identification for document in documents] == [
            {"id": self.records[2].pk},
            {"id": self.records[0].pk},
            {"id": self.records[1].pk},
        ]

    def test_missing_owner_fails_after_one_query_without_backend_write(self) -> None:
        """A missing secondary owner prevents every backend side effect."""
        backend = Mock()
        missing_id = max(record.pk for record in self.records) + 100

        with (
            self.assertNumQueries(0, using="default"),
            self.assertNumQueries(1, using="secondary"),
            self.assertRaises(MissingBatchManagerError),
        ):
            SearchIndexer(backend).index_manager_index_batch(
                self.SearchBatchManager,
                "global",
                ({"id": self.records[0].pk}, {"id": missing_id}),
            )

        backend.ensure_index.assert_not_called()
        backend.upsert.assert_not_called()

    def test_existing_model_custom_primary_key_bulk_loads_once_in_public_id_order(
        self,
    ) -> None:
        """The ORM fast path maps public ids onto a custom model primary key."""
        backend = Mock()
        assert tuple(self.CustomPrimaryKeyManager.Interface.input_fields) == ("id",)
        requested = (
            {"id": "beta"},
            {"id": "gamma"},
            {"id": "alpha"},
            {"id": "beta"},
        )

        with (
            self.assertNumQueries(0, using="default"),
            self.assertNumQueries(1, using="secondary"),
        ):
            count = SearchIndexer(backend).index_manager_index_batch(
                self.CustomPrimaryKeyManager,
                "global",
                requested,
            )

        assert count == 3
        backend.ensure_index.assert_called_once()
        backend.upsert.assert_called_once()
        documents = backend.upsert.call_args.args[1]
        assert [document.identification for document in documents] == [
            {"id": "beta"},
            {"id": "gamma"},
            {"id": "alpha"},
        ]
