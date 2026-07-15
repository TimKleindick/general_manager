"""Integration tests for related search invalidation lifecycle handling."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import patch

from django.db import transaction
from django.db.models import CharField

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.search.config import IndexConfig, SearchInvalidationRule
from general_manager.search.indexer import SearchDeleteTarget
from general_manager.search.models import SearchIndexState
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class RelatedRollback(RuntimeError):
    """Intentional transaction rollback for integration tests."""


class ResolverFailure(RuntimeError):
    """Intentional related resolver failure for integration tests."""


def _raise_related_rollback() -> None:
    """Raise the intentional rollback outside the transaction test body."""
    raise RelatedRollback


class SearchRelatedInvalidationIntegrationTests(GeneralManagerTransactionTestCase):
    """Exercise resolver phases and commit-safe related search work."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create isolated ORM-backed source and owner managers."""

        class Article(GeneralManager):
            class Interface(DatabaseInterface):
                title = CharField(max_length=200)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=200)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

        cls.Article = Article
        cls.ArticleModel = Article.Interface._model
        cls.Project = Project
        cls.ProjectModel = Project.Interface._model
        cls.general_manager_classes = [Article, Project]
        GeneralManagerMeta.all_classes = cls.general_manager_classes
        super().setUpClass()

    def _configure_project(self, resolver: object | None) -> None:
        """Attach one related invalidation rule to the project owner."""

        class SearchConfig:
            indexes = (IndexConfig(name="global", fields=["name"]),)
            invalidation_rules = (
                SearchInvalidationRule(
                    source=self.Article,
                    resolve=resolver,  # type: ignore[arg-type]
                ),
            )

        self.Project.SearchConfig = SearchConfig
        self.addCleanup(delattr, self.Project, "SearchConfig")

    def test_create_resolver_runs_synchronously_and_dispatch_waits_for_commit(
        self,
    ) -> None:
        """Create resolves after mutation but external work waits for commit."""
        phases: list[tuple[str, str, str]] = []
        project = self.Project.create(name="owner", ignore_permission=True)

        def resolve(change, _owner):
            phases.append((change.action, change.phase, change.instance.title))
            return (project,)

        self._configure_project(resolve)
        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            with transaction.atomic():
                self.Article.create(title="new", ignore_permission=True)
                self.assertEqual(phases, [("create", "after", "new")])
                dispatch.assert_not_called()

            dispatch.assert_called_once()
            self.assertEqual(
                dispatch.call_args.args[0].split(".")[-1],
                "Project",
            )
            self.assertEqual(
                dispatch.call_args.args[2][0],
                project.identification,
            )
            self.assertEqual(dispatch.call_args.args[1], "global")

    def test_update_resolves_old_and_new_owner_targets(self) -> None:
        """Update unions the before and after resolver results."""
        phases: list[tuple[str, str]] = []
        old_project = self.Project.create(name="old owner", ignore_permission=True)
        new_project = self.Project.create(name="new owner", ignore_permission=True)
        article = self.Article.create(title="old", ignore_permission=True)

        def resolve(change, _owner):
            phases.append((change.phase, change.instance.title))
            return (old_project if change.phase == "before" else new_project,)

        self._configure_project(resolve)
        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            with transaction.atomic():
                article.update(title="new", ignore_permission=True)
                dispatch.assert_not_called()

        self.assertEqual(phases, [("before", "old"), ("after", "new")])
        self.assertEqual(
            list(dispatch.call_args.args[2]),
            [old_project.identification, new_project.identification],
        )

    def test_delete_resolves_before_and_preserves_direct_delete_lane(self) -> None:
        """Related upserts and immutable direct deletes remain separate work."""
        phases: list[tuple[str, str]] = []
        project = self.Project.create(name="delete owner", ignore_permission=True)
        article = self.Article.create(title="gone", ignore_permission=True)

        def resolve(change, _owner):
            phases.append((change.phase, change.instance.title))
            return (project,)

        self._configure_project(resolve)

        class ArticleSearchConfig:
            indexes = (IndexConfig(name="global", fields=["title"]),)

        self.Article.SearchConfig = ArticleSearchConfig
        self.addCleanup(delattr, self.Article, "SearchConfig")
        target = SearchDeleteTarget(
            manager_class=self.Article,
            manager_path=f"{self.Article.__module__}.{self.Article.__name__}",
            index_name="global",
            document_id="article-1",
        )

        with (
            patch(
                "general_manager.search.invalidation.capture_delete_targets",
                return_value=(target,),
            ),
            patch(
                "general_manager.search.invalidation.dispatch_index_manager_batch"
            ) as upsert,
            patch(
                "general_manager.search.invalidation.dispatch_delete_documents"
            ) as delete,
        ):
            article.delete(ignore_permission=True)

        self.assertEqual(phases, [("before", "gone")])
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[2][0], project.identification)
        delete.assert_called_once()

    def test_outer_rollback_discards_related_work(self) -> None:
        """Neither rows nor related external work escape a rolled-back mutation."""
        project = self.Project.create(name="rollback owner", ignore_permission=True)
        self._configure_project(lambda _change, _owner: (project,))

        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            with self.assertRaises(RelatedRollback):
                with transaction.atomic():
                    self.Article.create(title="rolled back", ignore_permission=True)
                    raise RelatedRollback

        dispatch.assert_not_called()
        self.assertFalse(self.ArticleModel.objects.filter(title="rolled back").exists())

    def test_savepoint_rollback_discards_related_work(self) -> None:
        """Related callbacks registered below a rolled-back savepoint disappear."""
        project = self.Project.create(name="savepoint owner", ignore_permission=True)
        self._configure_project(lambda _change, _owner: (project,))

        with patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch:
            with transaction.atomic():
                try:
                    with transaction.atomic():
                        self.Article.create(
                            title="savepoint rollback", ignore_permission=True
                        )
                        _raise_related_rollback()
                except RelatedRollback:
                    pass

        dispatch.assert_not_called()

    def test_resolver_failure_does_not_abort_mutation_and_leaves_pair_dirty(
        self,
    ) -> None:
        """A resolver exception degrades only its exact pair to reconciliation."""

        def fail(_change, _owner):
            raise ResolverFailure

        self._configure_project(fail)
        with (
            patch(
                "general_manager.search.invalidation.dispatch_index_manager_batch"
            ) as dispatch,
            self.assertLogs("general_manager.search.invalidation", level="WARNING"),
        ):
            article = self.Article.create(
                title="still committed", ignore_permission=True
            )

        self.assertTrue(
            self.ArticleModel.objects.filter(pk=article.identification["id"]).exists()
        )
        dispatch.assert_not_called()
        state = SearchIndexState.objects.get(
            manager_path=f"{self.Project.__module__}.{self.Project.__name__}",
            index_name="global",
        )
        self.assertIsNotNone(state.dirty_since)
