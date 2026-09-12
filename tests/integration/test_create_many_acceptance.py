"""Acceptance coverage for the bounded ORM ``create_many`` contract."""

from __future__ import annotations

from io import BytesIO
import threading
from collections.abc import Iterator
from contextlib import suppress
import sys
from types import SimpleNamespace
from typing import Any, ClassVar, NoReturn
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.files.base import File
from django.core.exceptions import ValidationError
from django.db import (
    IntegrityError,
    OperationalError,
    connections,
    models,
    transaction,
)
from django.db.models.signals import post_save
from django.test import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.api.property import graph_ql_property
from general_manager.cache.signals import post_data_change, pre_data_change
from general_manager.interface import (
    DatabaseInterface,
    ExistingModelInterface,
    ReadOnlyInterface,
)
from general_manager.interface.utils.history import DATABASE_AWARE_HISTORY_MARKER
from general_manager.manager.bulk_create import (
    CreateManyError,
    CreateManyPostCommitError,
    CreateManyUnsupportedError,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.base_permission import PermissionCheckError
from general_manager.rule.rule import Rule
from general_manager.search.config import IndexConfig, SearchInvalidationRule
from general_manager.uploads.types import UploadCandidate
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
from tests.utils.database import create_test_models, drop_test_models


class _ExpectedRollback(RuntimeError):
    """Intentional transaction rollback used by acceptance tests."""


class _IteratorFailure(RuntimeError):
    """Input source failure with a stable type for cause assertions."""


def _non_negative(item: Any) -> bool:
    """Reject negative values through the model's canonical rule pipeline."""
    return item.value >= 0


def _raise_expected_rollback() -> NoReturn:
    """Raise the intentional rollback marker from a nested callback."""
    raise _ExpectedRollback


class CreateManyAcceptanceIntegrationTests(GeneralManagerTransactionTestCase):
    """Exercise cross-cutting behavior that must remain equivalent to ``create``."""

    Item: ClassVar[type[GeneralManager]]
    ItemModel: ClassVar[type[models.Model]]
    SearchTarget: ClassVar[type[GeneralManager]]
    SearchTargetModel: ClassVar[type[models.Model]]
    UploadItem: ClassVar[type[GeneralManager]]
    ReadOnlyItem: ClassVar[type[GeneralManager]]

    @classmethod
    def setUpClass(cls) -> None:
        user_model = get_user_model()

        class SearchTarget(GeneralManager):
            class Interface(DatabaseInterface):
                label = models.CharField(max_length=100, unique=True)

            class SearchConfig:
                indexes = (IndexConfig(name="global", fields=("label",)),)

        class Item(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100, unique=True)
                value = models.IntegerField(default=0)
                owner = models.ForeignKey(user_model, on_delete=models.CASCADE)
                target = models.ForeignKey(
                    SearchTarget.Interface._model,
                    on_delete=models.CASCADE,
                )
                tags = models.ManyToManyField(user_model, blank=True)

                class Meta:
                    rules: ClassVar[list[Rule[Any]]] = [Rule(_non_negative)]

            class SearchConfig:
                indexes = (IndexConfig(name="global", fields=("name",)),)

            @graph_ql_property(cache="dependency")
            def row_count(self) -> int:
                """Return the current number of rows for dependency-cache checks."""
                return type(self).all().count()

        class UploadItem(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100, unique=True)
                document = models.FileField(upload_to="create-many/")

        class ReadOnlyItem(GeneralManager):
            _data: ClassVar[list[dict[str, object]]] = []

            class Interface(ReadOnlyInterface):
                name = models.CharField(max_length=100, unique=True)

        def resolve_search_target(
            change: Any, owner: type[GeneralManager]
        ) -> tuple[GeneralManager, ...]:
            del owner
            target = change.instance.target
            return (target,)

        class SearchTargetSearchConfig:
            indexes = (IndexConfig(name="global", fields=("label",)),)
            invalidation_rules = (
                SearchInvalidationRule(source=Item, resolve=resolve_search_target),
            )

        SearchTarget.SearchConfig = SearchTargetSearchConfig

        # Dynamic managers are resolved by the real search task through their
        # import path. Expose them in this test module so the task sees the same
        # classes that generated the rows.
        for manager in (SearchTarget, Item, UploadItem, ReadOnlyItem):
            manager.__module__ = __name__
            setattr(sys.modules[__name__], manager.__name__, manager)

        cls.Item = Item
        cls.ItemModel = Item.Interface._model
        cls.SearchTarget = SearchTarget
        cls.SearchTargetModel = SearchTarget.Interface._model
        cls.UploadItem = UploadItem
        cls.ReadOnlyItem = ReadOnlyItem
        cls.general_manager_classes = [
            SearchTarget,
            Item,
            UploadItem,
            ReadOnlyItem,
        ]
        GeneralManagerMeta.all_classes = cls.general_manager_classes
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username=f"bulk-{uuid4().hex[:10]}",
            password=uuid4().hex,
        )
        self.target = self.SearchTarget.create(
            label=f"target-{uuid4().hex[:10]}",
            ignore_permission=True,
        )

    def _record(self, name: str, *, value: int = 1) -> dict[str, object]:
        return {
            "name": name,
            "value": value,
            "owner": self.user,
            "target": self.target,
        }

    def _workflow_record(self, name: str) -> dict[str, object]:
        """Use scalar foreign-key values so durable event JSON is serializable."""
        return {
            "name": name,
            "value": 1,
            "owner_id": self.user.pk,
            "target_id": self.target.identification["id"],
        }

    def _configure_database_workflow_registry(self) -> None:
        """Use the durable bridge for one test and restore global state after it."""
        prior_registry = get_event_registry()
        registry = DatabaseEventRegistry()
        connect_workflow_signal_bridge(registry=registry)
        self.addCleanup(disconnect_workflow_signal_bridge)
        self.addCleanup(configure_event_registry, prior_registry)

    def test_permission_gate_and_explicit_bypass(self) -> None:
        with self.assertRaises(CreateManyError) as raised:
            list(self.Item.create_many([self._record("denied")]))
        self.assertIsInstance(raised.exception.cause, PermissionCheckError)
        self.assertFalse(self.ItemModel.objects.filter(name="denied").exists())

        results = list(
            self.Item.create_many(
                [self._record("bypassed")],
                ignore_permission=True,
            )
        )
        self.assertEqual(results[0].successful_count, 1)
        self.assertTrue(self.ItemModel.objects.filter(name="bypassed").exists())

    def test_rules_fk_normalization_missing_fk_and_many_to_many_are_atomic(
        self,
    ) -> None:
        results = list(
            self.Item.create_many(
                [
                    self._record("manager-fk"),
                    {**self._record("id-fk"), "owner": None, "owner_id": self.user.pk},
                ],
                ignore_permission=True,
                batch_size=2,
            )
        )
        self.assertEqual(len(results[0].ids), 2)
        rows = self.ItemModel.objects.order_by("name")
        self.assertEqual(
            list(rows.values_list("owner_id", flat=True)), [self.user.pk] * 2
        )

        first = rows.get(name="manager-fk")
        self.assertEqual(list(first.tags.values_list("pk", flat=True)), [])
        list(
            self.Item.create_many(
                [{**self._record("with-tags"), "tags_id_list": [self.user.pk]}],
                ignore_permission=True,
            )
        )
        tagged = self.ItemModel.objects.get(name="with-tags")
        self.assertEqual(list(tagged.tags.values_list("pk", flat=True)), [self.user.pk])

        invalid_value = self._record("rule-failure", value=-1)
        with self.assertRaises(CreateManyError) as raised:
            list(self.Item.create_many([invalid_value], ignore_permission=True))
        self.assertEqual(raised.exception.failure_index, 0)
        self.assertFalse(self.ItemModel.objects.filter(name="rule-failure").exists())

        missing_fk = self._record("missing-fk")
        missing_fk["owner"] = None
        missing_fk["owner_id"] = self.user.pk + 999_999
        with self.assertRaises(CreateManyError) as raised:
            list(self.Item.create_many([missing_fk], ignore_permission=True))
        self.assertEqual(raised.exception.failure_index, 0)
        self.assertIsInstance(raised.exception.cause, (IntegrityError, ValidationError))
        self.assertFalse(self.ItemModel.objects.filter(name="missing-fk").exists())

    def test_uniqueness_is_authoritative_within_across_batches_and_persisted_rows(
        self,
    ) -> None:
        list(self.Item.create_many([self._record("persisted")], ignore_permission=True))

        with self.assertRaises(CreateManyError) as raised:
            list(
                self.Item.create_many(
                    [self._record("batch-a"), self._record("batch-a")],
                    ignore_permission=True,
                    batch_size=2,
                )
            )
        self.assertEqual(raised.exception.failure_index, 1)
        self.assertEqual(self.ItemModel.objects.count(), 1)

        with self.assertRaises(CreateManyError) as raised:
            list(
                self.Item.create_many(
                    [self._record("batch-b"), self._record("persisted")],
                    ignore_permission=True,
                    batch_size=2,
                )
            )
        self.assertEqual(raised.exception.failure_index, 1)
        self.assertEqual(self.ItemModel.objects.count(), 1)

    def test_history_actor_reason_model_hooks_and_workflow_one_event_per_row(
        self,
    ) -> None:
        saved: list[int] = []

        def record_save(
            sender: object, instance: models.Model, **kwargs: object
        ) -> None:
            del kwargs
            if sender is self.ItemModel:
                saved.append(int(instance.pk))

        post_save.connect(record_save, weak=False)
        self.addCleanup(post_save.disconnect, record_save)

        prior_registry = get_event_registry()
        registry = InMemoryEventRegistry()
        workflow_events: list[str] = []
        registry.register(
            "manager_created",
            handler=lambda event: workflow_events.append(event.event_id),
            registration_id="create-many-acceptance-manager-created",
        )
        connect_workflow_signal_bridge(registry=registry)
        self.addCleanup(disconnect_workflow_signal_bridge)
        self.addCleanup(configure_event_registry, prior_registry)

        results = list(
            self.Item.create_many(
                [self._record("history-a"), self._record("history-b")],
                creator_id=self.user.pk,
                history_comment="bulk acceptance",
                ignore_permission=True,
            )
        )

        self.assertEqual(len(saved), 2)
        self.assertEqual(len(workflow_events), 2)
        self.assertEqual(len(results[0].ids), 2)
        for identifier in results[0].ids:
            history = self.ItemModel.history.filter(id=identifier).latest(
                "history_date"
            )
            self.assertEqual(history.history_user_id, self.user.pk)
            self.assertEqual(history.history_change_reason, "bulk acceptance")

    def test_bounded_source_errors_and_reserved_keys_are_indexed_without_leaks(
        self,
    ) -> None:
        consumed: list[int] = []

        def source() -> Iterator[dict[str, object]]:
            consumed.append(0)
            yield self._record("source-a")
            consumed.append(1)
            yield self._record("source-b")
            raise _IteratorFailure

        results = self.Item.create_many(source(), ignore_permission=True, batch_size=2)
        first = next(results)
        self.assertEqual(first.successful_count, 2)
        self.assertEqual(consumed, [0, 1])
        with self.assertRaises(CreateManyError) as raised:
            next(results)
        failure = raised.exception
        self.assertEqual(failure.failure_index, 2)
        self.assertEqual(failure.successful_count, 2)
        self.assertIsInstance(failure.cause, _IteratorFailure)

        with self.assertRaises(CreateManyError) as raised:
            list(
                self.Item.create_many(
                    [{**self._record("reserved"), "creator_id": self.user.pk}],
                    ignore_permission=True,
                )
            )
        self.assertEqual(raised.exception.failure_index, 0)
        self.assertIsInstance(raised.exception.cause, TypeError)
        self.assertFalse(self.ItemModel.objects.filter(name="reserved").exists())

    def test_nested_same_class_rollback_does_not_leak_child_commit_callbacks(
        self,
    ) -> None:
        callbacks: list[str] = []

        def register_child_callback(sender: object, **kwargs: object) -> None:
            if sender is self.Item and kwargs.get("name") == "child":
                transaction.on_commit(
                    lambda: callbacks.append("child"),
                    using=kwargs["database_alias"],
                )

        def create_and_roll_back_child(sender: object, **kwargs: object) -> None:
            if sender is not self.Item or kwargs.get("name") != "parent":
                return
            try:
                with transaction.atomic():
                    self.Item.create(
                        **self._record("child"),
                        ignore_permission=True,
                    )
                    _raise_expected_rollback()
            except _ExpectedRollback:
                pass

        post_data_change.connect(register_child_callback, weak=False)
        post_data_change.connect(create_and_roll_back_child, weak=False)
        self.addCleanup(post_data_change.disconnect, register_child_callback)
        self.addCleanup(post_data_change.disconnect, create_and_roll_back_child)

        list(self.Item.create_many([self._record("parent")], ignore_permission=True))
        self.assertTrue(self.ItemModel.objects.filter(name="parent").exists())
        self.assertFalse(self.ItemModel.objects.filter(name="child").exists())
        self.assertEqual(callbacks, [])

    def test_outer_commit_and_rollback_control_callbacks_and_progress(self) -> None:
        callbacks: list[str] = []

        def register_callback(sender: object, **kwargs: object) -> None:
            if sender is self.Item:
                transaction.on_commit(
                    lambda: callbacks.append(str(kwargs["name"])),
                    using=kwargs["database_alias"],
                )

        post_data_change.connect(register_callback, weak=False)
        self.addCleanup(post_data_change.disconnect, register_callback)

        with transaction.atomic():
            results = list(
                self.Item.create_many(
                    [self._record("outer-commit-a"), self._record("outer-commit-b")],
                    ignore_permission=True,
                    batch_size=1,
                )
            )
            self.assertEqual([result.committed for result in results], [False, False])
            self.assertEqual(
                [result.committed_successful_count for result in results], [0, 0]
            )
            self.assertEqual(
                [result.pending_successful_count for result in results], [1, 2]
            )
            self.assertEqual(callbacks, [])
        self.assertEqual(callbacks, ["outer-commit-a", "outer-commit-b"])

        callbacks.clear()
        with self.assertRaises(_ExpectedRollback):
            with transaction.atomic():
                list(
                    self.Item.create_many(
                        [self._record("outer-rollback")],
                        ignore_permission=True,
                    )
                )
                raise _ExpectedRollback
        self.assertEqual(callbacks, [])
        self.assertFalse(self.ItemModel.objects.filter(name="outer-rollback").exists())

    def test_pending_iterator_rejects_next_after_outer_commit(self) -> None:
        consumed: list[int] = []

        def records() -> Iterator[dict[str, object]]:
            consumed.append(0)
            yield self._record("scope-commit-a")
            consumed.append(1)
            yield self._record("scope-commit-b")

        results = self.Item.create_many(records(), ignore_permission=True, batch_size=1)
        with transaction.atomic():
            first = next(results)
            self.assertFalse(first.committed)
        with self.assertRaises(CreateManyError) as raised:
            next(results)
        self.assertIsInstance(raised.exception.cause, CreateManyUnsupportedError)
        self.assertEqual(consumed, [0])
        self.assertEqual(raised.exception.failure_index, 1)
        self.assertTrue(self.ItemModel.objects.filter(name="scope-commit-a").exists())

    def test_pending_iterator_rejects_next_after_outer_rollback(self) -> None:
        consumed: list[int] = []

        def records() -> Iterator[dict[str, object]]:
            consumed.append(0)
            yield self._record("scope-rollback-a")
            consumed.append(1)
            yield self._record("scope-rollback-b")

        results = self.Item.create_many(records(), ignore_permission=True, batch_size=1)
        with self.assertRaises(_ExpectedRollback):
            with transaction.atomic():
                first = next(results)
                self.assertFalse(first.committed)
                raise _ExpectedRollback
        with self.assertRaises(CreateManyError) as raised:
            next(results)
        self.assertIsInstance(raised.exception.cause, CreateManyUnsupportedError)
        self.assertEqual(consumed, [0])
        self.assertFalse(
            self.ItemModel.objects.filter(name="scope-rollback-a").exists()
        )

    def test_pending_iterator_rejects_next_after_nested_savepoint_rollback(
        self,
    ) -> None:
        consumed: list[int] = []

        def records() -> Iterator[dict[str, object]]:
            consumed.append(0)
            yield self._record("scope-savepoint-a")
            consumed.append(1)
            yield self._record("scope-savepoint-b")

        results = self.Item.create_many(records(), ignore_permission=True, batch_size=1)
        with transaction.atomic():
            with self.assertRaises(_ExpectedRollback):
                with transaction.atomic():
                    first = next(results)
                    self.assertFalse(first.committed)
                    raise _ExpectedRollback
            with self.assertRaises(CreateManyError) as raised:
                next(results)
        self.assertIsInstance(raised.exception.cause, CreateManyUnsupportedError)
        self.assertEqual(consumed, [0])
        self.assertFalse(
            self.ItemModel.objects.filter(name="scope-savepoint-a").exists()
        )

    def test_pending_iterator_rejects_new_and_reused_atomic_scopes(self) -> None:
        for name, reused in (("scope-new", False), ("scope-reused", True)):
            with self.subTest(name=name, reused=reused):
                consumed: list[int] = []

                def records(
                    name: str = name,
                    consumed: list[int] = consumed,
                ) -> Iterator[dict[str, object]]:
                    consumed.append(0)
                    yield self._record(f"{name}-a")
                    consumed.append(1)
                    yield self._record(f"{name}-b")

                results = self.Item.create_many(
                    records(), ignore_permission=True, batch_size=1
                )
                atomic = transaction.atomic() if reused else None
                if atomic is None:
                    with transaction.atomic():
                        first = next(results)
                        self.assertFalse(first.committed)
                else:
                    with atomic:
                        first = next(results)
                        self.assertFalse(first.committed)

                with self.assertRaises(CreateManyError) as raised:
                    if atomic is None:
                        with transaction.atomic():
                            next(results)
                    else:
                        with atomic:
                            next(results)
                self.assertIsInstance(
                    raised.exception.cause, CreateManyUnsupportedError
                )
                self.assertEqual(consumed, [0])

    def test_pending_iterator_rejects_reused_nested_atomic_scope(self) -> None:
        consumed: list[int] = []

        def records() -> Iterator[dict[str, object]]:
            consumed.append(0)
            yield self._record("scope-nested-reused-a")
            consumed.append(1)
            yield self._record("scope-nested-reused-b")

        results = self.Item.create_many(records(), ignore_permission=True, batch_size=1)
        nested = transaction.atomic()
        with transaction.atomic():
            with nested:
                first = next(results)
                self.assertFalse(first.committed)
            with self.assertRaises(CreateManyError) as raised:
                with nested:
                    next(results)
        self.assertIsInstance(raised.exception.cause, CreateManyUnsupportedError)
        self.assertEqual(consumed, [0])
        self.assertTrue(
            self.ItemModel.objects.filter(name="scope-nested-reused-a").exists()
        )

    def test_pending_iterator_rejects_scope_after_earlier_commit_callback_error(
        self,
    ) -> None:
        consumed: list[int] = []

        def records() -> Iterator[dict[str, object]]:
            consumed.append(0)
            yield self._record("scope-callback-a")
            consumed.append(1)
            yield self._record("scope-callback-b")

        def fail_on_commit(sender: object, **kwargs: object) -> None:
            if sender is self.Item and kwargs.get("name") == "scope-callback-a":
                transaction.on_commit(
                    lambda: (_ for _ in ()).throw(_ExpectedRollback),
                    using=kwargs["database_alias"],
                )

        post_data_change.connect(fail_on_commit, weak=False)
        self.addCleanup(post_data_change.disconnect, fail_on_commit)
        results = self.Item.create_many(records(), ignore_permission=True, batch_size=1)
        with self.assertRaises(_ExpectedRollback):
            with transaction.atomic():
                first = next(results)
                self.assertFalse(first.committed)
        with self.assertRaises(CreateManyError) as raised:
            next(results)
        self.assertIsInstance(raised.exception.cause, CreateManyUnsupportedError)
        self.assertEqual(consumed, [0])
        self.assertTrue(self.ItemModel.objects.filter(name="scope-callback-a").exists())

    def test_search_cache_and_subscription_refresh_are_fresh_and_coalesced(
        self,
    ) -> None:
        existing = self.Item.create(
            **self._record("cache-existing"),
            ignore_permission=True,
        )

        # Prime the real dependency cache before the batch so stale values cannot
        # satisfy the assertion after the mutation commits.
        self.assertEqual(existing.row_count, 1)
        self.assert_cache_miss()
        self.assertEqual(existing.row_count, 1)
        self.assert_cache_hit()

        dispatched: list[tuple[tuple[object, ...], dict[str, object]]] = []
        sent: list[tuple[str, dict[str, object]]] = []

        async def group_send(group: str, message: dict[str, object]) -> None:
            sent.append((group, message))

        def record_dispatch(*args: object, **kwargs: object) -> None:
            dispatched.append((args, kwargs))

        with (
            patch.object(
                GraphQL,
                "manager_registry",
                {self.Item.__name__: self.Item},
            ),
            patch.object(
                GraphQL,
                "_get_channel_layer",
                return_value=SimpleNamespace(group_send=group_send),
            ),
            patch(
                "general_manager.search.invalidation.dispatch_index_manager_batch",
                new=record_dispatch,
            ),
        ):
            list(
                self.Item.create_many(
                    [self._record("fresh-a"), self._record("fresh-b")],
                    ignore_permission=True,
                )
            )

        self.assertEqual(existing.row_count, 3)
        self.assert_cache_miss()
        self.assertEqual(existing.row_count, 3)
        self.assert_cache_hit()

        self.assertEqual(self.ItemModel.objects.count(), 3)
        self.assertEqual(len(dispatched), 2)
        dispatched_paths = {str(args[0]).split(".")[-1] for args, _ in dispatched}
        self.assertIn("Item", dispatched_paths)
        self.assertIn("SearchTarget", dispatched_paths)
        target_dispatches = [
            args for args, _ in dispatched if str(args[0]).endswith("SearchTarget")
        ]
        self.assertEqual(len(target_dispatches), 1)
        self.assertEqual(
            target_dispatches[0][2][0]["id"],
            self.target.identification["id"],
        )

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1]["action"], "refresh")
        self.assertEqual(sent[0][1]["manager"], self.Item.__name__)
        self.assertEqual(
            self.ItemModel.objects.filter(name__startswith="fresh-").count(), 2
        )
        self.assertEqual(
            existing.identification["id"],
            self.ItemModel.objects.get(name="cache-existing").pk,
        )

    def test_unmarked_history_route_is_rejected_before_input(self) -> None:
        self_model = self.ItemModel
        history_model = self.ItemModel.history.model

        class HistorySecondaryRouter:
            def db_for_write(
                self, model: type[models.Model], **hints: object
            ) -> str | None:
                del hints
                if model is history_model:
                    return "secondary"
                if model is self_model:
                    return None
                return None

        consumed: list[int] = []

        def source() -> Iterator[dict[str, object]]:
            consumed.append(1)
            yield self._record("history-route-side-effect")

        with (
            patch.object(
                history_model,
                DATABASE_AWARE_HISTORY_MARKER,
                False,
            ),
            override_settings(DATABASE_ROUTERS=[HistorySecondaryRouter()]),
        ):
            with self.assertRaises(CreateManyUnsupportedError):
                list(self.Item.create_many(source(), ignore_permission=True))
        self.assertEqual(consumed, [])
        self.assertFalse(
            self.ItemModel.objects.filter(name="history-route-side-effect").exists()
        )

    def test_nested_unmarked_history_rejects_create_update_and_hard_delete_before_signals(
        self,
    ) -> None:
        class NestedHistoryItem(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100, unique=True)

        nested_model = NestedHistoryItem.Interface._model
        nested_history_model = nested_model.history.model
        nested_signal_actions: list[str] = []

        class HistoryOnlySecondaryRouter:
            def db_for_write(
                self, model: type[models.Model], **hints: object
            ) -> str | None:
                del hints
                if model is nested_history_model:
                    return "secondary"
                return None

        def nested_signal_observer(sender: object, **kwargs: object) -> None:
            if sender is NestedHistoryItem:
                nested_signal_actions.append(str(kwargs.get("action")))

        def nested_mutation(sender: object, **kwargs: object) -> None:
            if sender is not self.Item:
                return
            name = kwargs.get("name")
            if name == "nested-history-create":
                NestedHistoryItem.create(
                    name="nested-history-created",
                    ignore_permission=True,
                )
            elif name == "nested-history-update":
                nested_instance = NestedHistoryItem._from_trusted_orm_instance(
                    nested_model(pk=1)
                )
                nested_instance.update(
                    name="nested-history-updated",
                    ignore_permission=True,
                )
            elif name == "nested-history-delete":
                nested_instance = NestedHistoryItem._from_trusted_orm_instance(
                    nested_model(pk=1)
                )
                nested_instance.delete(ignore_permission=True)

        pre_data_change.connect(nested_signal_observer, weak=False)
        post_data_change.connect(nested_signal_observer, weak=False)
        post_data_change.connect(nested_mutation, weak=False)
        self.addCleanup(pre_data_change.disconnect, nested_signal_observer)
        self.addCleanup(post_data_change.disconnect, nested_signal_observer)
        self.addCleanup(post_data_change.disconnect, nested_mutation)

        with (
            patch.object(
                nested_history_model,
                DATABASE_AWARE_HISTORY_MARKER,
                False,
            ),
            override_settings(DATABASE_ROUTERS=[HistoryOnlySecondaryRouter()]),
        ):
            for name in (
                "nested-history-create",
                "nested-history-update",
                "nested-history-delete",
            ):
                with self.subTest(action=name):
                    with self.assertRaises(CreateManyError) as raised:
                        list(
                            self.Item.create_many(
                                [self._record(name)],
                                ignore_permission=True,
                            )
                        )
                    self.assertIsInstance(
                        raised.exception.cause,
                        CreateManyUnsupportedError,
                    )
                    self.assertFalse(self.ItemModel.objects.filter(name=name).exists())

        self.assertEqual(nested_signal_actions, [])

    def test_many_to_many_through_route_is_rejected_before_relation_write(self) -> None:
        self_model = self.ItemModel
        through_model = self.ItemModel.tags.through

        class ThroughSecondaryRouter:
            def db_for_write(
                self, model: type[models.Model], **hints: object
            ) -> str | None:
                del hints
                if model is through_model:
                    return "secondary"
                if model is self_model:
                    return None
                return None

        with override_settings(DATABASE_ROUTERS=[ThroughSecondaryRouter()]):
            with self.assertRaises(CreateManyError) as raised:
                list(
                    self.Item.create_many(
                        [
                            {
                                **self._record("through-route-side-effect"),
                                "tags_id_list": [self.user.pk],
                            }
                        ],
                        ignore_permission=True,
                    )
                )
        self.assertIsInstance(raised.exception.cause, CreateManyUnsupportedError)
        self.assertFalse(
            self.ItemModel.objects.filter(name="through-route-side-effect").exists()
        )

    def test_unsupported_read_only_custom_interface_upload_and_durable_workflow_reject_before_input(
        self,
    ) -> None:
        def untouched() -> Iterator[dict[str, object]]:
            raise AssertionError
            yield self._record("never")

        with self.assertRaises(CreateManyUnsupportedError):
            list(self.ReadOnlyItem.create_many(untouched(), ignore_permission=True))

        class CustomCreate(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)

            @classmethod
            def create(cls, **kwargs: object) -> GeneralManager:
                return super().create(**kwargs)

        with self.assertRaises(CreateManyUnsupportedError):
            list(CustomCreate.create_many(untouched(), ignore_permission=True))

        candidate = UploadCandidate(
            intent_id=uuid4(),
            filename="payload.txt",
            size=1,
            content_type="text/plain",
            checksum_sha256="a" * 64,
        )
        with self.assertRaises(CreateManyError) as raised:
            list(
                self.UploadItem.create_many(
                    [{"name": "upload", "document": candidate}],
                    ignore_permission=True,
                )
            )
        self.assertIsInstance(raised.exception.cause, CreateManyUnsupportedError)
        self.assertFalse(self.UploadItem.Interface._model.objects.exists())

        prior_registry = get_event_registry()
        configure_event_registry(DatabaseEventRegistry())
        try:
            with (
                override_settings(
                    GENERAL_MANAGER={
                        "WORKFLOW_MODE": "production",
                        "WORKFLOW_ASYNC": False,
                    }
                ),
                self.assertRaises(CreateManyUnsupportedError),
            ):
                list(
                    self.Item.create_many(
                        [self._record("sync-durable")], ignore_permission=True
                    )
                )
        finally:
            configure_event_registry(prior_registry)
        self.assertFalse(self.ItemModel.objects.filter(name="sync-durable").exists())

    def test_database_workflow_registry_persists_events_and_outbox_on_commit(
        self,
    ) -> None:
        self._configure_database_workflow_registry()
        with (
            override_settings(
                GENERAL_MANAGER={
                    "WORKFLOW_MODE": "production",
                    "WORKFLOW_ASYNC": True,
                }
            ),
            patch(
                "general_manager.workflow.tasks.publish_outbox_batch.delay"
            ) as enqueue,
        ):
            results = list(
                self.Item.create_many(
                    [
                        self._workflow_record("durable-commit-a"),
                        self._workflow_record("durable-commit-b"),
                    ],
                    ignore_permission=True,
                )
            )

        self.assertTrue(results[0].committed)
        self.assertEqual(
            self.ItemModel.objects.filter(name__startswith="durable-").count(), 2
        )
        self.assertEqual(WorkflowEventRecord.objects.count(), 2)
        self.assertEqual(WorkflowOutbox.objects.count(), 2)
        event_ids = set(WorkflowEventRecord.objects.values_list("id", flat=True))
        self.assertEqual(
            set(WorkflowOutbox.objects.values_list("event_id", flat=True)),
            event_ids,
        )
        self.assertEqual(
            set(WorkflowEventRecord.objects.values_list("event_type", flat=True)),
            {"general_manager.manager.created"},
        )
        self.assertEqual(
            WorkflowOutbox.objects.filter(status=WorkflowOutbox.STATUS_PENDING).count(),
            2,
        )
        self.assertEqual(enqueue.call_count, 2)

    def test_database_workflow_registry_rolls_back_events_and_outbox_with_batch(
        self,
    ) -> None:
        self._configure_database_workflow_registry()
        with (
            override_settings(
                GENERAL_MANAGER={
                    "WORKFLOW_MODE": "production",
                    "WORKFLOW_ASYNC": True,
                }
            ),
            patch(
                "general_manager.workflow.tasks.publish_outbox_batch.delay"
            ) as enqueue,
            self.assertRaises(_ExpectedRollback),
        ):
            with transaction.atomic():
                list(
                    self.Item.create_many(
                        [self._workflow_record("durable-rollback-a")],
                        ignore_permission=True,
                    )
                )
                raise _ExpectedRollback

        self.assertFalse(
            self.ItemModel.objects.filter(name="durable-rollback-a").exists()
        )
        self.assertEqual(WorkflowEventRecord.objects.count(), 0)
        self.assertEqual(WorkflowOutbox.objects.count(), 0)
        enqueue.assert_not_called()

    def test_workflow_record_and_outbox_router_rejects_before_input(self) -> None:
        class WorkflowSecondaryRouter:
            def db_for_write(
                self, model: type[models.Model], **hints: object
            ) -> str | None:
                del hints
                if model in {WorkflowEventRecord, WorkflowOutbox}:
                    return "secondary"
                return None

        consumed: list[int] = []

        def source() -> Iterator[dict[str, object]]:
            consumed.append(1)
            yield self._workflow_record("workflow-routed")

        self._configure_database_workflow_registry()
        with (
            override_settings(
                GENERAL_MANAGER={
                    "WORKFLOW_MODE": "production",
                    "WORKFLOW_ASYNC": True,
                },
                DATABASE_ROUTERS=[WorkflowSecondaryRouter()],
            ),
            self.assertRaises(CreateManyUnsupportedError),
        ):
            list(self.Item.create_many(source(), ignore_permission=True))

        self.assertEqual(consumed, [])
        self.assertFalse(self.ItemModel.objects.filter(name="workflow-routed").exists())
        self.assertEqual(WorkflowEventRecord.objects.count(), 0)
        self.assertEqual(WorkflowOutbox.objects.count(), 0)

    def test_nested_create_upload_candidate_rejects_before_finalization(self) -> None:
        candidate = UploadCandidate(
            intent_id=uuid4(),
            filename="nested-create.txt",
            size=7,
            content_type="text/plain",
            checksum_sha256="b" * 64,
        )

        def nested_create(sender: object, **kwargs: object) -> None:
            if sender is self.Item and kwargs.get("name") == "nested-create-upload":
                self.UploadItem.create(
                    name="nested-upload-candidate",
                    document=candidate,
                    ignore_permission=True,
                )

        post_data_change.connect(nested_create, weak=False)
        self.addCleanup(post_data_change.disconnect, nested_create)
        with (
            patch(
                "general_manager.interface.capabilities.orm.mutations.prepare_upload_claims"
            ) as prepare,
            patch(
                "general_manager.interface.capabilities.orm.mutations.run_upload_transaction"
            ) as run_upload,
            self.assertRaises(CreateManyError) as raised,
        ):
            list(
                self.Item.create_many(
                    [self._record("nested-create-upload")],
                    ignore_permission=True,
                )
            )

        self.assertIsInstance(raised.exception.cause, CreateManyUnsupportedError)
        prepare.assert_not_called()
        run_upload.assert_not_called()
        self.assertFalse(
            self.ItemModel.objects.filter(name="nested-create-upload").exists()
        )
        self.assertFalse(
            self.UploadItem.Interface._model.objects.filter(
                name="nested-upload-candidate"
            ).exists()
        )

    def test_nested_update_file_rejects_before_finalization(self) -> None:
        existing_row = self.UploadItem.Interface._model.objects.create(
            name="nested-update-upload",
            document="existing.txt",
        )
        existing = self.UploadItem(existing_row.pk)
        payload = File(BytesIO(b"nested"), name="nested-update.txt")

        def nested_update(sender: object, **kwargs: object) -> None:
            if sender is self.Item and kwargs.get("name") == "nested-update-file":
                existing.update(document=payload, ignore_permission=True)

        post_data_change.connect(nested_update, weak=False)
        self.addCleanup(post_data_change.disconnect, nested_update)
        with (
            patch(
                "general_manager.interface.capabilities.orm.mutations.prepare_upload_claims"
            ) as prepare,
            patch(
                "general_manager.interface.capabilities.orm.mutations.run_upload_transaction"
            ) as run_upload,
            self.assertRaises(CreateManyError) as raised,
        ):
            list(
                self.Item.create_many(
                    [self._record("nested-update-file")],
                    ignore_permission=True,
                )
            )

        self.assertIsInstance(raised.exception.cause, CreateManyUnsupportedError)
        prepare.assert_not_called()
        run_upload.assert_not_called()
        self.assertFalse(
            self.ItemModel.objects.filter(name="nested-update-file").exists()
        )
        existing_row = self.UploadItem.Interface._model.objects.get(
            name="nested-update-upload"
        )
        self.assertEqual(existing_row.document.name, "existing.txt")

    def test_actual_commit_failure_is_not_reported_as_post_commit_failure(self) -> None:
        connection = connections["default"]
        try:
            with (
                patch.object(
                    connection,
                    "commit",
                    side_effect=OperationalError("commit failed"),
                ),
                self.assertRaises(CreateManyError) as raised,
            ):
                list(
                    self.Item.create_many(
                        [self._record("commit-failure")], ignore_permission=True
                    )
                )
        finally:
            connection.close()
        self.assertNotIsInstance(raised.exception, CreateManyPostCommitError)
        self.assertFalse(raised.exception.committed)

    def test_receiver_rollback_flag_is_reported_and_discards_side_effects(self) -> None:
        callbacks: list[str] = []
        workflow_events: list[str] = []
        prior_registry = get_event_registry()
        registry = InMemoryEventRegistry()
        registry.register(
            "manager_created",
            handler=lambda event: workflow_events.append(event.event_id),
            registration_id="create-many-acceptance-rollback-manager-created",
        )
        configure_event_registry(registry)
        connect_workflow_signal_bridge()

        def schedule_callback(sender: object, **kwargs: object) -> None:
            if sender is self.Item and kwargs.get("name") == "rollback-flag":
                transaction.on_commit(
                    lambda: callbacks.append("committed"),
                    using=kwargs["database_alias"],
                )

        def mark_rollback(sender: object, **kwargs: object) -> None:
            if sender is self.Item and kwargs.get("name") == "rollback-flag":
                transaction.set_rollback(True, using=kwargs["database_alias"])

        post_data_change.connect(schedule_callback, weak=False)
        post_data_change.connect(mark_rollback, weak=False)
        self.addCleanup(post_data_change.disconnect, schedule_callback)
        self.addCleanup(post_data_change.disconnect, mark_rollback)
        self.addCleanup(disconnect_workflow_signal_bridge)
        self.addCleanup(configure_event_registry, prior_registry)

        with self.assertRaises(CreateManyError) as raised:
            list(
                self.Item.create_many(
                    [self._record("rollback-flag")], ignore_permission=True
                )
            )
        self.assertFalse(raised.exception.committed)
        self.assertFalse(self.ItemModel.objects.filter(name="rollback-flag").exists())
        self.assertEqual(callbacks, [])
        self.assertEqual(workflow_events, [])

    def test_manual_autocommit_transaction_is_rejected_before_source_consumption(
        self,
    ) -> None:
        connection = connections["default"]
        consumed: list[int] = []

        def source() -> Iterator[dict[str, object]]:
            consumed.append(1)
            yield self._record("manual-transaction")

        connection.set_autocommit(False)
        try:
            with self.assertRaises(CreateManyUnsupportedError):
                next(self.Item.create_many(source(), ignore_permission=True))
            self.assertEqual(consumed, [])
        finally:
            connection.rollback()
            connection.set_autocommit(True)

    def test_implicit_static_router_is_rejected_before_source_consumption(self) -> None:
        self_model = self.ItemModel

        class StaticSecondaryRouter:
            def db_for_write(
                self, model: type[models.Model], **hints: object
            ) -> str | None:
                del hints
                if model is self_model:
                    return "secondary"
                return None

        consumed: list[int] = []

        def source() -> Iterator[dict[str, object]]:
            consumed.append(1)
            yield self._record("routed-before-input")

        with override_settings(DATABASE_ROUTERS=[StaticSecondaryRouter()]):
            with self.assertRaises(CreateManyUnsupportedError):
                list(self.Item.create_many(source(), ignore_permission=True))
        self.assertEqual(consumed, [])
        self.assertFalse(
            self.ItemModel.objects.filter(name="routed-before-input").exists()
        )

    def test_instance_dependent_router_cannot_split_one_batch_across_aliases(
        self,
    ) -> None:
        self_model = self.ItemModel

        class InstanceSecondaryRouter:
            def db_for_write(
                self, model: type[models.Model], **hints: object
            ) -> str | None:
                instance = hints.get("instance")
                if (
                    model is self_model
                    and getattr(instance, "name", None) == "route-away"
                ):
                    return "secondary"
                return None

        with override_settings(DATABASE_ROUTERS=[InstanceSecondaryRouter()]):
            with self.assertRaises(CreateManyError) as raised:
                list(
                    self.Item.create_many(
                        [
                            self._record("default-row"),
                            self._record("route-away"),
                        ],
                        ignore_permission=True,
                        batch_size=2,
                    )
                )
        self.assertIsInstance(raised.exception.cause, CreateManyUnsupportedError)
        self.assertEqual(self.ItemModel.objects.count(), 0)

    def test_concurrent_conflicting_inserts_keep_unique_constraint_authoritative(
        self,
    ) -> None:
        if connections["default"].vendor == "sqlite":
            self.skipTest(
                "SQLite cannot provide the required concurrent insert fixture"
            )

        barrier = threading.Barrier(2)
        outcomes: list[object] = []
        lock = threading.Lock()

        original_full_clean = self.ItemModel.full_clean

        def synchronized_full_clean(
            instance: models.Model, *args: object, **kwargs: object
        ) -> None:
            """Let both workers finish validation before either insert begins."""
            original_full_clean(instance, *args, **kwargs)
            if getattr(instance, "name", None) == "concurrent":
                barrier.wait(timeout=10)

        def worker() -> None:
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                list(
                    self.Item.create_many(
                        [self._record("concurrent")], ignore_permission=True
                    )
                )
                outcome: object = "success"
            except BaseException as error:  # noqa: BLE001 - assert both worker outcomes.
                outcome = error
            finally:
                with lock:
                    outcomes.append(outcome)
                connections.close_all()

        with patch.object(self.ItemModel, "full_clean", synchronized_full_clean):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sum(outcome == "success" for outcome in outcomes), 1)
        errors = [
            outcome for outcome in outcomes if isinstance(outcome, CreateManyError)
        ]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0].cause, IntegrityError)
        self.assertEqual(self.ItemModel.objects.filter(name="concurrent").count(), 1)


