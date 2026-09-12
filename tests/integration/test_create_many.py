"""Integration coverage for bounded canonical ORM manager creation."""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

from django.db import transaction
from django.db.models import CharField

from general_manager.cache.signals import post_data_change
from general_manager.interface import DatabaseInterface
from general_manager.manager.bulk_create import (
    CreateManyError,
    CreateManyPostCommitError,
    CreateManyUnsupportedError,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class _ExpectedRollback(RuntimeError):
    """Test-only exception used to roll back a caller-owned transaction."""


class CreateManyIntegrationTests(GeneralManagerTransactionTestCase):
    """Exercise the public iterator contract against a generated ORM model."""

    Project: ClassVar[type[GeneralManager]]
    ProjectModel: ClassVar[type[object]]

    @classmethod
    def setUpClass(cls) -> None:
        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100, unique=True)

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

    def test_iterator_is_lazy_bounded_and_returns_immutable_progress(self) -> None:
        consumed: list[int] = []

        def records() -> Iterator[dict[str, object]]:
            for index in range(3):
                consumed.append(index)
                yield {"name": f"project-{index}"}

        result_iterator = self.Project.create_many(
            records(), ignore_permission=True, batch_size=2
        )
        self.assertEqual(consumed, [])

        first = next(result_iterator)

        self.assertEqual(consumed, [0, 1])
        self.assertEqual(first.input_range, range(0, 2))
        self.assertEqual(len(first.ids), 2)
        self.assertEqual(first.successful_count, 2)
        self.assertTrue(first.committed)
        with self.assertRaises(AttributeError):
            first.successful_count = 4  # type: ignore[misc]
        self.assertEqual(consumed, [0, 1])
        second = next(result_iterator)
        self.assertEqual(second.input_range, range(2, 3))
        self.assertEqual(consumed, [0, 1, 2])

    def test_failed_batch_rolls_back_every_row_and_reports_absolute_index(self) -> None:
        results = self.Project.create_many(
            [
                {"name": "first"},
                {"name": "second"},
                {"name": "second"},
                {"name": "fourth"},
            ],
            ignore_permission=True,
            batch_size=2,
        )
        first = next(results)
        self.assertEqual(first.successful_count, 2)

        with self.assertRaises(CreateManyError) as raised:
            next(results)

        failure = raised.exception
        self.assertEqual(failure.failure_index, 2)
        self.assertEqual(failure.input_range, range(2, 4))
        self.assertEqual(failure.successful_count, 2)
        self.assertFalse(failure.committed)
        self.assertEqual(self.ProjectModel.objects.count(), 2)
        self.assertFalse(self.ProjectModel.objects.filter(name="fourth").exists())

    def test_outer_transaction_marks_results_pending_and_can_rollback(self) -> None:
        with self.assertRaises(_ExpectedRollback):
            with transaction.atomic():
                results = list(
                    self.Project.create_many(
                        [{"name": "pending"}],
                        ignore_permission=True,
                    )
                )
                self.assertFalse(results[0].committed)
                raise _ExpectedRollback

        self.assertFalse(self.ProjectModel.objects.filter(name="pending").exists())

    def test_invalid_batch_size_and_custom_create_do_not_consume_input(self) -> None:
        consumed: list[object] = []

        def records() -> Iterator[dict[str, object]]:
            consumed.append(object())
            yield {"name": "not-consumed"}

        with self.assertRaises(ValueError):
            self.Project.create_many(records(), ignore_permission=True, batch_size=0)
        self.assertEqual(consumed, [])

        class Parent(self.Project):
            @classmethod
            def create(cls, **kwargs: object) -> GeneralManager:
                return super().create(**kwargs)

        class Child(Parent):
            pass

        with self.assertRaises(CreateManyUnsupportedError):
            Child.create_many(records(), ignore_permission=True)
        self.assertEqual(consumed, [])

    def test_post_commit_callback_failure_retains_committed_identifiers(self) -> None:
        def fail_on_commit(sender: object, **kwargs: object) -> None:
            if sender is self.Project and kwargs.get("action") == "create":
                transaction.on_commit(
                    lambda: (_ for _ in ()).throw(RuntimeError("post commit failed")),
                    using="default",
                )

        post_data_change.connect(fail_on_commit, weak=False)
        self.addCleanup(post_data_change.disconnect, fail_on_commit)

        with self.assertRaises(CreateManyPostCommitError) as raised:
            list(
                self.Project.create_many(
                    [{"name": "post-commit"}], ignore_permission=True
                )
            )

        error = raised.exception
        self.assertTrue(error.committed)
        self.assertEqual(len(error.ids), 1)
        self.assertEqual(error.successful_count, 1)
        self.assertEqual(error.committed_successful_count, 1)
        self.assertEqual(error.pending_successful_count, 0)
        self.assertTrue(self.ProjectModel.objects.filter(name="post-commit").exists())
