"""Integration tests for standard M2M search invalidation operations."""

from __future__ import annotations

from importlib import import_module
from typing import ClassVar
from unittest.mock import MagicMock, patch

from django.apps import apps
from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.db import connections, models, transaction
from django.db.models.signals import m2m_changed
from django.test import TransactionTestCase

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import IndexConfig, SearchInvalidationRule
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.indexer import SearchIndexer
from general_manager.search.m2m_invalidation import (
    _dispatch_uid,
    compile_m2m_invalidation_bindings,
    configure_search_m2m_invalidation,
)


class M2MRollback(RuntimeError):
    """Intentional rollback marker."""


class SearchM2MInvalidationIntegrationTests(TransactionTestCase):
    """Exercise auto/custom through signals and commit-safe scheduling."""

    databases: ClassVar[set[str]] = {"default", "secondary"}

    @classmethod
    def _restore_secondary_connection(cls) -> None:
        """Restore the connection handler state replaced by this test class."""
        if cls._secondary_connection_restored:
            return
        cls._secondary_connection_restored = True
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
        """Create isolated ORM-backed endpoints and both through styles."""
        cls._secondary_original_config = connections.databases.get("secondary")
        cls._secondary_had_cached_connection = hasattr(
            connections._connections,
            "secondary",
        )
        cls._secondary_original_connection = (
            connections._connections.secondary
            if cls._secondary_had_cached_connection
            else None
        )
        cls._secondary_connection_restored = False
        cls.addClassCleanup(cls._restore_secondary_connection)
        if cls._secondary_had_cached_connection:
            del connections["secondary"]
        connections.databases["secondary"] = {
            **connections.databases["default"],
            "NAME": ":memory:",
        }
        super().setUpClass()
        cls._original_all_classes = list(GeneralManagerMeta.all_classes)
        cls._original_pending_attributes = list(
            GeneralManagerMeta.pending_attribute_initialization
        )
        cls._original_pending_graphql = list(
            GeneralManagerMeta.pending_graphql_interfaces
        )

        class M2MSearchSource(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=64)

        def serialize_auto_owner(instance: GeneralManager) -> dict[str, object]:
            """Serialize relation-derived content from the owner's live row."""
            owner_model = instance.Interface._model
            row = owner_model.objects.get(pk=instance.identification["id"])
            return {
                "name": row.name,
                "source_names": list(
                    row.sources.order_by("name").values_list("name", flat=True)
                ),
            }

        class M2MSearchAutoOwner(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=64)
                sources = models.ManyToManyField(
                    "general_manager.M2MSearchSource",
                    related_name="auto_owners",
                )

            class SearchConfig:
                indexes: ClassVar[tuple[IndexConfig, ...]] = (
                    IndexConfig(name="global", fields=["name", "source_names"]),
                )
                to_document = serialize_auto_owner
                invalidation_rules = (
                    SearchInvalidationRule(
                        source=M2MSearchSource,
                        relation="sources",
                    ),
                )

        class M2MSearchCustomOwner(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=64)
                sources = models.ManyToManyField(
                    "general_manager.M2MSearchSource",
                    through="general_manager.M2MSearchMembership",
                    related_name="custom_owners",
                )

            class SearchConfig:
                indexes: ClassVar[tuple[IndexConfig, ...]] = (
                    IndexConfig(name="global", fields=["name"]),
                )
                invalidation_rules = (
                    SearchInvalidationRule(
                        source=M2MSearchSource,
                        relation="sources",
                    ),
                )

        class M2MSearchMembership(models.Model):
            owner = models.ForeignKey(
                M2MSearchCustomOwner.Interface._model,
                on_delete=models.CASCADE,
            )
            source = models.ForeignKey(
                M2MSearchSource.Interface._model,
                on_delete=models.CASCADE,
            )

            class Meta:
                app_label = "general_manager"

        cls.Source = M2MSearchSource
        cls.AutoOwner = M2MSearchAutoOwner
        cls.CustomOwner = M2MSearchCustomOwner
        cls.Membership = M2MSearchMembership
        cls.SourceModel = M2MSearchSource.Interface._model
        cls.AutoOwnerModel = M2MSearchAutoOwner.Interface._model
        cls.CustomOwnerModel = M2MSearchCustomOwner.Interface._model
        cls.general_manager_classes = [
            M2MSearchSource,
            M2MSearchAutoOwner,
            M2MSearchCustomOwner,
        ]
        GeneralManagerMeta.all_classes = cls.general_manager_classes
        manager_module = import_module("general_manager.models")
        cls._manager_module = manager_module
        cls._prior_manager_exports = {
            manager.__name__: getattr(manager_module, manager.__name__, None)
            for manager in cls.general_manager_classes
        }
        for manager in cls.general_manager_classes:
            setattr(manager_module, manager.__name__, manager)
        for alias in ("default", "secondary"):
            database = connections[alias]
            database.connect()
            with database.schema_editor() as editor:
                if alias == "secondary":
                    editor.create_model(ContentType)
                    editor.create_model(Permission)
                    editor.create_model(Group)
                    editor.create_model(User)
                editor.create_model(cls.SourceModel)
                editor.create_model(cls.SourceModel.history.model)
                editor.create_model(cls.AutoOwnerModel)
                editor.create_model(cls.AutoOwnerModel.history.model)
                editor.create_model(cls.CustomOwnerModel)
                editor.create_model(cls.CustomOwnerModel.history.model)
                editor.create_model(M2MSearchMembership)
        cls.bindings = configure_search_m2m_invalidation()

    @classmethod
    def tearDownClass(cls) -> None:
        """Drop dynamic tables, receivers, models, and manager registries."""
        try:
            for binding in cls.bindings:
                m2m_changed.disconnect(
                    sender=binding.through_model,
                    dispatch_uid=_dispatch_uid(binding),
                )
            for alias in ("secondary", "default"):
                with connections[alias].schema_editor() as editor:
                    editor.delete_model(cls.Membership)
                    editor.delete_model(cls.CustomOwnerModel.history.model)
                    editor.delete_model(cls.CustomOwnerModel)
                    editor.delete_model(cls.AutoOwnerModel.history.model)
                    editor.delete_model(cls.AutoOwnerModel)
                    editor.delete_model(cls.SourceModel.history.model)
                    editor.delete_model(cls.SourceModel)
                    if alias == "secondary":
                        editor.delete_model(User)
                        editor.delete_model(Group)
                        editor.delete_model(Permission)
                        editor.delete_model(ContentType)
        finally:
            auto_through = cls.AutoOwnerModel.sources.through
            dynamic_models = (
                cls.Membership,
                cls.CustomOwnerModel,
                cls.CustomOwnerModel.history.model,
                cls.AutoOwnerModel,
                auto_through,
                cls.AutoOwnerModel.history.model,
                cls.SourceModel,
                cls.SourceModel.history.model,
            )
            app_config = apps.get_app_config("general_manager")
            for model in dynamic_models:
                model_name = model._meta.model_name
                if apps.all_models["general_manager"].get(model_name) is model:
                    apps.all_models["general_manager"].pop(model_name, None)
                if app_config.models.get(model_name) is model:
                    app_config.models.pop(model_name, None)
            apps.clear_cache()
            GeneralManagerMeta.all_classes = cls._original_all_classes
            GeneralManagerMeta.pending_attribute_initialization = (
                cls._original_pending_attributes
            )
            GeneralManagerMeta.pending_graphql_interfaces = (
                cls._original_pending_graphql
            )
            for name, prior in cls._prior_manager_exports.items():
                if prior is None:
                    delattr(cls._manager_module, name)
                else:
                    setattr(cls._manager_module, name, prior)
            super().tearDownClass()

    def setUp(self) -> None:
        """Create two owners and sources through the ORM endpoints."""
        super().setUp()
        self.source_a = self.SourceModel.objects.create(name="source-a")
        self.source_b = self.SourceModel.objects.create(name="source-b")
        self.auto_owner_a = self.AutoOwnerModel.objects.create(name="auto-a")
        self.auto_owner_b = self.AutoOwnerModel.objects.create(name="auto-b")
        self.custom_owner = self.CustomOwnerModel.objects.create(name="custom")

    def test_secondary_connection_cleanup_is_idempotent(self) -> None:
        """Repeated cleanup leaves the restored cached connection untouched."""

        class CleanupProbe(type(self)):
            pass

        current_connection = MagicMock()
        original_connection = MagicMock()
        original_config = object()
        cached_connections = MagicMock()
        cached_connections.secondary = current_connection
        fake_connections = MagicMock()
        fake_connections._connections = cached_connections
        fake_connections.databases = {"secondary": object()}
        fake_connections.__getitem__.side_effect = lambda alias: getattr(
            cached_connections, alias
        )
        fake_connections.__delitem__.side_effect = lambda alias: delattr(
            cached_connections, alias
        )
        CleanupProbe._secondary_connection_restored = False
        CleanupProbe._secondary_original_config = original_config
        CleanupProbe._secondary_had_cached_connection = True
        CleanupProbe._secondary_original_connection = original_connection

        with patch(f"{__name__}.connections", fake_connections):
            CleanupProbe._restore_secondary_connection()
            restored_config = fake_connections.databases["secondary"]
            restored_connection = cached_connections.secondary

            CleanupProbe._restore_secondary_connection()

        current_connection.close.assert_called_once_with()
        original_connection.close.assert_not_called()
        assert fake_connections.databases["secondary"] is restored_config
        assert cached_connections.secondary is restored_connection
        assert restored_config is original_config
        assert restored_connection is original_connection

    def test_secondary_connection_cleanup_survives_setup_failure(self) -> None:
        """Class cleanup restores secondary state when setup fails after mutation."""

        class SetupFailureProbe(type(self)):
            pass

        original_connection = MagicMock()
        original_config = object()
        cached_connections = MagicMock()
        cached_connections.secondary = original_connection
        fake_connections = MagicMock()
        fake_connections._connections = cached_connections
        fake_connections.databases = {
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
            "secondary": original_config,
        }
        fake_connections.__getitem__.side_effect = lambda alias: getattr(
            cached_connections, alias
        )
        fake_connections.__delitem__.side_effect = lambda alias: delattr(
            cached_connections, alias
        )

        with (
            patch(f"{__name__}.connections", fake_connections),
            patch.object(
                TransactionTestCase,
                "setUpClass",
                side_effect=RuntimeError("setup failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "setup failed"),
        ):
            SetupFailureProbe.setUpClass()

        with patch(f"{__name__}.connections", fake_connections):
            SetupFailureProbe.doClassCleanups()
            SetupFailureProbe._restore_secondary_connection()

        original_connection.close.assert_not_called()
        assert fake_connections.databases["secondary"] is original_config
        assert cached_connections.secondary is original_connection

    def test_compiler_binds_exact_auto_and_custom_through_fields(self) -> None:
        """Startup compilation records the real Django through metadata."""
        bindings = compile_m2m_invalidation_bindings([self.AutoOwner, self.CustomOwner])

        assert len(bindings) == 2
        by_owner = {binding.owner_manager: binding for binding in bindings}
        auto = by_owner[self.AutoOwner]
        custom = by_owner[self.CustomOwner]
        assert auto.through_model is self.AutoOwnerModel.sources.through
        assert (auto.owner_through_field, auto.source_through_field) == (
            "m2msearchautoowner",
            "m2msearchsource",
        )
        assert custom.through_model is self.Membership
        assert (custom.owner_through_field, custom.source_through_field) == (
            "owner",
            "source",
        )

    def test_auto_through_forward_add_remove_and_clear_target_owner(self) -> None:
        """Every standard forward operation schedules the exact owner id."""
        relation = self.auto_owner_a.sources
        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            relation.add(self.source_a, self.source_b)
            relation.remove(self.source_a)
            relation.clear()

        assert [call.args[2] for call in dispatch.call_args_list] == [
            ({"id": self.auto_owner_a.pk},),
            ({"id": self.auto_owner_a.pk},),
            ({"id": self.auto_owner_a.pk},),
        ]

    def test_auto_through_reverse_add_remove_and_clear_target_owners(self) -> None:
        """Reverse operations use owner ids, including pre-clear through rows."""
        reverse = self.source_a.auto_owners
        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            reverse.add(self.auto_owner_a, self.auto_owner_b)
            reverse.remove(self.auto_owner_a)
            reverse.clear()

        assert [call.args[2] for call in dispatch.call_args_list] == [
            ({"id": self.auto_owner_a.pk}, {"id": self.auto_owner_b.pk}),
            ({"id": self.auto_owner_a.pk},),
            ({"id": self.auto_owner_b.pk},),
        ]

    def test_set_clear_false_and_true_leave_fresh_final_owner(self) -> None:
        """Django set variants may duplicate work but both cover final state."""
        relation = self.auto_owner_a.sources
        relation.add(self.source_a)

        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            relation.set([self.source_b], clear=False)
            relation.set([self.source_a], clear=True)

        assert relation.filter(pk=self.source_a.pk).exists()
        assert not relation.filter(pk=self.source_b.pk).exists()
        assert dispatch.call_count >= 3
        assert all(
            call.args[2] == ({"id": self.auto_owner_a.pk},)
            for call in dispatch.call_args_list
        )

    def test_set_variants_refresh_relation_derived_dev_search_document(self) -> None:
        """Real batch indexing observes final related content after both set modes."""
        backend = DevSearchBackend()
        configure_search_backend(backend)
        self.addCleanup(configure_search_backend, None)
        SearchIndexer(backend).reindex_manager(self.AutoOwner)

        def indexed_source_names() -> list[str]:
            result = backend.search(
                "global",
                "",
                filters={"name": self.auto_owner_a.name},
            )
            assert result.total == 1
            return result.hits[0].data["source_names"]  # type: ignore[return-value]

        assert indexed_source_names() == []

        self.auto_owner_a.sources.set([self.source_a], clear=False)
        assert indexed_source_names() == ["source-a"]

        self.auto_owner_a.sources.set([self.source_b], clear=True)
        assert indexed_source_names() == ["source-b"]

    def test_outer_rollback_discards_m2m_dispatch(self) -> None:
        """The scheduler callback follows the relation transaction rollback."""
        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            with self.assertRaises(M2MRollback):
                with transaction.atomic():
                    self.auto_owner_a.sources.add(self.source_a)
                    raise M2MRollback

        dispatch.assert_not_called()
        assert not self.auto_owner_a.sources.exists()

    def test_configured_secondary_alias_commits_and_rolls_back_on_that_alias(
        self,
    ) -> None:
        """M2M callbacks follow the concrete signal alias, including rollback."""
        source = self.SourceModel.objects.using("secondary").create(name="secondary")
        owner = self.AutoOwnerModel.objects.using("secondary").create(
            name="secondary-owner"
        )

        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            with transaction.atomic(using="secondary"):
                owner.sources.add(source)
                dispatch.assert_not_called()
            dispatch.assert_called_once()

            with self.assertRaises(M2MRollback):
                with transaction.atomic(using="secondary"):
                    owner.sources.remove(source)
                    raise M2MRollback

        dispatch.assert_called_once()
        assert owner.sources.using("secondary").filter(pk=source.pk).exists()

    def test_custom_through_relation_manager_add_remove_and_clear_is_supported(
        self,
    ) -> None:
        """Custom through models work through Django's public relation manager."""
        relation = self.custom_owner.sources
        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            relation.add(self.source_a)
            relation.remove(self.source_a)
            relation.add(self.source_b)
            relation.clear()

        assert dispatch.call_count == 4
        assert all(
            call.args[2] == ({"id": self.custom_owner.pk},)
            for call in dispatch.call_args_list
        )

    def test_custom_through_reverse_operations_target_owner_ids(self) -> None:
        """The reverse manager is supported for an exact custom through model."""
        reverse = self.source_a.custom_owners
        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            reverse.add(self.custom_owner)
            reverse.remove(self.custom_owner)
            reverse.add(self.custom_owner)
            reverse.clear()

        assert dispatch.call_count == 4
        assert all(
            call.args[2] == ({"id": self.custom_owner.pk},)
            for call in dispatch.call_args_list
        )

    def test_reverse_clear_reads_limit_plus_one_and_degrades_on_overflow(self) -> None:
        """Reverse clear captures a bounded prefix and emits no targeted work."""
        extra_owner = self.AutoOwnerModel.objects.create(name="auto-extra")
        through = self.AutoOwnerModel.sources.through
        owner_field = self.AutoOwnerModel._meta.get_field("sources").m2m_field_name()
        source_field = self.AutoOwnerModel._meta.get_field(
            "sources"
        ).m2m_reverse_field_name()
        through.objects.bulk_create(
            [
                through(**{owner_field: owner, source_field: self.source_a})
                for owner in (self.auto_owner_a, self.auto_owner_b, extra_owner)
            ]
        )

        with (
            patch(
                "general_manager.search.m2m_invalidation.get_search_invalidation_max_targets",
                return_value=2,
            ),
            patch(
                "general_manager.search.m2m_invalidation.schedule_search_invalidation_work"
            ) as schedule,
        ):
            self.source_a.auto_owners.clear()

        schedule.assert_called_once()
        work = schedule.call_args.args[0]
        assert work.upserts.targets == ()
        assert [pair.index_name for pair in work.upserts.dirty_fallbacks] == ["global"]

    def test_direct_through_writes_are_explicitly_not_intercepted(self) -> None:
        """Direct through-table writes remain an unsupported no-signal path."""
        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            self.Membership.objects.create(
                owner=self.custom_owner,
                source=self.source_a,
            )

        dispatch.assert_not_called()
