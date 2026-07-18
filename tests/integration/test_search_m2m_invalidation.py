"""Integration tests for standard M2M search invalidation operations."""

from __future__ import annotations

from contextlib import suppress
from importlib import import_module
from typing import ClassVar
from unittest.mock import MagicMock, patch

from django.apps import apps
from django.db import connections, models, transaction
from django.db.models.signals import m2m_changed
from django.test import SimpleTestCase, TransactionTestCase

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import IndexConfig, SearchInvalidationRule
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.indexer import SearchIndexer
from general_manager.search.m2m_invalidation import (
    M2MInvalidationBinding,
    _dispatch_uid,
    compile_m2m_invalidation_bindings,
    configure_search_m2m_invalidation,
)
from tests.utils.database import create_test_models, drop_test_models


class M2MRollback(RuntimeError):
    """Intentional rollback marker."""


class SearchM2MInvalidationIntegrationTests(TransactionTestCase):
    """Exercise auto/custom through signals and commit-safe scheduling."""

    databases: ClassVar[set[str]] = {"default", "secondary"}
    bindings: ClassVar[tuple[M2MInvalidationBinding, ...]] = ()
    _created_models_by_alias: ClassVar[dict[str, list[type[models.Model]]]] = {}
    _registered_models: ClassVar[list[type[models.Model]]] = []

    @classmethod
    def setUpClass(cls) -> None:
        """Create ORM-backed endpoints and both through styles."""
        super().setUpClass()
        cls.bindings = ()
        cls._created_models_by_alias = {}
        cls._registered_models = []
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
        cls._registered_models = [
            cls.Membership,
            cls.CustomOwnerModel,
            cls.CustomOwnerModel.history.model,
            cls.AutoOwnerModel,
            cls.AutoOwnerModel.sources.through,
            cls.AutoOwnerModel.history.model,
            cls.SourceModel,
            cls.SourceModel.history.model,
        ]
        schema_models = (
            cls.SourceModel,
            cls.SourceModel.history.model,
            cls.AutoOwnerModel,
            cls.AutoOwnerModel.history.model,
            cls.CustomOwnerModel,
            cls.CustomOwnerModel.history.model,
            cls.Membership,
        )
        try:
            for alias in ("default", "secondary"):
                database = connections[alias]
                database.connect()
                cls._created_models_by_alias[alias] = create_test_models(
                    database,
                    schema_models,
                )
            cls.bindings = configure_search_m2m_invalidation()
        except Exception:
            with suppress(Exception):
                cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        """Drop dynamic tables, receivers, models, and manager registries."""
        cleanup_error: Exception | None = None
        for binding in cls.bindings:
            try:
                m2m_changed.disconnect(
                    sender=binding.through_model,
                    dispatch_uid=_dispatch_uid(binding),
                )
            except Exception as error:  # noqa: BLE001 - cleanup must continue.
                if cleanup_error is None:
                    cleanup_error = error

        for alias in ("secondary", "default"):
            created_models = cls._created_models_by_alias.get(alias, ())
            if not created_models:
                continue
            try:
                with connections[alias].schema_editor() as editor:
                    drop_test_models(editor, reversed(created_models))
            except Exception as error:  # noqa: BLE001 - cleanup must continue.
                if cleanup_error is None:
                    cleanup_error = error
        cls._created_models_by_alias = {}

        try:
            app_config = apps.get_app_config("general_manager")
            for model in cls._registered_models:
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
                    if hasattr(cls._manager_module, name):
                        delattr(cls._manager_module, name)
                else:
                    setattr(cls._manager_module, name, prior)
        except Exception as error:  # noqa: BLE001 - superclass cleanup must run.
            if cleanup_error is None:
                cleanup_error = error
        cls._registered_models = []

        try:
            super().tearDownClass()
        except Exception as error:  # noqa: BLE001 - preserve the first failure.
            if cleanup_error is None:
                cleanup_error = error

        if cleanup_error is not None:
            raise cleanup_error

    def setUp(self) -> None:
        """Create two owners and sources through the ORM endpoints."""
        super().setUp()
        self.source_a = self.SourceModel.objects.create(name="source-a")
        self.source_b = self.SourceModel.objects.create(name="source-b")
        self.auto_owner_a = self.AutoOwnerModel.objects.create(name="auto-a")
        self.auto_owner_b = self.AutoOwnerModel.objects.create(name="auto-b")
        self.custom_owner = self.CustomOwnerModel.objects.create(name="custom")

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


class SearchM2MInvalidationLifecycleTests(SimpleTestCase):
    """Regression tests for two-alias dynamic schema cleanup."""

    def test_teardown_cleans_both_aliases_and_restores_registries(self) -> None:
        """Later cleanup runs while the first alias error remains authoritative."""
        first_model = type("FirstM2MModel", (), {})
        second_model = type("SecondM2MModel", (), {})
        created_models = [first_model, second_model]
        registered_model = MagicMock()
        registered_model._meta.model_name = "registered_model"
        registered_models = {"registered_model": registered_model}
        fake_apps = MagicMock()
        fake_apps.all_models = {"general_manager": registered_models}
        fake_apps.get_app_config.return_value.models = registered_models.copy()
        secondary_error = RuntimeError("secondary membership drop failed")
        default_error = RuntimeError("default membership drop failed")
        secondary = MagicMock()
        default = MagicMock()
        original_all_classes = [object()]
        original_pending_attributes = [object()]
        original_pending_graphql = [object()]

        class CleanupProbe(SearchM2MInvalidationIntegrationTests):
            bindings = ()
            _created_models_by_alias: ClassVar[dict[str, list[type]]] = {
                "default": created_models,
                "secondary": created_models,
            }
            _registered_models: ClassVar[list[object]] = [registered_model]
            _prior_manager_exports: ClassVar[dict[str, object]] = {}
            _original_all_classes = original_all_classes
            _original_pending_attributes = original_pending_attributes
            _original_pending_graphql = original_pending_graphql

        with (
            patch(f"{__name__}.apps", fake_apps),
            patch(
                f"{__name__}.connections",
                {"default": default, "secondary": secondary},
            ),
            patch(
                f"{__name__}.drop_test_models",
                side_effect=(secondary_error, default_error),
            ) as drop_models,
            patch.object(GeneralManagerMeta, "all_classes", [object()]),
            patch.object(
                GeneralManagerMeta,
                "pending_attribute_initialization",
                [object()],
            ),
            patch.object(
                GeneralManagerMeta,
                "pending_graphql_interfaces",
                [object()],
            ),
            patch.object(TransactionTestCase, "tearDownClass") as superclass_teardown,
        ):
            with self.assertRaises(RuntimeError) as raised:
                CleanupProbe.tearDownClass()

            self.assertIs(raised.exception, secondary_error)
            self.assertEqual(
                [list(call.args[1]) for call in drop_models.call_args_list],
                [[second_model, first_model], [second_model, first_model]],
            )
            secondary.schema_editor.assert_called_once_with()
            default.schema_editor.assert_called_once_with()
            self.assertEqual(fake_apps.all_models["general_manager"], {})
            self.assertEqual(fake_apps.get_app_config.return_value.models, {})
            self.assertIs(GeneralManagerMeta.all_classes, original_all_classes)
            self.assertIs(
                GeneralManagerMeta.pending_attribute_initialization,
                original_pending_attributes,
            )
            self.assertIs(
                GeneralManagerMeta.pending_graphql_interfaces,
                original_pending_graphql,
            )
            fake_apps.clear_cache.assert_called_once_with()
            superclass_teardown.assert_called_once_with()
