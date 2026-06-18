"""Persistent search reconciliation state."""

from __future__ import annotations

from typing import Any

from django.db import models
from django.utils import timezone

SEARCH_INDEX_DIRTY_REASON_INITIALIZATION = "initialization"
SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED = "schema_changed"
SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED = "data_changed"
SEARCH_INDEX_DIRTY_REASON_FORCED = "forced"

SEARCH_INDEX_DIRTY_REASONS = (
    (SEARCH_INDEX_DIRTY_REASON_INITIALIZATION, "Initialization"),
    (SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED, "Schema changed"),
    (SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED, "Data changed"),
    (SEARCH_INDEX_DIRTY_REASON_FORCED, "Forced"),
)


class SearchIndexState(models.Model):
    """Durable reconciliation state for one manager/index pair."""

    manager_path: Any = models.CharField(max_length=512)
    index_name: Any = models.CharField(max_length=255)
    schema_fingerprint: Any = models.CharField(max_length=64)
    initialized_at: Any = models.DateTimeField(null=True, blank=True)
    last_reconciled_at: Any = models.DateTimeField(null=True, blank=True)
    dirty_since: Any = models.DateTimeField(null=True, blank=True)
    dirty_reason: Any = models.CharField(
        max_length=32,
        choices=SEARCH_INDEX_DIRTY_REASONS,
        blank=True,
        default="",
    )
    last_error: Any = models.TextField(blank=True, default="")
    created_at: Any = models.DateTimeField(auto_now_add=True)
    updated_at: Any = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=("manager_path", "index_name"),
                name="general_manager_search_state_manager_index_uniq",
            ),
        )
        indexes = (
            models.Index(fields=["dirty_since", "index_name"]),
            models.Index(fields=["manager_path", "index_name"]),
            models.Index(fields=["last_reconciled_at"]),
        )

    def mark_dirty(self, reason: str) -> None:
        """Mark this state dirty while preserving the first dirty timestamp."""
        if self.dirty_since is None:
            self.dirty_since = timezone.now()
        self.dirty_reason = reason
        self.save(update_fields=["dirty_since", "dirty_reason", "updated_at"])

    def clear_dirty(self) -> None:
        """Clear dirty/error fields and record a successful reconciliation."""
        now = timezone.now()
        if self.initialized_at is None:
            self.initialized_at = now
        self.last_reconciled_at = now
        self.dirty_since = None
        self.dirty_reason = ""
        self.last_error = ""
        self.save(
            update_fields=[
                "initialized_at",
                "last_reconciled_at",
                "dirty_since",
                "dirty_reason",
                "last_error",
                "updated_at",
            ]
        )
