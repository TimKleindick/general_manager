"""Dependency-cache barriers follow the actual database transaction lifetime."""

from contextlib import suppress
from typing import ClassVar
from unittest.mock import patch

from django.db import DatabaseError, connections, models, transaction

from general_manager.cache.cache_decorator import cached
from general_manager.cache import dependency_index
from general_manager.cache.dependency_index import is_dependency_data_change_active
from general_manager.cache.signals import post_data_change
from general_manager.cache.run_context import CalculationRunContext
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.bulk_create import CreateManyPostCommitError
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from tests.utils.database import create_test_models, drop_test_models


class _ExpectedRollback(RuntimeError):
    """Deliberately abort an outer transaction or nested savepoint."""


class TransactionCacheCleanupTests(GeneralManagerTransactionTestCase):
    """Exercise native canonical mutations without application transaction helpers."""

    database_alias: ClassVar[str] = "default"
    Row: ClassVar[type[GeneralManager]]
    RowModel: ClassVar[type[models.Model]]
    _secondary_models: ClassVar[list[type[models.Model]]] = []

    @classmethod
    def setUpClass(cls) -> None:
        alias = cls.database_alias

        class TransactionCleanupRow(GeneralManager):
            class Interface(DatabaseInterface):
                database = alias
                name = models.CharField(max_length=100, unique=True)
                amount = models.IntegerField()

        cls.Row = TransactionCleanupRow
        cls.RowModel = TransactionCleanupRow.Interface._model
        cls.general_manager_classes = [TransactionCleanupRow]
        cls._secondary_models = []
        super().setUpClass()
        if alias != "default":
            try:
                cls._secondary_models = create_test_models(
                    connections[alias],
                    (cls.RowModel, cls.RowModel.history.model),
                )
            except Exception:
                with suppress(Exception):
                    super().tearDownClass()
                raise

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            if cls._secondary_models:
                with connections[cls.database_alias].schema_editor() as editor:
                    drop_test_models(editor, reversed(cls._secondary_models))
        finally:
            cls._secondary_models = []
            super().tearDownClass()

    def setUp(self) -> None:
        super().setUp()
        self.seed = self.Row.create(name="seed", amount=10, ignore_permission=True)
        self.calls = 0

        @cached(cache="dependency")
        def total() -> int:
            self.calls += 1
            return sum(row.amount for row in self.Row.all())

        self.total = total
        self.assert_settled(10)
        self.assertEqual(self.calls, 1)

    def assert_pending(self, expected: int) -> None:
        self.assertTrue(is_dependency_data_change_active())
        self.assertEqual(
            self.RowModel.objects.using(self.database_alias).aggregate(
                total=models.Sum("amount")
            )["total"]
            or 0,
            expected,
        )
        calls = self.calls
        self.assertEqual(self.total(), expected)
        self.assertEqual(self.total(), expected)
        self.assertEqual(self.calls, calls + 2)

    def assert_settled(self, expected: int) -> None:
        self.assertFalse(is_dependency_data_change_active())
        self.assertEqual(
            self.RowModel.objects.using(self.database_alias).aggregate(
                total=models.Sum("amount")
            )["total"]
            or 0,
            expected,
        )
        self.assertEqual(self.total(), expected)
        calls = self.calls
        self.assertEqual(self.total(), expected)
        self.assertEqual(self.calls, calls)

    def history_count(self) -> int:
        return self.RowModel.history.using(self.database_alias).count()

    def test_transaction_completion_restores_connection_methods(self):
        connection = connections[self.database_alias]
        with transaction.atomic(using=self.database_alias):
            self.Row.create(name="pending", amount=5, ignore_permission=True)
            self.assertIn("_general_manager_dependency_barrier", vars(connection))
        self.assertNotIn("_general_manager_dependency_barrier", vars(connection))
        for method in (
            "commit",
            "rollback",
            "close",
            "set_autocommit",
            "savepoint_rollback",
        ):
            self.assertNotIn(method, vars(connection))

    def test_failed_owned_begin_restores_existing_connection_methods(self):
        connection = connections[self.database_alias]
        original_begin = dependency_index.begin_dependency_data_change
        original_commit = connection.commit
        failure = ConnectionError("transaction barrier start unavailable")

        def commit_probe():
            original_commit()

        def fail_owned_begin(*, owner=None):
            if owner is not None:
                raise failure
            return original_begin()

        with patch.object(connection, "commit", commit_probe):
            with transaction.atomic(using=self.database_alias):
                with patch.object(
                    dependency_index,
                    "begin_dependency_data_change",
                    side_effect=fail_owned_begin,
                ):
                    with self.assertRaises(ConnectionError):
                        self.Row.create(
                            name="not-written", amount=5, ignore_permission=True
                        )
                self.assertIs(connection.commit, commit_probe)
                self.assertNotIn(
                    "_general_manager_dependency_barrier", vars(connection)
                )
        self.assert_settled(10)
        self.assertEqual(self.history_count(), 1)

    def test_pending_cleanup_retries_even_when_commit_callback_raises(self):
        primary = RuntimeError("user commit callback failed")
        cleanup = ConnectionError("commit cleanup unavailable")
        original_end = dependency_index.end_dependency_data_change
        failed = False

        def fail_owned_once(*, owner=None):
            nonlocal failed
            if owner is not None and not failed:
                failed = True
                raise cleanup
            return original_end(owner=owner)

        def fail_on_commit():
            raise primary

        with patch.object(
            dependency_index, "end_dependency_data_change", side_effect=fail_owned_once
        ):
            with self.assertRaises(RuntimeError) as raised:
                with transaction.atomic(using=self.database_alias):
                    transaction.on_commit(fail_on_commit, using=self.database_alias)
                    self.Row.create(name="committed", amount=5, ignore_permission=True)
        self.assertIs(raised.exception, primary)
        self.assert_settled(15)
        self.assertNotIn(
            "_general_manager_dependency_barrier",
            vars(connections[self.database_alias]),
        )

    def test_interrupted_commit_cleanup_preserves_primary_during_retry(self):
        primary = KeyboardInterrupt("commit cleanup interrupted")
        retry_failure = ConnectionError("cleanup retry unavailable")
        original_end = dependency_index.end_dependency_data_change
        interrupted = False

        def interrupt_owned_end(*, owner=None):
            nonlocal interrupted
            if owner is not None:
                if not interrupted:
                    interrupted = True
                    raise primary
                raise retry_failure
            return original_end()

        with patch.object(
            dependency_index,
            "end_dependency_data_change",
            side_effect=interrupt_owned_end,
        ):
            with self.assertRaises(KeyboardInterrupt) as raised:
                with transaction.atomic(using=self.database_alias):
                    self.Row.create(name="committed", amount=5, ignore_permission=True)
        self.assertIs(raised.exception, primary)
        self.assert_pending(15)
        connections[self.database_alias].set_autocommit(True)
        self.assert_settled(15)

    def test_unavailable_cleanup_after_commit_preserves_committed_rows_for_retry(self):
        original_end = dependency_index.end_dependency_data_change
        failure = ConnectionError("commit cleanup unavailable")

        def fail_owned_end(*, owner=None):
            if owner is not None:
                raise failure
            return original_end()

        with patch.object(
            dependency_index, "end_dependency_data_change", side_effect=fail_owned_end
        ):
            with self.assertRaises(ConnectionError):
                with transaction.atomic(using=self.database_alias):
                    self.Row.create(name="committed", amount=5, ignore_permission=True)
        self.assert_pending(15)
        connections[self.database_alias].set_autocommit(True)
        self.assert_settled(15)
        self.assertEqual(self.history_count(), 2)

    def test_cache_database_error_after_commit_keeps_batch_outcome_committed(self):
        failure = DatabaseError("cache database cleanup unavailable")
        original_end = dependency_index.end_dependency_data_change
        failed = False

        def fail_owned_once(*, owner=None):
            nonlocal failed
            if owner is not None and not failed:
                failed = True
                raise failure
            original_end(owner=owner)

        with patch.object(
            dependency_index, "end_dependency_data_change", side_effect=fail_owned_once
        ):
            try:
                batches = list(
                    self.Row.create_many(
                        [{"name": "committed", "amount": 5}], ignore_permission=True
                    )
                )
            except CreateManyPostCommitError as error:
                self.assertTrue(error.committed)
            else:
                self.assertTrue(batches[0].committed)
        self.assert_settled(15)

    def test_savepoint_cleanup_failure_preserves_primary_exception(self):
        primary = _ExpectedRollback("primary savepoint failure")
        cleanup = ConnectionError("savepoint cache cleanup unavailable")
        try:
            with patch(
                "general_manager.cache.transaction_barrier._clear_transaction_run_cache",
                side_effect=cleanup,
            ):
                with self.assertRaises(_ExpectedRollback) as raised:
                    with transaction.atomic(using=self.database_alias):
                        with transaction.atomic(using=self.database_alias):
                            self.Row.create(
                                name="pending", amount=5, ignore_permission=True
                            )
                            raise primary
                self.assertIs(raised.exception, primary)
        finally:
            connections[self.database_alias].set_autocommit(True)
        self.assert_settled(10)

    def test_rolled_back_bulk_savepoint_does_not_enqueue_warmup(self):
        with patch(
            "general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup"
        ) as enqueue:
            with transaction.atomic(using=self.database_alias):
                with self.assertRaises(_ExpectedRollback):
                    with transaction.atomic(using=self.database_alias):
                        list(
                            self.Row.create_many(
                                [{"name": "pending", "amount": 5}],
                                ignore_permission=True,
                            )
                        )
                        raise _ExpectedRollback
            enqueue.assert_not_called()
        self.assert_settled(10)

    def test_nested_autocommit_warmup_waits_for_enclosing_mutation_barrier(self):
        marker = "nested-autocommit-recipe"

        def nested_write(sender, **kwargs):
            if sender is self.Row:
                if kwargs["instance"].name == "outer":
                    self.Row.create(name="inner", amount=5, ignore_permission=True)
                elif kwargs["instance"].name == "inner":
                    dependency_index.record_invalidated_cache_keys_for_graphql_rewarm(
                        (marker,)
                    )

        post_data_change.connect(nested_write, weak=False)
        self.addCleanup(post_data_change.disconnect, nested_write)
        barriers = []
        keys = []

        def enqueue(cache_keys):
            barriers.append(is_dependency_data_change_active())
            keys.extend(cache_keys)

        with patch(
            "general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup",
            side_effect=enqueue,
        ):
            self.Row.create(name="outer", amount=5, ignore_permission=True)
        self.assertIn(marker, keys)
        self.assertTrue(barriers)
        self.assertFalse(any(barriers))

    def test_savepoint_rollback_clears_run_local_rows(self):
        with CalculationRunContext():
            with transaction.atomic(using=self.database_alias):
                with self.assertRaises(_ExpectedRollback):
                    with transaction.atomic(using=self.database_alias):
                        self.Row.create(
                            name="pending", amount=5, ignore_permission=True
                        )
                        self.assertTrue(is_dependency_data_change_active())
                        self.assertEqual(self.total(), 15)
                        raise _ExpectedRollback
                self.assertTrue(is_dependency_data_change_active())
                self.assertEqual(self.total(), 10)
            self.assert_settled(10)

    def test_applied_release_error_still_clears_rolled_back_run_local_rows(self):
        actual_end = dependency_index.end_dependency_data_change
        cleanup = ConnectionError("applied owner release failure")
        failed = False

        def fail_after_release(*, owner=None):
            nonlocal failed
            actual_end(owner=owner)
            if owner is not None and not failed:
                failed = True
                raise cleanup

        with CalculationRunContext():
            with patch.object(
                dependency_index,
                "end_dependency_data_change",
                side_effect=fail_after_release,
            ):
                with self.assertRaises(_ExpectedRollback):
                    with transaction.atomic(using=self.database_alias):
                        self.Row.create(
                            name="pending", amount=5, ignore_permission=True
                        )
                        self.assertEqual(self.total(), 15)
                        raise _ExpectedRollback
            self.assert_settled(10)

    def test_dependency_cleanup_error_still_clears_rolled_back_orm_rows(self):
        cleanup = ConnectionError("dependency lease cleanup unavailable")
        with CalculationRunContext() as context:
            original_discard = context.discard_dependency_cache_state

            def fail_after_rollback():
                if not connections[self.database_alias].in_atomic_block:
                    raise cleanup
                original_discard()

            with patch.object(
                context,
                "discard_dependency_cache_state",
                side_effect=fail_after_rollback,
            ):
                with self.assertRaises(_ExpectedRollback):
                    with transaction.atomic(using=self.database_alias):
                        self.Row.create(
                            name="pending", amount=5, ignore_permission=True
                        )
                        self.assertEqual(sum(row.amount for row in self.Row.all()), 15)
                        raise _ExpectedRollback
                self.assertFalse(is_dependency_data_change_active())
                self.assertEqual(sum(row.amount for row in self.Row.all()), 10)
            connections[self.database_alias].set_autocommit(True)

    def test_rollback_cleanup_failure_preserves_primary_during_autocommit_retry(self):
        primary = _ExpectedRollback("primary mutation failure")
        cleanup = ConnectionError("owner cleanup unavailable")
        actual_end = dependency_index.end_dependency_data_change

        def fail_owned_end(*, owner=None):
            if owner is not None:
                raise cleanup
            return actual_end()

        try:
            with patch.object(
                dependency_index,
                "end_dependency_data_change",
                side_effect=fail_owned_end,
            ):
                with self.assertRaises(_ExpectedRollback) as raised:
                    with transaction.atomic(using=self.database_alias):
                        self.Row.create(
                            name="pending", amount=5, ignore_permission=True
                        )
                        raise primary
                self.assertIs(raised.exception, primary)
        finally:
            connections[self.database_alias].set_autocommit(True)
        self.assert_settled(10)

    def test_rollback_cleanup_retry_does_not_enqueue_rolled_back_keys(self):
        primary = _ExpectedRollback("primary mutation failure")
        actual_end = dependency_index.end_dependency_data_change
        cleanup = ConnectionError("owner cleanup unavailable once")
        failed = False

        def record_key(sender, **kwargs):
            if sender is self.Row:
                dependency_index.record_invalidated_cache_keys_for_graphql_rewarm(
                    ("rolled-back-key",)
                )

        post_data_change.connect(record_key, weak=False)
        self.addCleanup(post_data_change.disconnect, record_key)

        def fail_owned_once(*, owner=None):
            nonlocal failed
            if owner is not None and not failed:
                failed = True
                raise cleanup
            return actual_end(owner=owner)

        with patch(
            "general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup"
        ) as enqueue:
            with patch.object(
                dependency_index,
                "end_dependency_data_change",
                side_effect=fail_owned_once,
            ):
                with self.assertRaises(_ExpectedRollback):
                    with transaction.atomic(using=self.database_alias):
                        self.Row.create(
                            name="pending", amount=5, ignore_permission=True
                        )
                        raise primary
            enqueue.assert_not_called()
        self.assert_settled(10)

    def test_create_keeps_barrier_until_outer_commit(self) -> None:
        with transaction.atomic(using=self.database_alias):
            self.Row.create(name="created", amount=5, ignore_permission=True)
            self.assert_pending(15)
        self.assert_settled(15)
        self.assertEqual(self.history_count(), 2)

    def test_update_keeps_barrier_until_outer_commit(self) -> None:
        with transaction.atomic(using=self.database_alias):
            self.seed.update(amount=25, ignore_permission=True)
            self.assert_pending(25)
        self.assert_settled(25)
        self.assertEqual(self.history_count(), 2)

    def test_delete_keeps_barrier_until_outer_commit(self) -> None:
        with transaction.atomic(using=self.database_alias):
            self.seed.delete(ignore_permission=True)
            self.assert_pending(0)
        self.assert_settled(0)
        self.assertEqual(self.history_count(), 2)

    def test_canonical_create_many_keeps_barrier_between_pending_batches(self) -> None:
        # No BulkCreate opt-in: each row takes the public canonical create path.
        with transaction.atomic(using=self.database_alias):
            batches = self.Row.create_many(
                [{"name": "first", "amount": 7}, {"name": "second", "amount": 3}],
                batch_size=1,
                ignore_permission=True,
            )
            first = next(batches)
            self.assertFalse(first.committed)
            self.assert_pending(17)
            second = next(batches)
            self.assertFalse(second.committed)
            self.assert_pending(20)
            self.assertEqual(list(batches), [])
        self.assert_settled(20)
        self.assertEqual(self.history_count(), 3)

    def test_outer_rollback_discards_mutations_and_their_history(self) -> None:
        error = _ExpectedRollback("outer rollback")
        with self.assertRaises(_ExpectedRollback) as raised:
            with transaction.atomic(using=self.database_alias):
                self.seed.update(amount=99, ignore_permission=True)
                self.Row.create(name="pending", amount=7, ignore_permission=True)
                self.seed.delete(ignore_permission=True)
                list(
                    self.Row.create_many(
                        [{"name": "bulk", "amount": 5}], ignore_permission=True
                    )
                )
                self.assert_pending(12)
                self.assertEqual(self.history_count(), 5)
                raise error
        self.assertIs(raised.exception, error)
        self.assert_settled(10)
        self.assertEqual(self.history_count(), 1)
        self.assertEqual(
            list(
                self.RowModel.objects.using(self.database_alias).values_list(
                    "name", "amount"
                )
            ),
            [("seed", 10)],
        )

    def test_savepoint_release_keeps_barrier_until_outer_commit(self) -> None:
        with transaction.atomic(using=self.database_alias):
            with transaction.atomic(using=self.database_alias):
                self.Row.create(name="nested", amount=5, ignore_permission=True)
                self.assert_pending(15)
            self.assert_pending(15)
        self.assert_settled(15)
        self.assertEqual(self.history_count(), 2)

    def test_savepoint_rollback_preserves_outer_pending_write(self) -> None:
        with transaction.atomic(using=self.database_alias):
            self.Row.create(name="kept", amount=5, ignore_permission=True)
            with self.assertRaises(_ExpectedRollback):
                with transaction.atomic(using=self.database_alias):
                    self.seed.update(amount=90, ignore_permission=True)
                    self.Row.create(name="discarded", amount=7, ignore_permission=True)
                    self.assert_pending(102)
                    raise _ExpectedRollback
            self.assert_pending(15)
            self.assertEqual(self.history_count(), 2)
        self.assert_settled(15)

    def test_first_write_savepoint_rollback_still_cleans_up_at_outer_commit(
        self,
    ) -> None:
        with transaction.atomic(using=self.database_alias):
            with self.assertRaises(_ExpectedRollback):
                with transaction.atomic(using=self.database_alias):
                    self.Row.create(name="discarded", amount=5, ignore_permission=True)
                    self.assert_pending(15)
                    raise _ExpectedRollback
            self.assert_pending(10)
        self.assert_settled(10)
        self.assertEqual(self.history_count(), 1)

    def test_outer_rollback_discards_released_savepoint(self) -> None:
        with self.assertRaises(_ExpectedRollback):
            with transaction.atomic(using=self.database_alias):
                with transaction.atomic(using=self.database_alias):
                    self.Row.create(name="released", amount=5, ignore_permission=True)
                self.assert_pending(15)
                raise _ExpectedRollback
        self.assert_settled(10)
        self.assertEqual(self.history_count(), 1)

    def test_autocommit_mutations_release_barrier_and_repopulate_cache(self) -> None:
        self.Row.create(name="created", amount=5, ignore_permission=True)
        self.assert_settled(15)
        self.seed.update(amount=20, ignore_permission=True)
        self.assert_settled(25)
        self.seed.delete(ignore_permission=True)
        self.assert_settled(5)
        list(
            self.Row.create_many(
                [{"name": "bulk", "amount": 7}], ignore_permission=True
            )
        )
        self.assert_settled(12)
        self.assertEqual(self.history_count(), 5)

    def test_mutation_exception_is_preserved_and_rolls_back_history(self) -> None:
        error = RuntimeError("post-change receiver failed")

        def fail_after_write(sender: object, **kwargs: object) -> None:
            if sender is self.Row and kwargs.get("action") == "create":
                raise error

        post_data_change.connect(fail_after_write, weak=False)
        self.addCleanup(post_data_change.disconnect, fail_after_write)
        with self.assertRaises(RuntimeError) as raised:
            self.Row.create(name="failed", amount=5, ignore_permission=True)
        self.assertIs(raised.exception, error)
        self.assert_settled(10)
        self.assertEqual(self.history_count(), 1)

    def test_commit_callback_failure_releases_barrier_after_rows_commit(self) -> None:
        error = RuntimeError("commit callback failed")

        def fail_on_commit() -> None:
            raise error

        with self.assertRaises(RuntimeError) as raised:
            with transaction.atomic(using=self.database_alias):
                # Run first, before any cleanup callback a mutation registers.
                transaction.on_commit(fail_on_commit, using=self.database_alias)
                self.Row.create(name="committed", amount=5, ignore_permission=True)
                self.assert_pending(15)
        self.assertIs(raised.exception, error)
        self.assert_settled(15)
        self.assertEqual(self.history_count(), 2)

    def test_failed_mutation_preserves_caller_transaction_and_primary_exception(
        self,
    ) -> None:
        error = RuntimeError("post-change receiver failed")

        def fail_after_write(sender: object, **kwargs: object) -> None:
            if sender is self.Row and kwargs.get("action") == "create":
                raise error

        with transaction.atomic(using=self.database_alias):
            self.Row.create(name="kept", amount=5, ignore_permission=True)
            post_data_change.connect(fail_after_write, weak=False)
            self.addCleanup(post_data_change.disconnect, fail_after_write)
            with self.assertRaises(RuntimeError) as raised:
                self.Row.create(name="failed", amount=7, ignore_permission=True)
            self.assertIs(raised.exception, error)
            self.assert_pending(15)
            self.assertEqual(self.history_count(), 2)
        self.assert_settled(15)

    def test_actual_connection_close_rolls_back_and_releases_barrier(self) -> None:
        database = connections[self.database_alias]
        if database.vendor == "sqlite":
            self.skipTest("Django leaves shared in-memory SQLite connections open")
        with transaction.atomic(using=self.database_alias):
            self.Row.create(name="closed", amount=5, ignore_permission=True)
            self.assert_pending(15)
            database.close()
            self.assertFalse(is_dependency_data_change_active())
        self.assert_settled(10)
        self.assertEqual(self.history_count(), 1)

    def test_actual_manual_close_does_not_reopen_connection_for_warmup(self):
        connection = connections[self.database_alias]
        if connection.vendor == "sqlite":
            self.skipTest("Django leaves shared in-memory SQLite connections open")
        marker = "previously-committed-before-close"

        def eager_warmup(keys):
            self.RowModel.objects.using(self.database_alias).count()

        transaction.set_autocommit(False, using=self.database_alias)
        try:
            with patch(
                "general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup",
                side_effect=eager_warmup,
            ) as enqueue:
                self.Row.create(name="closed", amount=5, ignore_permission=True)
                dependency_index.record_committed_graphql_rewarm_keys((marker,))
                connection.close()
                self.assertIsNone(connection.connection)
                self.assertNotIn(
                    "_general_manager_dependency_barrier", vars(connection)
                )
                self.assertFalse(is_dependency_data_change_active())
                enqueue.assert_not_called()
                transaction.set_autocommit(True, using=self.database_alias)
                self.Row.create(name="next-writer", amount=5, ignore_permission=True)
                observed = [
                    key for call in enqueue.call_args_list for key in call.args[0]
                ]
                self.assertIn(marker, observed)
        finally:
            transaction.set_autocommit(True, using=self.database_alias)
        self.assert_settled(15)

    def test_noop_sqlite_close_keeps_barrier_until_transaction_completion(self):
        connection = connections[self.database_alias]
        if connection.vendor != "sqlite":
            self.skipTest("SQLite shared-memory close is the no-op case")
        with transaction.atomic(using=self.database_alias):
            self.Row.create(name="pending", amount=5, ignore_permission=True)
            connection.close()
            self.assert_pending(15)
        self.assert_settled(15)


