from __future__ import annotations

import uuid

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from general_manager.search.models import (
    SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
    SEARCH_INDEX_DIRTY_REASON_FORCED,
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

    def test_search_index_state_index_names_match_migration(self) -> None:
        """Keep operational index names aligned with the checked-in migration."""
        expected_indexes = {
            ("dirty_since", "index_name"): "general_man_dirty_s_71fc00_idx",
            ("claim_token",): "general_man_claim_t_3aaacc_idx",
            ("claim_expires_at",): "general_man_claim_e_1fa228_idx",
            ("last_reconciled_at",): "general_man_last_re_81038c_idx",
        }

        actual_indexes = {
            tuple(index.fields): index.name for index in SearchIndexState._meta.indexes
        }

        assert actual_indexes == expected_indexes

    def test_mark_dirty_increments_generation_without_clearing_existing_timestamp(
        self,
    ) -> None:
        """Preserve the first dirty timestamp and advance every dirty mark."""
        first_dirty = timezone.now()
        state = SearchIndexState.objects.create(
            manager_path="tests.Project",
            index_name="global",
            schema_fingerprint="abc",
            dirty_since=first_dirty,
            dirty_reason=SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
            dirty_generation=4,
        )

        state.mark_dirty(SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED)
        state.mark_dirty(SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED)
        state.refresh_from_db()

        assert state.dirty_since == first_dirty
        assert state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED
        assert state.dirty_generation == 6

    def test_mark_dirty_atomically_advances_generation_from_stale_instances(
        self,
    ) -> None:
        """Count every mark even when callers loaded the same old generation."""
        SearchIndexState.objects.create(
            manager_path="tests.Project",
            index_name="global",
            schema_fingerprint="abc",
        )
        first_state = SearchIndexState.objects.get()
        stale_state = SearchIndexState.objects.get()

        first_state.mark_dirty(SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED)
        first_dirty = first_state.dirty_since
        stale_state.mark_dirty(SEARCH_INDEX_DIRTY_REASON_FORCED)

        persisted_state = SearchIndexState.objects.get()
        assert persisted_state.dirty_generation == 2
        assert stale_state.dirty_generation == 2
        assert persisted_state.dirty_since == first_dirty
        assert stale_state.dirty_since == first_dirty
        assert persisted_state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_FORCED
        assert stale_state.dirty_reason == SEARCH_INDEX_DIRTY_REASON_FORCED

    def test_clear_dirty_rejects_stale_generation(self) -> None:
        """Keep state untouched when a worker captured an older generation."""
        claim_token = uuid.uuid4().hex
        state = SearchIndexState.objects.create(
            manager_path="tests.Project",
            index_name="global",
            schema_fingerprint="abc",
            dirty_since=timezone.now(),
            dirty_reason=SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
            dirty_generation=2,
            claim_token=claim_token,
            last_error="boom",
        )

        cleared = state.clear_dirty(
            claim_token=claim_token,
            dirty_generation=1,
        )
        state.refresh_from_db()

        assert cleared is False
        assert state.dirty_since is not None
        assert state.claim_token == claim_token
        assert state.last_error == "boom"
        assert state.last_reconciled_at is None

    def test_clear_dirty_rejects_replacement_claim(self) -> None:
        """Keep state untouched when another worker replaced the claim."""
        replacement_claim = uuid.uuid4().hex
        stale_claim = uuid.uuid4().hex
        state = SearchIndexState.objects.create(
            manager_path="tests.Project",
            index_name="global",
            schema_fingerprint="abc",
            dirty_since=timezone.now(),
            dirty_reason=SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
            dirty_generation=2,
            claim_token=replacement_claim,
            last_error="boom",
        )

        cleared = state.clear_dirty(
            claim_token=stale_claim,
            dirty_generation=2,
        )
        state.refresh_from_db()

        assert cleared is False
        assert state.dirty_since is not None
        assert state.claim_token == replacement_claim
        assert state.last_error == "boom"
        assert state.last_reconciled_at is None

    def test_clear_dirty_records_success_for_matching_claim_and_generation(
        self,
    ) -> None:
        """Clear reconciliation fields only for the current claimed work."""
        claim_token = uuid.uuid4().hex
        state = SearchIndexState.objects.create(
            manager_path="tests.Project",
            index_name="global",
            schema_fingerprint="abc",
            dirty_since=timezone.now(),
            dirty_reason=SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
            dirty_generation=2,
            claim_token=claim_token,
            claimed_at=timezone.now(),
            claim_expires_at=timezone.now(),
            last_error="boom",
        )

        cleared = state.clear_dirty(
            claim_token=claim_token,
            dirty_generation=2,
        )
        state.refresh_from_db()

        assert cleared is True
        assert state.dirty_since is None
        assert state.dirty_reason == ""
        assert state.last_error == ""
        assert state.last_reconciled_at is not None
        assert state.initialized_at is not None
        assert state.claim_token == ""
        assert state.claimed_at is None
        assert state.claim_expires_at is None
