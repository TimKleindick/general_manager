"""Search index reconciliation services."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string

from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.search.config import IndexConfig
from general_manager.search.models import (
    SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
    SEARCH_INDEX_DIRTY_REASON_FORCED,
    SEARCH_INDEX_DIRTY_REASON_INITIALIZATION,
    SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED,
    SearchIndexState,
)
from general_manager.search.registry import get_search_config, iter_searchable_managers

logger = get_logger("search.reconciliation")


@dataclass(frozen=True)
class SearchIndexTarget:
    """Configured manager/index pair that can be reconciled."""

    manager_class: type[GeneralManager]
    manager_path: str
    index_name: str
    schema_fingerprint: str


@dataclass(frozen=True)
class SearchStateEnsureResult:
    """Counts produced while ensuring search reconciliation state rows."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass(frozen=True)
class SearchReconcileResult:
    """Counts produced by one search reconciliation sweep."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    claimed: int = 0
    reconciled: int = 0
    failed: int = 0
    documents: int = 0


def manager_import_path(manager_class: type[GeneralManager]) -> str:
    """Return the import path used to identify a searchable manager."""
    return f"{manager_class.__module__}.{manager_class.__name__}"


def _callable_path(value: Any) -> str | None:
    if value is None:
        return None
    module = getattr(value, "__module__", "")
    name = getattr(value, "__qualname__", getattr(value, "__name__", repr(value)))
    return f"{module}.{name}" if module else name


def _index_payload(index_config: IndexConfig) -> dict[str, Any]:
    return {
        "name": index_config.name,
        "fields": [
            {"name": field.name, "boost": field.boost}
            for field in index_config.iter_fields()
        ],
        "filters": list(index_config.filters),
        "sorts": list(index_config.sorts),
        "boost": index_config.boost,
        "min_score": index_config.min_score,
    }


def build_search_schema_fingerprint(
    manager_class: type[GeneralManager],
    index_config: IndexConfig,
) -> str:
    """Build a stable fingerprint for one manager/index search configuration."""
    config = get_search_config(manager_class)
    payload = {
        "manager_path": manager_import_path(manager_class),
        "index": _index_payload(index_config),
        "document_id": _callable_path(config.document_id if config else None),
        "to_document": _callable_path(config.to_document if config else None),
        "type_label": config.type_label if config else None,
        "update_strategy": config.update_strategy if config else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def iter_search_index_targets() -> Iterable[SearchIndexTarget]:
    """Yield all configured searchable manager/index targets."""
    for manager_class in iter_searchable_managers():
        config = get_search_config(manager_class)
        if config is None:
            continue
        manager_path = manager_import_path(manager_class)
        for index_config in config.indexes:
            yield SearchIndexTarget(
                manager_class=manager_class,
                manager_path=manager_path,
                index_name=index_config.name,
                schema_fingerprint=build_search_schema_fingerprint(
                    manager_class,
                    index_config,
                ),
            )


def ensure_search_index_states(*, force: bool = False) -> SearchStateEnsureResult:
    """Ensure durable state rows exist and mark missing/changed targets dirty."""
    created = 0
    updated = 0
    unchanged = 0
    for target in iter_search_index_targets():
        with transaction.atomic():
            state, was_created = (
                SearchIndexState.objects.select_for_update().get_or_create(
                    manager_path=target.manager_path,
                    index_name=target.index_name,
                    defaults={"schema_fingerprint": target.schema_fingerprint},
                )
            )
            if was_created:
                state.mark_dirty(SEARCH_INDEX_DIRTY_REASON_INITIALIZATION)
                created += 1
                continue
            if force:
                state.schema_fingerprint = target.schema_fingerprint
                state.save(update_fields=["schema_fingerprint", "updated_at"])
                state.mark_dirty(SEARCH_INDEX_DIRTY_REASON_FORCED)
                updated += 1
                continue
            if state.schema_fingerprint != target.schema_fingerprint:
                state.schema_fingerprint = target.schema_fingerprint
                state.save(update_fields=["schema_fingerprint", "updated_at"])
                state.mark_dirty(SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED)
                updated += 1
                continue
            unchanged += 1
    return SearchStateEnsureResult(
        created=created, updated=updated, unchanged=unchanged
    )


def mark_search_indexes_dirty(
    manager_class: type[GeneralManager],
    *,
    reason: str = SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
) -> int:
    """Mark all configured search indexes for a manager dirty."""
    marked = 0
    config = get_search_config(manager_class)
    if config is None:
        return 0
    for index_config in config.indexes:
        target_fingerprint = build_search_schema_fingerprint(
            manager_class, index_config
        )
        with transaction.atomic():
            state, _created = (
                SearchIndexState.objects.select_for_update().get_or_create(
                    manager_path=manager_import_path(manager_class),
                    index_name=index_config.name,
                    defaults={"schema_fingerprint": target_fingerprint},
                )
            )
            if state.schema_fingerprint != target_fingerprint:
                state.schema_fingerprint = target_fingerprint
                state.save(update_fields=["schema_fingerprint", "updated_at"])
            state.mark_dirty(reason)
        marked += 1
    return marked


def _resolve_manager_path(manager_path: str) -> type[GeneralManager]:
    return import_string(manager_path)


def _claim_dirty_states(
    *,
    max_states: int | None = None,
    claim_ttl_seconds: int = 300,
) -> list[SearchIndexState]:
    now = timezone.now()
    claim_token = uuid.uuid4().hex
    claim_expires_at = now + timedelta(seconds=claim_ttl_seconds)
    claim_filter = Q(claim_token="") | Q(claim_expires_at__lte=now)

    with transaction.atomic():
        queryset = (
            SearchIndexState.objects.select_for_update()
            .filter(dirty_since__isnull=False)
            .filter(claim_filter)
            .order_by("dirty_since", "id")
        )
        if max_states is not None:
            queryset = queryset[:max_states]
        states = list(queryset)
        for state in states:
            state.claim_token = claim_token
            state.claimed_at = now
            state.claim_expires_at = claim_expires_at
            state.save(
                update_fields=[
                    "claim_token",
                    "claimed_at",
                    "claim_expires_at",
                    "updated_at",
                ]
            )
    return states


def _release_claim_with_error(state: SearchIndexState, error: str) -> None:
    state.claim_token = ""
    state.claimed_at = None
    state.claim_expires_at = None
    state.last_error = error
    state.save(
        update_fields=[
            "claim_token",
            "claimed_at",
            "claim_expires_at",
            "last_error",
            "updated_at",
        ]
    )


def reconcile_search_indexes(
    *,
    force: bool = False,
    max_states: int | None = None,
) -> SearchReconcileResult:
    """Reconcile dirty search index states."""
    ensure_result = ensure_search_index_states(force=force)
    claimed_states = _claim_dirty_states(max_states=max_states)

    if not claimed_states:
        skipped = SearchIndexState.objects.filter(dirty_since__isnull=True).count()
        logger.info(
            "search reconciliation skipped; no dirty indexes",
            context={"skipped": skipped},
        )
        return SearchReconcileResult(
            created=ensure_result.created,
            updated=ensure_result.updated,
            skipped=skipped,
        )

    from general_manager.search.backend_registry import get_search_backend
    from general_manager.search.indexer import SearchIndexer

    indexer = SearchIndexer(get_search_backend())
    reconciled = 0
    failed = 0
    documents = 0
    for state in claimed_states:
        try:
            manager_class = _resolve_manager_path(state.manager_path)
            document_count = indexer.reindex_manager_index(
                manager_class,
                state.index_name,
            )
            documents += document_count
            state.clear_dirty()
            reconciled += 1
            logger.info(
                "search index reconciled",
                context={
                    "manager": state.manager_path,
                    "index": state.index_name,
                    "documents": document_count,
                },
            )
        except Exception as exc:
            failed += 1
            _release_claim_with_error(state, str(exc))
            logger.exception(
                "search index reconciliation failed",
                context={"manager": state.manager_path, "index": state.index_name},
            )

    return SearchReconcileResult(
        created=ensure_result.created,
        updated=ensure_result.updated,
        claimed=len(claimed_states),
        reconciled=reconciled,
        failed=failed,
        documents=documents,
    )
