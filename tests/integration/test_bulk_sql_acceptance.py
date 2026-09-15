"""Acceptance coverage for the opt-in SQL ``create_many`` contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import threading
from typing import Any, ClassVar
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, connection, connections, models, transaction
from django.db.models.query import QuerySet
from django.db.models.signals import post_save
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from simple_history.signals import post_create_historical_record

from general_manager.api.graphql import GraphQL
from general_manager.api.property import graph_ql_property
from general_manager.cache.batch_refresh import connect_batch_refresh_receiver
from general_manager.cache.signals import post_data_change
from general_manager.interface import DatabaseInterface
from general_manager.interface.capabilities.orm.bulk import (
    bulk_create_eligibility,
)
from general_manager.manager.bulk_create import CreateManyError
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.base_permission import PermissionCheckError
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.rule.rule import Rule
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from general_manager.workflow.event_registry import (
    DatabaseEventRegistry,
    InMemoryEventRegistry,
    configure_event_registry,
    get_event_registry,
)
from general_manager.workflow.models import WorkflowEventRecord, WorkflowOutbox
from general_manager.workflow.signal_bridge import (
    connect_workflow_signal_bridge,
    disconnect_workflow_signal_bridge,
)


class _ExpectedRollback(RuntimeError):
    """Intentional savepoint or outer-transaction rollback for a test."""


def _raise_expected_rollback() -> None:
    """Raise the test rollback marker through an inner helper."""
    raise _ExpectedRollback


def _positive_value(item: Any) -> bool:
    """Require the local ORM rule to accept only non-negative values."""
    return item.value >= 0


class BulkSqlAcceptanceIntegrationTests(GeneralManagerTransactionTestCase):
    """Exercise the real SQL path and its canonical fallback boundaries."""

    Target: ClassVar[type[GeneralManager]]
    TargetModel: ClassVar[type[models.Model]]
    Item: ClassVar[type[GeneralManager]]
    ItemModel: ClassVar[type[models.Model]]
    CanonicalItem: ClassVar[type[GeneralManager]]
    ToFieldItem: ClassVar[type[GeneralManager]]
    ToFieldItemModel: ClassVar[type[models.Model]]

    @classmethod
    def setUpClass(cls) -> None:
        user_model = get_user_model()

        class Target(GeneralManager):
            class Interface(DatabaseInterface):
                code = models.CharField(max_length=40, unique=True)
                label = models.CharField(max_length=100)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

        class Item(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100, unique=True)
                value = models.IntegerField(default=0)
                group = models.CharField(max_length=100)
                sequence = models.IntegerField(default=0)
                owner = models.ForeignKey(user_model, on_delete=models.CASCADE)
                target = models.ForeignKey(
                    Target.Interface._model,
                    on_delete=models.CASCADE,
                    related_name="bulk_sql_items",
                )

                class Meta:
                    rules: ClassVar[list[Rule[Any]]] = [Rule(_positive_value)]
                    unique_together = (("group", "sequence"),)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["isAuthenticated"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]
                seen_create_payloads: ClassVar[list[dict[str, object]]] = []

                @classmethod
                def check_create_permission(
                    permission_cls: type[ManagerBasedPermission],
                    data: dict[str, object],
                    manager: type[GeneralManager],
                    request_user: object,
                ) -> None:
                    permission_cls.seen_create_payloads.append(dict(data))
                    super(Item.Permission, permission_cls).check_create_permission(
                        data, manager, request_user
                    )

            class BulkCreate:
                enabled = True
                local_rules = True
                local_permissions = True
                local_search = True

            @graph_ql_property(cache="dependency")
            def row_count(self) -> int:
                """Expose a dependency-cached read for refresh callback checks."""
                return type(self).all().count()

        class CanonicalItem(Item):
            class BulkCreate:
                enabled = False

        class ToFieldItem(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100, unique=True)
                value = models.IntegerField(default=0)
                owner = models.ForeignKey(user_model, on_delete=models.CASCADE)
                target = models.ForeignKey(
                    Target.Interface._model,
                    on_delete=models.CASCADE,
                    related_name="bulk_sql_to_field_items",
                )
                target_by_code = models.ForeignKey(
                    Target.Interface._model,
                    to_field="code",
                    db_column="target_by_code",
                    on_delete=models.CASCADE,
                    related_name="bulk_sql_code_items",
                )

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

            class BulkCreate:
                enabled = True
                local_permissions = True
                local_search = True

        cls.Target = Target
        cls.TargetModel = Target.Interface._model
        cls.Item = Item
        cls.ItemModel = Item.Interface._model
        cls.CanonicalItem = CanonicalItem
        cls.ToFieldItem = ToFieldItem
        cls.ToFieldItemModel = ToFieldItem.Interface._model
        cls.general_manager_classes = [Target, Item, CanonicalItem, ToFieldItem]
        GeneralManagerMeta.all_classes = cls.general_manager_classes
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username=f"bulk-sql-{self._testMethodName}",
            password=f"{self._testMethodName}-password",
        )
        self.target = self.Target.create(
            code=f"target-{uuid4().hex[:10]}",
            label="Shared target",
            ignore_permission=True,
        )
        self.Item.Permission.seen_create_payloads.clear()

    def _record(
        self,
        name: str,
        *,
        value: int = 1,
        group: str | None = None,
        sequence: int = 1,
    ) -> dict[str, object]:
        return {
            "name": name,
            "value": value,
            "group": group or f"group-{name}",
            "sequence": sequence,
            "owner": self.user,
            "target": self.target,
        }

    def _record_ids(
        self,
        name: str,
        *,
        value: int = 1,
        group: str | None = None,
        sequence: int = 1,
    ) -> dict[str, object]:
        return {
            "name": name,
            "value": value,
            "group": group or f"group-{name}",
            "sequence": sequence,
            "owner_id": self.user.pk,
            "target_id": self.target.identification["id"],
        }

    def _to_field_record(
        self,
        name: str,
        *,
        value: int = 1,
        target_by_code: str | None = None,
    ) -> dict[str, object]:
        return {
            "name": name,
            "value": value,
            "owner": self.user,
            "target": self.target,
            "target_by_code": target_by_code or self.target.code,
        }

    @staticmethod
    def _insert_queries(captured: list[dict[str, object]]) -> list[str]:
        return [
            str(query["sql"])
            for query in captured
            if "INSERT" in str(query["sql"]).upper()
        ]

    def _configure_workflow(self) -> list[object]:
        prior_registry = get_event_registry()
        registry = InMemoryEventRegistry()
        events: list[object] = []
        registry.register(
            "manager_created",
            handler=events.append,
            registration_id=f"bulk-sql-acceptance-{self._testMethodName}",
        )
        connect_workflow_signal_bridge(registry=registry)
        self.addCleanup(disconnect_workflow_signal_bridge)
        self.addCleanup(configure_event_registry, prior_registry)
        return events

    def test_eligible_sql_batch_is_frozen_and_inserts_source_and_history_once(
        self,
    ) -> None:
        eligibility = bulk_create_eligibility(self.Item)
        self.assertTrue(eligibility.eligible, eligibility.reasons)
        self.assertIsInstance(eligibility.reasons, tuple)
        with self.assertRaises(FrozenInstanceError):
            eligibility.eligible = False  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            eligibility.reasons = ()  # type: ignore[misc]

        records = [
            self._record("sql-a", value=3),
            self._record("sql-b", value=4),
        ]
        with CaptureQueriesContext(connection) as queries:
            result = list(
                self.Item.create_many(
                    records,
                    creator_id=self.user.pk,
                    history_comment="SQL acceptance",
                    ignore_permission=True,
                )
            )

        self.assertEqual(
            result[0].ids, tuple(self.ItemModel.objects.values_list("pk", flat=True))
        )
        self.assertEqual(result[0].successful_count, 2)
        inserts = self._insert_queries(queries.captured_queries)
        self.assertEqual(len(inserts), 2, inserts)
        history = self.ItemModel.history.order_by("id")
        self.assertEqual(history.count(), 2)
        self.assertEqual(
            list(history.values_list("history_user_id", "history_change_reason")),
            [(self.user.pk, "SQL acceptance")] * 2,
        )
        rows = self.ItemModel.objects.order_by("name")
        self.assertEqual(
            list(rows.values_list("target_id", flat=True)),
            [self.target.identification["id"]] * 2,
        )

    def test_permission_receives_raw_payload_and_denial_is_indexed(self) -> None:
        records = [self._record("raw-permission")]
        list(
            self.Item.create_many(
                records,
                creator_id=self.user.pk,
                ignore_permission=False,
            )
        )
        payload = self.Item.Permission.seen_create_payloads[-1]
        self.assertIs(payload["owner"], self.user)
        self.assertIs(payload["target"], self.target)

        with self.assertRaises(CreateManyError) as raised:
            list(self.Item.create_many([self._record("permission-denied")]))
        self.assertEqual(raised.exception.failure_index, 0)
        self.assertIsInstance(raised.exception.cause, PermissionCheckError)
        self.assertFalse(
            self.ItemModel.objects.filter(name="permission-denied").exists()
        )

    def test_bulk_validation_reports_scalar_and_fk_indexes(self) -> None:
        cases = (
            ({**self._record("bad-scalar"), "value": "bad"}, "value"),
            ({**self._record("missing-fk"), "target_id": 999999}, "target"),
        )
        for offset, (invalid, field) in enumerate(cases):
            valid_before = self._record(f"validation-before-{offset}")
            valid_after = self._record(f"validation-after-{offset}", value=2)
            with self.assertRaises(CreateManyError) as raised:
                list(
                    self.Item.create_many(
                        [valid_before, invalid, valid_after],
                        ignore_permission=True,
                        batch_size=3,
                    )
                )
            failure = raised.exception
            self.assertEqual(failure.failure_index, 1)
            self.assertIsInstance(failure.cause, ValidationError)
            self.assertIn(field, failure.cause.message_dict)
            self.assertEqual(self.ItemModel.objects.count(), 0)

    def test_required_foreign_key_null_is_not_silently_skipped(self) -> None:
        invalid = self._record("null-fk")
        invalid["target"] = None
        with self.assertRaises(CreateManyError) as raised:
            list(
                self.Item.create_many(
                    [self._record("null-before"), invalid],
                    ignore_permission=True,
                    batch_size=2,
                )
            )
        self.assertEqual(raised.exception.failure_index, 1)
        self.assertIsInstance(raised.exception.cause, ValidationError)
        self.assertIn("target", raised.exception.cause.message_dict)
        self.assertEqual(self.ItemModel.objects.count(), 0)

    def test_to_field_fk_uses_canonical_fallback_with_related_value_validation(
        self,
    ) -> None:
        eligibility = bulk_create_eligibility(self.ToFieldItem)
        self.assertFalse(eligibility.eligible)
        self.assertIn(
            "foreign keys using to_field are unsupported", eligibility.reasons
        )
        valid = list(
            self.ToFieldItem.create_many(
                [
                    self._to_field_record("to-field-a"),
                    self._to_field_record("to-field-b", value=2),
                ],
                ignore_permission=True,
            )
        )
        self.assertEqual(len(valid[0].ids), 2)
        self.assertEqual(
            list(
                self.ToFieldItemModel.objects.values_list(
                    "target_by_code_id", flat=True
                )
            ),
            [self.target.code, self.target.code],
        )
        with self.assertRaises(CreateManyError) as raised:
            list(
                self.ToFieldItem.create_many(
                    [
                        self._to_field_record("to-field-before"),
                        self._to_field_record(
                            "to-field-missing", target_by_code="missing"
                        ),
                    ],
                    ignore_permission=True,
                )
            )
        self.assertEqual(raised.exception.failure_index, 1)
        self.assertIsInstance(raised.exception.cause, ValidationError)

    def test_in_batch_and_existing_unique_errors_keep_the_failing_index(self) -> None:
        duplicate_together = [
            self._record("unique-a", group="same-group", sequence=8),
            self._record("unique-b", group="same-group", sequence=8),
        ]
        with self.assertRaises(CreateManyError) as raised:
            list(
                self.Item.create_many(
                    duplicate_together,
                    ignore_permission=True,
                    batch_size=2,
                )
            )
        self.assertEqual(raised.exception.failure_index, 1)
        self.assertIsInstance(raised.exception.cause, ValidationError)
        self.assertIn(NON_FIELD_ERRORS, raised.exception.cause.message_dict)
        self.assertEqual(self.ItemModel.objects.count(), 0)

        list(
            self.Item.create_many(
                [self._record("already-there")], ignore_permission=True
            )
        )
        with self.assertRaises(CreateManyError) as raised:
            list(
                self.Item.create_many(
                    [self._record("unique-before"), self._record("already-there")],
                    ignore_permission=True,
                    batch_size=2,
                )
            )
        self.assertEqual(raised.exception.failure_index, 1)
        self.assertIsInstance(raised.exception.cause, ValidationError)
        self.assertIn("name", raised.exception.cause.message_dict)
        self.assertEqual(self.ItemModel.objects.count(), 1)

    def test_failed_sql_batch_rolls_back_while_earlier_progress_remains(self) -> None:
        records = [
            self._record("earlier-a"),
            self._record("earlier-b", value=2),
            self._record("rule-failure", value=-1),
            self._record("never-written", value=2),
        ]
        results = self.Item.create_many(records, ignore_permission=True, batch_size=2)
        first = next(results)
        self.assertEqual(first.successful_count, 2)
        self.assertTrue(first.committed)
        with self.assertRaises(CreateManyError) as raised:
            next(results)
        failure = raised.exception
        self.assertEqual(failure.failure_index, 2)
        self.assertEqual(failure.successful_count, 2)
        self.assertFalse(failure.committed)
        self.assertEqual(self.ItemModel.objects.count(), 2)
        self.assertFalse(self.ItemModel.objects.filter(name="never-written").exists())

    def test_batch_refresh_coalesces_sql_and_preserves_canonical_row_timing(
        self,
    ) -> None:
        calls: list[tuple[type[GeneralManager], tuple[object, ...], str, str]] = []

        def refresh(
            sender: type[GeneralManager],
            identifiers: tuple[object, ...],
            action: str,
            database_alias: str,
        ) -> None:
            calls.append((sender, identifiers, action, database_alias))

        handle = connect_batch_refresh_receiver(refresh)
        self.addCleanup(handle.disconnect)
        sql_results = list(
            self.Item.create_many(
                [self._record("coalesced-a"), self._record("coalesced-b", value=2)],
                ignore_permission=True,
                batch_size=2,
            )
        )
        self.assertEqual(
            calls,
            [(self.Item, sql_results[0].ids, "create", "default")],
        )

        handle.disconnect()
        canonical_calls: list[tuple[object, ...]] = []
        canonical_handle = connect_batch_refresh_receiver(
            lambda _sender,
            identifiers,
            _action,
            _database_alias: canonical_calls.append(identifiers)
        )
        self.addCleanup(canonical_handle.disconnect)
        canonical_results = list(
            self.CanonicalItem.create_many(
                [self._record("canonical-a"), self._record("canonical-b", value=2)],
                ignore_permission=True,
            )
        )
        self.assertFalse(bulk_create_eligibility(self.CanonicalItem).eligible)
        self.assertEqual(
            canonical_calls,
            [(canonical_results[0].ids[0],), (canonical_results[0].ids[1],)],
        )

    def test_refresh_callback_observes_fresh_cache_and_disconnects(self) -> None:
        existing = self.Item.create(
            **self._record("cache-existing"), ignore_permission=True
        )
        self.assertEqual(existing.row_count, 1)
        self.assertEqual(existing.row_count, 1)
        calls: list[tuple[tuple[object, ...], int, int]] = []

        def refresh(
            _sender: type[GeneralManager],
            identifiers: tuple[object, ...],
            _action: str,
            _database_alias: str,
        ) -> None:
            calls.append((identifiers, self.Item.all().count(), existing.row_count))

        handle = connect_batch_refresh_receiver(refresh)
        self.addCleanup(handle.disconnect)
        result = list(
            self.Item.create_many(
                [self._record("fresh-a"), self._record("fresh-b", value=2)],
                ignore_permission=True,
            )
        )
        self.assertEqual(calls, [(result[0].ids, 3, 3)])
        handle.disconnect()
        list(
            self.Item.create_many(
                [self._record("after-disconnect")], ignore_permission=True
            )
        )
        self.assertEqual(len(calls), 1)

    def test_outer_commit_and_rollback_preserve_pending_progress_and_callback_timing(
        self,
    ) -> None:
        immediate: list[tuple[object, ...]] = []
        on_commit: list[tuple[object, ...]] = []
        immediate_handle = connect_batch_refresh_receiver(
            lambda _sender, identifiers, _action, _alias: immediate.append(identifiers)
        )
        commit_handle = connect_batch_refresh_receiver(
            lambda _sender, identifiers, _action, _alias: on_commit.append(identifiers),
            on_commit=True,
        )
        self.addCleanup(immediate_handle.disconnect)
        self.addCleanup(commit_handle.disconnect)

        with transaction.atomic():
            results = list(
                self.Item.create_many(
                    [self._record("outer-a"), self._record("outer-b", value=2)],
                    ignore_permission=True,
                    batch_size=1,
                )
            )
            self.assertEqual([result.committed for result in results], [False, False])
            self.assertEqual(
                [result.pending_successful_count for result in results], [1, 2]
            )
            self.assertEqual(len(immediate), 2)
            self.assertEqual(on_commit, [])
        self.assertEqual(len(on_commit), 2)

        immediate_before_rollback = len(immediate)
        commit_before_rollback = len(on_commit)
        with self.assertRaises(_ExpectedRollback):
            with transaction.atomic():
                list(
                    self.Item.create_many(
                        [self._record("outer-rollback")],
                        ignore_permission=True,
                    )
                )
                self.assertEqual(len(immediate), immediate_before_rollback + 1)
                self.assertEqual(len(on_commit), commit_before_rollback)
                raise _ExpectedRollback
        self.assertEqual(len(on_commit), commit_before_rollback)
        self.assertFalse(self.ItemModel.objects.filter(name="outer-rollback").exists())

    def test_workflow_receives_each_sql_row_payload_and_outer_rollback_drops_events(
        self,
    ) -> None:
        events = self._configure_workflow()
        records = [
            self._record_ids("workflow-a", value=7),
            self._record_ids("workflow-b", value=8),
        ]
        list(self.Item.create_many(records, ignore_permission=True))
        self.assertEqual(len(events), 2)
        for event, record in zip(events, records, strict=True):
            payload = event.payload["values"]  # type: ignore[union-attr]
            self.assertEqual(payload["name"], record["name"])
            self.assertEqual(payload["value"], record["value"])
            self.assertEqual(payload["owner_id"], self.user.pk)
            self.assertEqual(payload["target_id"], self.target.identification["id"])

        with self.assertRaises(_ExpectedRollback):
            with transaction.atomic():
                list(
                    self.Item.create_many(
                        [self._record_ids("workflow-rollback")],
                        ignore_permission=True,
                    )
                )
                raise _ExpectedRollback
        self.assertEqual(len(events), 2)
        self.assertFalse(
            self.ItemModel.objects.filter(name="workflow-rollback").exists()
        )

    @override_settings(
        GENERAL_MANAGER={
            "WORKFLOW_MODE": "production",
            "WORKFLOW_ASYNC": True,
        }
    )
    def test_sql_database_workflow_registry_outbox_follows_batch_transaction(
        self,
    ) -> None:
        prior_registry = get_event_registry()
        registry = DatabaseEventRegistry()
        connect_workflow_signal_bridge(registry=registry)
        self.addCleanup(disconnect_workflow_signal_bridge)
        self.addCleanup(configure_event_registry, prior_registry)

        with patch(
            "general_manager.workflow.tasks.publish_outbox_batch.delay"
        ) as delay:
            committed = list(
                self.Item.create_many(
                    [
                        self._record_ids("database-workflow-a", value=7),
                        self._record_ids("database-workflow-b", value=8),
                    ],
                    ignore_permission=True,
                )
            )
            self.assertEqual(committed[0].successful_count, 2)
            self.assertEqual(WorkflowEventRecord.objects.count(), 2)
            self.assertEqual(WorkflowOutbox.objects.count(), 2)
            self.assertEqual(
                set(
                    WorkflowEventRecord.objects.values_list(
                        "payload__values__name", flat=True
                    )
                ),
                {"database-workflow-a", "database-workflow-b"},
            )
            self.assertEqual(delay.call_count, 2)

            delay.reset_mock()
            with self.assertRaises(_ExpectedRollback):
                with transaction.atomic():
                    list(
                        self.Item.create_many(
                            [self._record_ids("database-workflow-rollback")],
                            ignore_permission=True,
                        )
                    )
                    self.assertEqual(WorkflowEventRecord.objects.count(), 3)
                    self.assertEqual(WorkflowOutbox.objects.count(), 3)
                    raise _ExpectedRollback

            self.assertEqual(WorkflowEventRecord.objects.count(), 2)
            self.assertEqual(WorkflowOutbox.objects.count(), 2)
            self.assertEqual(delay.call_count, 0)

    def test_postgresql_concurrent_unique_conflict_keeps_source_history_atomic(
        self,
    ) -> None:
        if connections["default"].vendor == "sqlite":
            self.skipTest("SQLite cannot provide concurrent insert semantics")

        from general_manager.interface.capabilities.orm import bulk as bulk_module

        barrier = threading.Barrier(2)
        outcomes: list[object] = []
        validation_calls: list[int] = []
        lock = threading.Lock()
        original_validate_unique = bulk_module._validate_unique

        def synchronized_validate_unique(
            model: type[models.Model],
            instances: list[models.Model],
            alias: str,
        ) -> None:
            original_validate_unique(model, instances, alias)
            if getattr(instances[0], "name", None) == "concurrent-sql":
                with lock:
                    validation_calls.append(1)
                barrier.wait(timeout=20)

        def worker() -> None:
            connections.close_all()
            try:
                list(
                    self.Item.create_many(
                        [self._record("concurrent-sql")],
                        ignore_permission=True,
                    )
                )
                outcome: object = "success"
            except BaseException as error:  # noqa: BLE001 - assert both outcomes.
                outcome = error
            finally:
                with lock:
                    outcomes.append(outcome)
                connections.close_all()

        with patch.object(
            bulk_module, "_validate_unique", synchronized_validate_unique
        ):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(validation_calls), 2)
        self.assertEqual(sum(outcome == "success" for outcome in outcomes), 1)
        errors = [
            outcome for outcome in outcomes if isinstance(outcome, CreateManyError)
        ]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].failure_index, 0)
        self.assertIsInstance(errors[0].cause, IntegrityError)
        self.assertEqual(
            self.ItemModel.objects.filter(name="concurrent-sql").count(), 1
        )
        self.assertEqual(
            self.ItemModel.history.filter(name="concurrent-sql").count(),
            1,
        )

    def test_nested_refresh_write_savepoint_does_not_leak_workflow_notifications_or_ids(
        self,
    ) -> None:
        events = self._configure_workflow()
        existing = self.Item.create(
            **self._record("nested-cache-existing"), ignore_permission=True
        )
        events.clear()
        self.assertEqual(existing.row_count, 1)
        refresh_calls: list[tuple[object, ...]] = []
        nested_refresh_calls: list[tuple[object, ...]] = []
        callback_counts: list[int] = []
        nested_callback_counts: list[int] = []
        nested_write_active = False
        external_refresh_calls: list[tuple[object, ...]] = []

        def refresh(
            _sender: type[GeneralManager],
            identifiers: tuple[object, ...],
            _action: str,
            _alias: str,
        ) -> None:
            nonlocal nested_write_active
            if nested_write_active:
                nested_refresh_calls.append(identifiers)
                nested_callback_counts.append(existing.row_count)
                return
            refresh_calls.append(identifiers)
            callback_counts.append(existing.row_count)
            if len(refresh_calls) != 1:
                return
            try:
                nested_write_active = True
                with transaction.atomic():
                    self.Item.create(
                        **self._record("nested-rolled-back"),
                        ignore_permission=True,
                    )
                    _raise_expected_rollback()
            except _ExpectedRollback:
                pass
            finally:
                nested_write_active = False

        external_handle = connect_batch_refresh_receiver(
            lambda _sender, identifiers, _action, _alias: external_refresh_calls.append(
                identifiers
            ),
            on_commit=True,
        )
        self.addCleanup(external_handle.disconnect)
        refresh_handle = connect_batch_refresh_receiver(refresh)
        self.addCleanup(refresh_handle.disconnect)
        publish_calls: list[tuple[object, ...]] = []

        def record_publish(
            manager: type[GeneralManager],
            action: str,
            identification: dict[str, object],
        ) -> None:
            publish_calls.append((manager, action, identification))

        with patch.object(GraphQL, "manager_registry", {self.Item.__name__: self.Item}):
            with patch.object(
                GraphQL, "_publish_data_change", side_effect=record_publish
            ):
                results = list(
                    self.Item.create_many(
                        [
                            self._record("nested-outer-a"),
                            self._record("nested-outer-b", value=2),
                        ],
                        ignore_permission=True,
                        batch_size=1,
                    )
                )

        self.assertEqual(refresh_calls, [(results[0].ids[0],), (results[1].ids[0],)])
        self.assertEqual(len(nested_refresh_calls), 1)
        self.assertEqual(nested_callback_counts, [3])
        self.assertEqual(callback_counts, [2, 3])
        self.assertEqual(
            external_refresh_calls,
            [(results[0].ids[0],), (results[1].ids[0],)],
        )
        self.assertEqual(self.ItemModel.objects.count(), 3)
        self.assertFalse(
            self.ItemModel.objects.filter(name="nested-rolled-back").exists()
        )
        self.assertEqual(len(publish_calls), 2)
        self.assertEqual(
            {call[2]["id"] for call in publish_calls},
            set(refresh_calls[0] + refresh_calls[1]),
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(
            {event.payload["values"]["name"] for event in events},  # type: ignore[union-attr]
            {"nested-outer-a", "nested-outer-b"},
        )

    def test_nested_create_many_uses_canonical_rows_and_rolls_back_side_effects(
        self,
    ) -> None:
        events = self._configure_workflow()
        existing = self.Item.create(
            **self._record("nested-batch-cache-existing"), ignore_permission=True
        )
        events.clear()
        self.assertEqual(existing.row_count, 1)
        refresh_calls: list[tuple[object, ...]] = []
        nested_refresh_calls: list[tuple[object, ...]] = []
        callback_counts: list[int] = []
        nested_callback_counts: list[int] = []
        nested_write_active = False
        nested_ids: list[object] = []
        external_refresh_calls: list[tuple[object, ...]] = []

        def refresh(
            _sender: type[GeneralManager],
            identifiers: tuple[object, ...],
            _action: str,
            _alias: str,
        ) -> None:
            nonlocal nested_write_active
            if nested_write_active:
                nested_refresh_calls.append(identifiers)
                nested_callback_counts.append(existing.row_count)
                return
            refresh_calls.append(identifiers)
            callback_counts.append(existing.row_count)
            if len(refresh_calls) != 1:
                return
            try:
                nested_write_active = True
                with transaction.atomic():
                    nested_results = list(
                        self.Item.create_many(
                            [
                                self._record("nested-batch-rolled-back-a"),
                                self._record("nested-batch-rolled-back-b", value=2),
                            ],
                            batch_size=2,
                            ignore_permission=True,
                        )
                    )
                    nested_ids.extend(nested_results[0].ids)
                    _raise_expected_rollback()
            except _ExpectedRollback:
                pass
            finally:
                nested_write_active = False

        external_handle = connect_batch_refresh_receiver(
            lambda _sender, identifiers, _action, _alias: external_refresh_calls.append(
                identifiers
            ),
            on_commit=True,
        )
        self.addCleanup(external_handle.disconnect)
        refresh_handle = connect_batch_refresh_receiver(refresh)
        self.addCleanup(refresh_handle.disconnect)
        nested_bulk_create_calls: list[object] = []
        original_bulk_create = QuerySet.bulk_create

        def track_bulk_create(
            queryset: QuerySet[models.Model],
            objects: object,
            *args: object,
            **kwargs: object,
        ) -> object:
            if nested_write_active:
                nested_bulk_create_calls.append(objects)
            return original_bulk_create(  # type: ignore[arg-type]
                queryset, objects, *args, **kwargs
            )

        publish_calls: list[tuple[object, ...]] = []

        def record_publish(
            manager: type[GeneralManager],
            action: str,
            identification: dict[str, object],
        ) -> None:
            publish_calls.append((manager, action, identification))

        with patch.object(QuerySet, "bulk_create", autospec=True) as bulk_create:
            bulk_create.side_effect = track_bulk_create
            with patch.object(
                GraphQL, "manager_registry", {self.Item.__name__: self.Item}
            ):
                with patch.object(
                    GraphQL, "_publish_data_change", side_effect=record_publish
                ):
                    results = list(
                        self.Item.create_many(
                            [
                                self._record("nested-batch-outer-a"),
                                self._record("nested-batch-outer-b", value=2),
                            ],
                            ignore_permission=True,
                            batch_size=1,
                        )
                    )

        self.assertEqual(refresh_calls, [(results[0].ids[0],), (results[1].ids[0],)])
        self.assertEqual(len(nested_refresh_calls), 2)
        self.assertEqual(
            set(nested_refresh_calls),
            {(nested_ids[0],), (nested_ids[1],)},
        )
        self.assertEqual(nested_callback_counts, [3, 4])
        self.assertEqual(callback_counts, [2, 3])
        self.assertEqual(
            external_refresh_calls,
            [(results[0].ids[0],), (results[1].ids[0],)],
        )
        self.assertEqual(self.ItemModel.objects.count(), 3)
        self.assertFalse(
            self.ItemModel.objects.filter(
                name__startswith="nested-batch-rolled-back"
            ).exists()
        )
        self.assertEqual(
            self.ItemModel.history.filter(
                name__startswith="nested-batch-rolled-back"
            ).count(),
            0,
        )
        self.assertEqual(len(publish_calls), 2)
        self.assertEqual(
            {call[2]["id"] for call in publish_calls},
            set(refresh_calls[0] + refresh_calls[1]),
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(
            {event.payload["values"]["name"] for event in events},  # type: ignore[union-attr]
            {"nested-batch-outer-a", "nested-batch-outer-b"},
        )
        self.assertEqual(nested_bulk_create_calls, [])
        self.assertEqual(bulk_create.call_count, 4)

    def test_sender_specific_post_save_receiver_forces_row_fallback(self) -> None:
        saved: list[int] = []

        def receiver(sender: object, instance: models.Model, **kwargs: object) -> None:
            del kwargs
            if sender is self.ItemModel:
                saved.append(int(instance.pk))

        post_save.connect(receiver, sender=self.ItemModel, weak=False)
        self.addCleanup(post_save.disconnect, receiver, sender=self.ItemModel)
        eligibility = bulk_create_eligibility(self.Item)
        self.assertFalse(eligibility.eligible)
        self.assertIn("custom Django model signal receiver", eligibility.reasons)
        result = list(
            self.Item.create_many(
                [self._record("post-save-a"), self._record("post-save-b", value=2)],
                ignore_permission=True,
            )
        )
        self.assertEqual(len(saved), 2)
        self.assertEqual(result[0].ids, tuple(saved))
        self.assertEqual(self.ItemModel.history.count(), 2)

    def test_sender_specific_post_data_change_receiver_forces_row_fallback(
        self,
    ) -> None:
        changes: list[str] = []

        def receiver(sender: object, action: str, **kwargs: object) -> None:
            del kwargs
            if sender is self.Item:
                changes.append(action)

        post_data_change.connect(receiver, sender=self.Item, weak=False)
        self.addCleanup(post_data_change.disconnect, receiver, sender=self.Item)
        eligibility = bulk_create_eligibility(self.Item)
        self.assertFalse(eligibility.eligible)
        self.assertIn("custom manager lifecycle signal receiver", eligibility.reasons)
        list(
            self.Item.create_many(
                [self._record("post-change-a"), self._record("post-change-b", value=2)],
                ignore_permission=True,
            )
        )
        self.assertEqual(changes, ["create", "create"])

    def test_custom_history_signal_receiver_forces_canonical_history_fallback(
        self,
    ) -> None:
        history_signal_rows: list[object] = []

        def history_signal_receiver(sender: object, **kwargs: object) -> None:
            if sender is self.ItemModel.history.model:
                history_signal_rows.append(kwargs.get("history_instance"))

        history_model = self.ItemModel.history.model
        post_create_historical_record.connect(
            history_signal_receiver, sender=history_model, weak=False
        )
        self.addCleanup(
            post_create_historical_record.disconnect,
            history_signal_receiver,
            sender=history_model,
        )
        eligibility = bulk_create_eligibility(self.Item)
        self.assertFalse(eligibility.eligible)
        self.assertIn("custom history signal receiver", eligibility.reasons)
        result = list(
            self.Item.create_many(
                [
                    self._record("history-fallback-a"),
                    self._record("history-fallback-b", value=2),
                ],
                creator_id=self.user.pk,
                history_comment="canonical history",
                ignore_permission=True,
            )
        )
        self.assertEqual(len(result[0].ids), 2)
        self.assertEqual(len(history_signal_rows), 2)
        self.assertTrue(all(row is not None for row in history_signal_rows))
        self.assertEqual(
            set(self.ItemModel.history.values_list("history_change_reason", flat=True)),
            {"canonical history"},
        )