class RoutedCreateManyIntegrationTests(GeneralManagerTransactionTestCase):
    """Verify canonical batch writes use the interface's configured alias."""

    databases: ClassVar[set[str]] = {"default", "secondary"}
    _secondary_created_models: ClassVar[list[type[models.Model]]] = []

    @classmethod
    def setUpClass(cls) -> None:
        class RoutedRecord(models.Model):
            name = models.CharField(max_length=100, unique=True)

            class Meta:
                app_label = "general_manager"

        class RoutedInterface(ExistingModelInterface):
            model = RoutedRecord
            database = "secondary"

        class RoutedManager(GeneralManager):
            Interface = RoutedInterface

        cls.RoutedRecord = RoutedRecord
        cls.RoutedManager = RoutedManager
        cls.general_manager_classes = [RoutedManager]
        cls._secondary_created_models = []
        super().setUpClass()
        cls._secondary_created_models = create_test_models(
            connections["secondary"],
            (RoutedRecord, RoutedRecord.history.model),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        with suppress(Exception):
            with connections["secondary"].schema_editor() as editor:
                drop_test_models(editor, reversed(cls._secondary_created_models))
        super().tearDownClass()

    def test_batch_result_and_rows_use_secondary_alias(self) -> None:
        results = list(
            self.RoutedManager.create_many(
                [{"name": "secondary-a"}, {"name": "secondary-b"}],
                ignore_permission=True,
            )
        )
        self.assertEqual(results[0].database_alias, "secondary")
        self.assertTrue(results[0].committed)
        self.assertEqual(self.RoutedRecord.objects.using("secondary").count(), 2)
        self.assertEqual(self.RoutedRecord.objects.using("default").count(), 0)


__all__ = [
    "CreateManyAcceptanceIntegrationTests",
    "RoutedCreateManyIntegrationTests",
]