class SecondaryTransactionCacheCleanupTests(TransactionCacheCleanupTests):
    """Apply the same transaction contract on the configured secondary alias."""

    database_alias: ClassVar[str] = "secondary"

    def test_committed_secondary_warmup_dispatches_after_default_rolls_back(self):
        marker = "committed-secondary-after-default-rollback"
        autocommit_states = []

        def eager_warmup(keys):
            autocommit_states.append(connections["default"].get_autocommit())
            self.RowModel.objects.using("default").count()

        def record_key(sender, **kwargs):
            if sender is self.Row and kwargs["instance"].name == "inner":
                dependency_index.record_invalidated_cache_keys_for_graphql_rewarm(
                    (marker,)
                )

        post_data_change.connect(record_key, weak=False)
        self.addCleanup(post_data_change.disconnect, record_key)
        with patch(
            "general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup",
            side_effect=eager_warmup,
        ) as enqueue:
            with self.assertRaises(_ExpectedRollback):
                with transaction.atomic(using="default"):
                    with patch.object(self.Row.Interface, "database", "default"):
                        self.Row.create(name="outer", amount=5, ignore_permission=True)
                    with transaction.atomic(using="secondary"):
                        self.Row.create(name="inner", amount=5, ignore_permission=True)
                    enqueue.assert_not_called()
                    raise _ExpectedRollback
            observed = [key for call in enqueue.call_args_list for key in call.args[0]]
            self.assertIn(marker, observed)
            self.assertTrue(autocommit_states)
            self.assertTrue(all(autocommit_states))
        self.assert_settled(15)

    def test_committed_secondary_warmup_dispatches_after_default_barrier_closes(self):
        marker = "committed-secondary-key"
        autocommit_states = []

        def eager_warmup(keys):
            autocommit_states.append(connections["default"].get_autocommit())
            self.RowModel.objects.using("default").count()

        def record_key(sender, **kwargs):
            if sender is self.Row and kwargs["instance"].name == "inner":
                dependency_index.record_invalidated_cache_keys_for_graphql_rewarm(
                    (marker,)
                )

        post_data_change.connect(record_key, weak=False)
        self.addCleanup(post_data_change.disconnect, record_key)
        with patch(
            "general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup",
            side_effect=eager_warmup,
        ) as enqueue:
            with transaction.atomic(using="default"):
                with patch.object(self.Row.Interface, "database", "default"):
                    self.Row.create(name="outer", amount=5, ignore_permission=True)
                with transaction.atomic(using="secondary"):
                    self.Row.create(name="inner", amount=5, ignore_permission=True)
                enqueue.assert_not_called()
            observed = [key for call in enqueue.call_args_list for key in call.args[0]]
            self.assertIn(marker, observed)
            self.assertTrue(autocommit_states)
            self.assertTrue(all(autocommit_states))

    def test_nested_secondary_write_does_not_take_outer_default_rewarm_keys(self):
        marker = "outer-default-rolled-back-key"

        def nested_write(sender, **kwargs):
            if sender is self.Row and kwargs["instance"].name == "outer":
                dependency_index.record_invalidated_cache_keys_for_graphql_rewarm(
                    (marker,)
                )
                with transaction.atomic(using="secondary"):
                    with patch.object(self.Row.Interface, "database", "secondary"):
                        self.Row.create(name="inner", amount=5, ignore_permission=True)

        post_data_change.connect(nested_write, weak=False)
        self.addCleanup(post_data_change.disconnect, nested_write)
        with patch(
            "general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup"
        ) as enqueue:
            with self.assertRaises(_ExpectedRollback):
                with transaction.atomic(using="default"):
                    with patch.object(self.Row.Interface, "database", "default"):
                        self.Row.create(name="outer", amount=5, ignore_permission=True)
                    raise _ExpectedRollback
            observed = [key for call in enqueue.call_args_list for key in call.args[0]]
            self.assertNotIn(marker, observed)

    databases: ClassVar[set[str]] = {"default", "secondary"}
