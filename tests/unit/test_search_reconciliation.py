from __future__ import annotations

import uuid
from datetime import timedelta
from typing import ClassVar
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import FieldConfig, IndexConfig
from general_manager.search.models import (
    SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
    SEARCH_INDEX_DIRTY_REASON_INITIALIZATION,
    SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED,
    SearchIndexState,
)
from general_manager.search.reconciliation import (
    build_search_schema_fingerprint,
    ensure_search_index_states,
    iter_search_index_targets,
    mark_search_indexes_dirty,
    manager_import_path,
)
from tests.utils.simple_manager_interface import BaseTestInterface


class ReconcileProjectInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}

    @classmethod
    def get_attribute_types(cls):
        """Return search-visible attribute type metadata."""
        return {"name": {"type": str}, "status": {"type": str}}

    @classmethod
    def get_attributes(cls):
        """Return static attribute extractors for reconciliation tests."""
        return {
            "name": lambda _interface: "Alpha",
            "status": lambda _interface: "public",
        }


class ReconcileProject(GeneralManager):
    Interface = ReconcileProjectInterface

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(
                name="global",
                fields=[FieldConfig("name", boost=2.0)],
                filters=["status"],
                sorts=["name"],
            )
        ]
        type_label = "ProjectDoc"
        update_strategy = "inline"


class SearchReconciliationDiscoveryTests(TestCase):
    def setUp(self) -> None:
        """Register the searchable manager class for discovery tests."""
        self._original_all_classes = list(GeneralManagerMeta.all_classes)
        GeneralManagerMeta.all_classes = [ReconcileProject]
        GeneralmanagerConfig.initialize_general_manager_classes(
            [ReconcileProject], [ReconcileProject]
        )

    def tearDown(self) -> None:
        """Restore the global manager class registry after each test."""
        GeneralManagerMeta.all_classes = self._original_all_classes
        super().tearDown()

    def test_manager_import_path_is_stable(self) -> None:
        """Build a stable import path for a manager class."""
        assert manager_import_path(ReconcileProject).endswith(".ReconcileProject")

    def test_iter_search_index_targets_includes_fingerprint(self) -> None:
        """Discover search targets with their schema fingerprints."""
        targets = list(iter_search_index_targets())

        assert len(targets) == 1
        target = targets[0]
        assert target.manager_class is ReconcileProject
        assert target.manager_path == manager_import_path(ReconcileProject)
        assert target.index_name == "global"
        assert len(target.schema_fingerprint) == 64

    def test_fingerprint_changes_when_index_config_changes(self) -> None:
        """Change schema fingerprints when index configuration changes."""
        original = build_search_schema_fingerprint(
            ReconcileProject,
            ReconcileProject.SearchConfig.indexes[0],
        )
        changed_index = IndexConfig(
            name="global",
            fields=["name", "status"],
            filters=["status"],
        )

        changed = build_search_schema_fingerprint(ReconcileProject, changed_index)

        assert original != changed

    def test_ensure_states_marks_missing_targets_dirty_for_initialization(self) -> None:
        """Create missing state rows as dirty initialization work."""
        result = ensure_search_index_states()

        assert result.created == 1
        state = SearchIndexState.objects.get()
        assert state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_INITIALIZATION
        assert state.dirty_since is not None

    def test_ensure_states_marks_schema_changes_dirty(self) -> None:
        """Mark existing state dirty when the stored fingerprint changes."""
        ensure_search_index_states()
        state = SearchIndexState.objects.get()
        state.schema_fingerprint = "outdated"
        state.dirty_since = None
        state.dirty_reason = ""
        state.save(
            update_fields=[
                "schema_fingerprint",
                "dirty_since",
                "dirty_reason",
                "updated_at",
            ]
        )

        result = ensure_search_index_states()
        state.refresh_from_db()

        assert result.updated == 1
        assert state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED
        assert state.dirty_since is not None
        assert state.schema_fingerprint != "outdated"


class SearchDirtyMarkerTests(TestCase):
    def setUp(self) -> None:
        """Register the searchable manager class for dirty marker tests."""
        self._original_all_classes = list(GeneralManagerMeta.all_classes)
        GeneralManagerMeta.all_classes = [ReconcileProject]
        GeneralmanagerConfig.initialize_general_manager_classes(
            [ReconcileProject], [ReconcileProject]
        )

    def tearDown(self) -> None:
        """Restore the global manager class registry after each test."""
        GeneralManagerMeta.all_classes = self._original_all_classes
        super().tearDown()

    def test_mark_dirty_creates_state_for_all_manager_indexes(self) -> None:
        """Create and dirty state rows for all configured manager indexes."""
        marked = mark_search_indexes_dirty(
            ReconcileProject,
            reason=SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
        )

        assert marked == 1
        state = SearchIndexState.objects.get(
            manager_path=manager_import_path(ReconcileProject),
            index_name="global",
        )
        assert state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED
        assert state.dirty_since is not None

    def test_mark_dirty_preserves_existing_dirty_since(self) -> None:
        """Keep the original dirty timestamp when marking dirty again."""
        mark_search_indexes_dirty(
            ReconcileProject,
            reason=SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
        )
        state = SearchIndexState.objects.get()
        original_dirty_since = state.dirty_since

        mark_search_indexes_dirty(
            ReconcileProject,
            reason=SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
        )
        state.refresh_from_db()

        assert state.dirty_since == original_dirty_since


