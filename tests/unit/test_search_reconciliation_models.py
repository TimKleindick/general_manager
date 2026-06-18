from __future__ import annotations

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from general_manager.search.models import (
    SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
    SearchIndexState,
)


class SearchIndexStateModelTests(TestCase):
    def test_search_index_state_is_unique_per_manager_and_index(self) -> None:
        """Enforce one state row per manager and index pair."""
        SearchIndexState.objects.create(
            manager_path="tests.Project",
            index_name="global",
            schema_fingerprint="abc",
        )

        with self.assertRaises(IntegrityError):
            SearchIndexState.objects.create(
                manager_path="tests.Project",
                index_name="global",
                schema_fingerprint="def",
            )

    def test_search_index_state_does_not_define_duplicate_unique_lookup_index(
        self,
    ) -> None:
        """Avoid a redundant explicit index for the unique lookup fields."""
        duplicate_indexes = [
            index
            for index in SearchIndexState._meta.indexes
            if list(index.fields) == ["manager_path", "index_name"]
        ]

        assert duplicate_indexes == []

    def test_mark_dirty_records_reason_without_clearing_existing_timestamp(
        self,
    ) -> None:
        """Preserve the first dirty timestamp when marking dirty again."""
        first_dirty = timezone.now()
        state = SearchIndexState.objects.create(
            manager_path="tests.Project",
            index_name="global",
            schema_fingerprint="abc",
            dirty_since=first_dirty,
            dirty_reason=SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
        )

        state.mark_dirty(SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED)
        state.refresh_from_db()

        assert state.dirty_since == first_dirty
        assert state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED

    def test_clear_dirty_records_success(self) -> None:
        """Clear dirty/error fields and record reconciliation success."""
        state = SearchIndexState.objects.create(
            manager_path="tests.Project",
            index_name="global",
            schema_fingerprint="abc",
            dirty_since=timezone.now(),
            dirty_reason=SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
            last_error="boom",
        )

        state.clear_dirty()
        state.refresh_from_db()

        assert state.dirty_since is None
        assert state.dirty_reason == ""
        assert state.last_error == ""
        assert state.last_reconciled_at is not None
