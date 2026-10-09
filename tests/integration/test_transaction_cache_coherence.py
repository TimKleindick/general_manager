"""Native cache coherence across caller-owned database transactions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from django.core.cache import caches
from django.db import connections, models, transaction

from general_manager.cache.cache_decorator import cached
from general_manager.cache.dependency_index import is_dependency_data_change_active
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TransactionCacheCoherenceTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class TransactionProbeRow(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100, unique=True)
                amount = models.IntegerField()

            class BulkCreate:
                enabled = True
                local_permissions = True
                local_search = True

        cls.Row = TransactionProbeRow
        cls.general_manager_classes = [TransactionProbeRow]
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.Row.create(name="a", amount=1250, ignore_permission=True)
        self.cache_backend = caches["default"]
        self.read_started = Event()
        self.allow_publication = Event()
        self.pause_reader = False
        self.calls = 0

        @cached(cache="dependency")
        def total():
            self.calls += 1
            value = sum(row.amount for row in self.Row.all())
            if self.pause_reader:
                self.read_started.set()
                if not self.allow_publication.wait(10):
                    raise TimeoutError
            return value

        self.total = total

    def read_on_separate_connection(self):
        # Share cache storage while Django supplies a distinct thread-local DB
        # connection. No transaction/cache behavior is replaced.
        caches._connections.default = self.cache_backend
        try:
            return self.total()
        finally:
            connections.close_all()

    def require_concurrent_database(self):
        if connections["default"].vendor == "sqlite":
            self.skipTest("SQLite shared-memory tables lock during pending writes")

    def test_bulk_reader_before_outer_commit_cannot_poison_cache(self):
        self.require_concurrent_database()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                batches = list(
                    self.Row.create_many(
                        [{"name": "b", "amount": 625}, {"name": "c", "amount": 625}],
                        batch_size=1,
                        ignore_permission=True,
                    )
                )
                self.assertTrue(all(not batch.committed for batch in batches))
                self.assertEqual(
                    pool.submit(self.read_on_separate_connection).result(10), 1250
                )
            self.assertEqual(
                pool.submit(self.read_on_separate_connection).result(10), 2500
            )
            self.assertEqual(
                pool.submit(self.read_on_separate_connection).result(10), 2500
            )

    def test_sql_bulk_savepoint_rollback_discards_warmup_work(self):
        self.assertEqual(self.total(), 1250)
        failure = RuntimeError("rollback SQL bulk savepoint")
        with patch(
            "general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup"
        ) as enqueue:
            with transaction.atomic():
                with self.assertRaises(RuntimeError):
                    with transaction.atomic():
                        list(
                            self.Row.create_many(
                                [{"name": "b", "amount": 1250}], ignore_permission=True
                            )
                        )
                        raise failure
            enqueue.assert_not_called()
        self.assertFalse(is_dependency_data_change_active())
        self.assertEqual(self.total(), 1250)

    def test_reader_started_inside_transaction_is_fenced_after_commit(self):
        self.require_concurrent_database()
        self.pause_reader = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                with transaction.atomic():
                    list(
                        self.Row.create_many(
                            [{"name": "b", "amount": 1250}], ignore_permission=True
                        )
                    )
                    pending = pool.submit(self.read_on_separate_connection)
                    self.assertTrue(self.read_started.wait(10))
                self.pause_reader = False
            finally:
                self.allow_publication.set()
            self.assertEqual(pending.result(10), 1250)
            self.assertEqual(
                pool.submit(self.read_on_separate_connection).result(10), 2500
            )

    def test_canonical_mutations_preserve_cache_coherence_at_outer_commit(self):
        self.require_concurrent_database()
        for action in ("create", "update", "delete", "canonical_batch"):
            with self.subTest(action=action):
                self.Row.Interface._model.objects.all().delete()
                row = self.Row.create(name="a", amount=1250, ignore_permission=True)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with transaction.atomic():
                        if action == "create":
                            self.Row.create(
                                name="b", amount=1250, ignore_permission=True
                            )
                        elif action == "update":
                            row.update(amount=2500, ignore_permission=True)
                        elif action == "delete":
                            row.delete(ignore_permission=True)
                        else:
                            with patch.object(self.Row.BulkCreate, "enabled", False):
                                list(
                                    self.Row.create_many(
                                        [{"name": "b", "amount": 1250}],
                                        ignore_permission=True,
                                    )
                                )
                        self.assertEqual(
                            pool.submit(self.read_on_separate_connection).result(10),
                            1250,
                        )
                    expected = 0 if action == "delete" else 2500
                    self.assertEqual(
                        pool.submit(self.read_on_separate_connection).result(10),
                        expected,
                    )

    def test_one_writer_commit_cannot_release_another_writers_barrier(self):
        self.require_concurrent_database()
        writer_started = Event()
        allow_commit = Event()

        def writer():
            caches._connections.default = self.cache_backend
            try:
                with transaction.atomic():
                    self.Row.create(name="b", amount=1250, ignore_permission=True)
                    writer_started.set()
                    if not allow_commit.wait(10):
                        raise TimeoutError
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(writer)
            try:
                self.assertTrue(writer_started.wait(10))
                with transaction.atomic():
                    self.Row.create(name="c", amount=1250, ignore_permission=True)
                    self.assertEqual(
                        pool.submit(self.read_on_separate_connection).result(10), 1250
                    )
                self.assertTrue(is_dependency_data_change_active())
                self.assertEqual(
                    pool.submit(self.read_on_separate_connection).result(10), 2500
                )
            finally:
                allow_commit.set()
            pending.result(10)
            self.assertFalse(is_dependency_data_change_active())
            self.assertEqual(
                pool.submit(self.read_on_separate_connection).result(10), 3750
            )

    def test_manual_commit_and_rollback_release_the_connection_barrier(self):
        self.require_concurrent_database()
        for commit in (False, True):
            with self.subTest(commit=commit):
                transaction.set_autocommit(False)
                try:
                    self.Row.create(name="manual", amount=1250, ignore_permission=True)
                    self.assertTrue(is_dependency_data_change_active())
                    if commit:
                        transaction.commit()
                    else:
                        transaction.rollback()
                    self.assertFalse(is_dependency_data_change_active())
                finally:
                    transaction.rollback()
                    transaction.set_autocommit(True)
                self.assertEqual(self.total(), 2500 if commit else 1250)

    def test_new_manual_write_after_cleanup_retry_keeps_completion_hooks(self):
        from general_manager.cache import dependency_index

        original_end = dependency_index.end_dependency_data_change
        failure = ConnectionError("owned rollback cleanup unavailable")

        def fail_owned_end(*, owner=None):
            if owner is not None:
                raise failure
            return original_end()

        transaction.set_autocommit(False)
        try:
            if connections["default"].vendor == "sqlite":
                # SQLite's manual-autocommit mode needs an explicit BEGIN;
                # otherwise releasing the first savepoint commits its rows.
                with connections["default"].cursor() as cursor:
                    cursor.execute("BEGIN")
            self.Row.create(name="rolled-back", amount=1250, ignore_permission=True)
            with patch.object(
                dependency_index,
                "end_dependency_data_change",
                side_effect=fail_owned_end,
            ):
                transaction.rollback()
            if connections["default"].vendor == "sqlite":
                with connections["default"].cursor() as cursor:
                    cursor.execute("BEGIN")
            self.Row.create(name="committed", amount=1250, ignore_permission=True)
            transaction.commit()
            self.assertFalse(is_dependency_data_change_active())
        finally:
            transaction.rollback()
            transaction.set_autocommit(True)
        self.assertEqual(self.total(), 2500)