class SearchReconcileEngineTests(TestCase):
    def setUp(self) -> None:
        """Register the searchable manager class for reconcile engine tests."""
        self._original_all_classes = list(GeneralManagerMeta.all_classes)
        GeneralManagerMeta.all_classes = [ReconcileProject]
        GeneralmanagerConfig.initialize_general_manager_classes(
            [ReconcileProject], [ReconcileProject]
        )

    def tearDown(self) -> None:
        """Restore the global manager class registry after each test."""
        GeneralManagerMeta.all_classes = self._original_all_classes
        super().tearDown()

    def test_reconcile_skips_when_nothing_dirty(self) -> None:
        """Skip backend writes when all search states are clean."""
        ensure_search_index_states()
        state = SearchIndexState.objects.get()
        state.clear_dirty()

        from general_manager.search.reconciliation import reconcile_search_indexes

        with patch("general_manager.search.indexer.SearchIndexer") as indexer:
            result = reconcile_search_indexes()

        assert result.reconciled == 0
        assert result.skipped == 1
        indexer.assert_not_called()

    def test_reconcile_reindexes_dirty_state_and_clears_it(self) -> None:
        """Reindex dirty states and clear their dirty and claim fields."""
        ensure_search_index_states()

        from general_manager.search.reconciliation import reconcile_search_indexes

        with patch("general_manager.search.indexer.SearchIndexer") as indexer:
            indexer.return_value.reindex_manager_index.return_value = 2

            result = reconcile_search_indexes()

        assert result.reconciled == 1
        assert result.documents == 2
        state = SearchIndexState.objects.get()
        assert state.dirty_since is None
        assert state.last_reconciled_at is not None
        assert state.claim_token == ""
        indexer.return_value.reindex_manager_index.assert_called_once_with(
            ReconcileProject,
            "global",
        )

    def test_reconcile_keeps_state_dirty_after_failure(self) -> None:
        """Keep failed states dirty and record the backend error."""
        ensure_search_index_states()

        from general_manager.search.reconciliation import reconcile_search_indexes

        with patch("general_manager.search.indexer.SearchIndexer") as indexer:
            indexer.return_value.reindex_manager_index.side_effect = RuntimeError(
                "backend down"
            )

            result = reconcile_search_indexes()

        assert result.failed == 1
        state = SearchIndexState.objects.get()
        assert state.dirty_since is not None
        assert state.claim_token == ""
        assert "backend down" in state.last_error

    def test_reconcile_records_invalid_manager_path_error(self) -> None:
        """Record a deliberate validation error for non-manager import targets."""
        ensure_search_index_states()
        state = SearchIndexState.objects.get()
        state.manager_path = "math.sqrt"
        state.save(update_fields=["manager_path", "updated_at"])
        GeneralManagerMeta.all_classes = []

        from general_manager.search.reconciliation import reconcile_search_indexes

        with patch("general_manager.search.indexer.SearchIndexer") as indexer:
            result = reconcile_search_indexes()

        assert result.failed == 1
        indexer.return_value.reindex_manager_index.assert_not_called()
        state.refresh_from_db()
        assert state.dirty_since is not None
        assert state.claim_token == ""
        assert "must resolve to a GeneralManager class" in state.last_error

    def test_force_reconcile_marks_clean_state_dirty_and_reindexes(self) -> None:
        """Force clean states back through reconciliation."""
        ensure_search_index_states()
        state = SearchIndexState.objects.get()
        state.clear_dirty()

        from general_manager.search.reconciliation import reconcile_search_indexes

        with patch("general_manager.search.indexer.SearchIndexer") as indexer:
            indexer.return_value.reindex_manager_index.return_value = 1

            result = reconcile_search_indexes(force=True)

        assert result.reconciled == 1

    def test_reconcile_skips_actively_claimed_dirty_state(self) -> None:
        """Skip dirty states claimed by another active worker."""
        ensure_search_index_states()
        state = SearchIndexState.objects.get()
        state.claim_token = uuid.uuid4().hex
        state.claim_expires_at = timezone.now() + timedelta(minutes=5)
        state.save(update_fields=["claim_token", "claim_expires_at", "updated_at"])

        from general_manager.search.reconciliation import reconcile_search_indexes

        with patch("general_manager.search.indexer.SearchIndexer") as indexer:
            result = reconcile_search_indexes()

        assert result.claimed == 0
        assert result.reconciled == 0
        indexer.assert_not_called()

    def test_reconcile_reclaims_expired_dirty_state(self) -> None:
        """Reclaim dirty states whose worker claim has expired."""
        ensure_search_index_states()
        state = SearchIndexState.objects.get()
        state.claim_token = uuid.uuid4().hex
        state.claim_expires_at = timezone.now() - timedelta(seconds=1)
        state.save(update_fields=["claim_token", "claim_expires_at", "updated_at"])

        from general_manager.search.reconciliation import reconcile_search_indexes

        with patch("general_manager.search.indexer.SearchIndexer") as indexer:
            indexer.return_value.reindex_manager_index.return_value = 1

            result = reconcile_search_indexes()

        assert result.claimed == 1
        assert result.reconciled == 1
