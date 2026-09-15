"""Focused coverage for opt-in SQL persistence in ``create_many``."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import patch

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db.models import CharField, IntegerField
from django.db.models import NOT_PROVIDED
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext
from django.db import connection

from general_manager.cache.batch_refresh import connect_batch_refresh_receiver
from general_manager.interface import DatabaseInterface
from general_manager.interface.capabilities.orm.bulk import bulk_create_eligibility
from general_manager.manager.bulk_create import (
    CreateManyError,
    CreateManyPostCommitError,
    CreateManyUnsupportedError,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class BulkSqlCreateIntegrationTests(GeneralManagerTransactionTestCase):
    """Exercise the narrow generated-model SQL path on SQLite."""

    Item: ClassVar[type[GeneralManager]]
    ItemModel: ClassVar[type[object]]

    @classmethod
    def setUpClass(cls) -> None:
        class Item(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100, unique=True)
                note = CharField(max_length=100, default="default note")
                group = CharField(max_length=100, null=True, blank=True, default=None)
                sequence = IntegerField(default=0)

                class Meta:
                    unique_together = (("group", "sequence"),)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

            class BulkCreate:
                enabled = True
                local_permissions = True
                local_search = True

        cls.Item = Item
        cls.ItemModel = Item.Interface._model
        cls.general_manager_classes = [Item]
        GeneralManagerMeta.all_classes = cls.general_manager_classes
        super().setUpClass()

    def test_opted_in_generated_manager_uses_bulk_source_and_history_rows(self) -> None:
        eligibility = bulk_create_eligibility(self.Item)
        self.assertTrue(eligibility.eligible, eligibility.reasons)
        with CaptureQueriesContext(connection) as queries:
            result = list(
                self.Item.create_many(
                    [{"name": "one"}, {"name": "two"}],
                    ignore_permission=True,
                )
            )
        self.assertEqual(
            result[0].ids, tuple(self.ItemModel.objects.values_list("id", flat=True))
        )
        self.assertEqual(self.ItemModel.history.filter(history_type="+").count(), 2)
        inserts = [
            query["sql"].upper()
            for query in queries.captured_queries
            if "INSERT" in query["sql"].upper()
        ]
        self.assertEqual(len(inserts), 2)

    def test_missing_opt_in_declaration_falls_back_with_a_reason(self) -> None:
        class NonOptedIn(self.Item):
            class BulkCreate:
                enabled = True
                local_search = True

        eligibility = bulk_create_eligibility(NonOptedIn)
        self.assertFalse(eligibility.eligible)
        self.assertIn(
            "BulkCreate.local_permissions must be exactly True", eligibility.reasons
        )

    def test_sql_refresh_dispatch_failure_retains_committed_rows_and_ids(self) -> None:
        failure = RuntimeError("refresh unavailable")

        def fail_refresh(*_: object) -> None:
            raise failure

        registration = connect_batch_refresh_receiver(fail_refresh, on_commit=True)
        self.addCleanup(registration.disconnect)
        with self.assertRaises(CreateManyPostCommitError) as raised:
            list(
                self.Item.create_many(
                    [{"name": "committed-one"}, {"name": "committed-two"}],
                    ignore_permission=True,
                )
            )
        error = raised.exception
        self.assertIs(error.cause, failure)
        self.assertTrue(error.committed)
        self.assertEqual(error.committed_successful_count, 2)
        self.assertEqual(
            set(error.ids), set(self.ItemModel.objects.values_list("id", flat=True))
        )
        self.assertEqual(self.ItemModel.history.count(), 2)

    def test_batch_refresh_receiver_runs_once_for_bulk_and_once_per_mutation(
        self,
    ) -> None:
        calls: list[tuple[type[GeneralManager], tuple[object, ...], str, str]] = []
        disconnect = connect_batch_refresh_receiver(
            lambda sender, identifications, action, database_alias: calls.append(
                (sender, identifications, action, database_alias)
            )
        )
        self.addCleanup(disconnect)

        list(
            self.Item.create_many(
                [{"name": "batch-a"}, {"name": "batch-b"}],
                ignore_permission=True,
            )
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], self.Item)
        self.assertEqual(calls[0][2:], ("create", "default"))
        self.assertEqual(len(calls[0][1]), 2)

        self.Item.create(name="ordinary", ignore_permission=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][2:], ("create", "default"))
        self.assertEqual(len(calls[1][1]), 1)

    def test_eligibility_is_rechecked_between_lazy_batches(self) -> None:
        iterator = self.Item.create_many(
            [{"name": "first"}, {"name": "second"}],
            ignore_permission=True,
            batch_size=1,
        )
        next(iterator)
        observed: list[str] = []

        def custom_post_save(sender: object, instance: object, **_: object) -> None:
            del sender, instance
            observed.append("called")

        post_save.connect(custom_post_save, sender=self.ItemModel, weak=False)
        self.addCleanup(post_save.disconnect, custom_post_save, self.ItemModel)

        next(iterator)
        self.assertEqual(observed, ["called"])

    def test_custom_scalar_field_runs_its_cleaner_through_fallback(self) -> None:
        cleaned: list[object] = []

        class CustomCharField(CharField):
            def clean(self, value: object, model_instance: object) -> object:
                cleaned.append(value)
                return super().clean(value, model_instance)

        field = self.ItemModel._meta.get_field("name")
        original_type = type(field)
        field.__class__ = CustomCharField
        try:
            eligibility = bulk_create_eligibility(self.Item)
            self.assertFalse(eligibility.eligible)
            self.assertIn("custom or unsupported model field", eligibility.reasons)
            list(
                self.Item.create_many(
                    [{"name": "custom-a"}, {"name": "custom-b"}], ignore_permission=True
                )
            )
        finally:
            field.__class__ = original_type
        self.assertEqual(cleaned, ["custom-a", "custom-b"])

    def test_history_model_save_receiver_is_preserved_by_fallback(self) -> None:
        history_model = self.ItemModel.history.model
        created_history: list[object] = []

        def record_history(
            sender: object, instance: object, created: bool, **_: object
        ) -> None:
            if created:
                created_history.append(instance)

        post_save.connect(record_history, sender=history_model, weak=False)
        self.addCleanup(post_save.disconnect, record_history, history_model)
        self.assertFalse(bulk_create_eligibility(self.Item).eligible)
        list(
            self.Item.create_many(
                [{"name": "audit-a"}, {"name": "audit-b"}], ignore_permission=True
            )
        )
        self.assertEqual(len(created_history), 2)

    def test_inactive_framework_consumers_do_not_require_manager_construction(
        self,
    ) -> None:
        from general_manager.api.graphql import GraphQL

        with (
            patch.object(GraphQL, "manager_registry", {}),
            patch.object(
                self.Item,
                "_from_trusted_orm_instance",
                side_effect=AssertionError("unnecessary manager hydration"),
            ),
        ):
            self.assertTrue(bulk_create_eligibility(self.Item).eligible)
            results = list(
                self.Item.create_many(
                    [{"name": "ids-a"}, {"name": "ids-b"}], ignore_permission=True
                )
            )
        self.assertEqual(len(results[0].ids), 2)

    def test_not_provided_preserves_model_defaults_for_source_and_history(self) -> None:
        results = list(
            self.Item.create_many(
                [
                    {"name": "default-one", "note": NOT_PROVIDED},
                    {"name": "default-two", "note": NOT_PROVIDED},
                ],
                ignore_permission=True,
            )
        )
        self.assertEqual(len(results[0].ids), 2)
        self.assertEqual(
            list(
                self.ItemModel.objects.filter(name__startswith="default-")
                .order_by("name")
                .values_list("note", flat=True)
            ),
            ["default note", "default note"],
        )
        self.assertEqual(
            list(
                self.ItemModel.history.filter(name__startswith="default-")
                .order_by("name")
                .values_list("note", flat=True)
            ),
            ["default note", "default note"],
        )

    def test_not_provided_has_the_same_defaults_in_canonical_fallback(self) -> None:
        def force_canonical(sender: object, **_: object) -> None:
            del sender

        post_save.connect(force_canonical, sender=self.ItemModel, weak=False)
        self.addCleanup(post_save.disconnect, force_canonical, self.ItemModel)
        self.assertFalse(bulk_create_eligibility(self.Item).eligible)
        list(
            self.Item.create_many(
                [
                    {"name": "canonical-default-one", "note": NOT_PROVIDED},
                    {"name": "canonical-default-two", "note": NOT_PROVIDED},
                ],
                ignore_permission=True,
            )
        )
        self.assertEqual(
            list(
                self.ItemModel.objects.filter(name__startswith="canonical-default-")
                .order_by("name")
                .values_list("note", flat=True)
            ),
            ["default note", "default note"],
        )
        self.assertEqual(
            list(
                self.ItemModel.history.filter(name__startswith="canonical-default-")
                .order_by("name")
                .values_list("note", flat=True)
            ),
            ["default note", "default note"],
        )

    def test_composite_validation_chunks_a_thousand_values(self) -> None:
        results = list(
            self.Item.create_many(
                [
                    {
                        "name": f"chunked-{index}",
                        "group": f"group-{index}",
                        "sequence": index,
                    }
                    for index in range(1000)
                ],
                ignore_permission=True,
                batch_size=1000,
            )
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].ids), 1000)
        self.assertEqual(self.ItemModel.objects.count(), 1000)

    def test_earliest_existing_composite_conflict_wins_over_later_duplicate(
        self,
    ) -> None:
        list(
            self.Item.create_many(
                [{"name": "existing-composite", "group": "taken", "sequence": 7}],
                ignore_permission=True,
            )
        )
        records = [
            {"name": "earliest-existing", "group": "taken", "sequence": 7},
            {"name": "later-duplicate", "group": "fresh", "sequence": 1},
            {"name": "later-duplicate", "group": "another", "sequence": 2},
        ]
        with self.assertRaises(CreateManyError) as raised:
            list(self.Item.create_many(records, ignore_permission=True, batch_size=3))
        self.assertEqual(raised.exception.failure_index, 0)
        self.assertIsInstance(raised.exception.cause, ValidationError)
        self.assertIn(NON_FIELD_ERRORS, raised.exception.cause.message_dict)

    def test_non_concrete_input_key_is_rejected_without_silent_assignment(self) -> None:
        with self.assertRaises(CreateManyError) as raised:
            list(
                self.Item.create_many(
                    [{"name": "extension-key", "history": "ignored"}],
                    ignore_permission=True,
                )
            )
        self.assertEqual(raised.exception.failure_index, 0)
        self.assertIsInstance(raised.exception.cause, CreateManyUnsupportedError)
        self.assertIn("Disable BulkCreate.enabled", str(raised.exception.cause))

    def test_custom_history_attribution_hook_forces_fallback(self) -> None:
        type.__setattr__(self.ItemModel, "_history_date", object())
        self.addCleanup(delattr, self.ItemModel, "_history_date")
        eligibility = bulk_create_eligibility(self.Item)
        self.assertFalse(eligibility.eligible)
        self.assertIn(
            "custom history attribution or timestamp hook", eligibility.reasons
        )

    def test_inherited_history_hooks_force_fallback(self) -> None:
        base = self.ItemModel.__mro__[1]
        for name in ("_history_date", "_change_reason", "_history_user"):
            with self.subTest(hook=name):
                with patch.object(base, name, property(lambda _: None), create=True):
                    eligibility = bulk_create_eligibility(self.Item)
                    self.assertFalse(eligibility.eligible)
                    self.assertIn(
                        "custom history attribution or timestamp hook",
                        eligibility.reasons,
                    )
