"""Search index reconciliation services."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from django.db import transaction
from django.db.models import F, Q
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
    """
    Configured manager/index pair that can be reconciled.

    The dataclass is frozen and equality is value-based; ordering is not
    defined. Fields are not runtime-validated. `manager_path` uses
    `manager_import_path()`. Duplicate configured index names can yield multiple
    equal targets, while durable state rows still share the same manager/index
    key.
    """

    manager_class: type[GeneralManager]
    manager_path: str
    index_name: str
    schema_fingerprint: str


@dataclass(frozen=True)
class DirtySearchIndex:
    """Generation-fenced result of marking one configured search index dirty."""

    state_id: int
    manager_path: str
    index_name: str
    generation: int
    acknowledgeable: bool


@dataclass(frozen=True)
class SearchStateEnsureResult:
    """
    Counts produced while ensuring search reconciliation state rows.

    `updated` includes both forced dirtying and schema-fingerprint changes.
    Counts are increments performed by the ensure loop, not necessarily unique
    database rows when duplicate index names are configured. Manual construction
    is not validated against negative or non-sensical counts.
    """

    created: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass(frozen=True)
class SearchReconcileResult:
    """
    Counts produced by one search reconciliation sweep.

    `claimed`, `reconciled`, and `failed` count durable state rows. `documents`
    counts documents reported by successful `reindex_manager_index()` calls.
    `skipped` is populated only when no dirty states were claimed and counts
    currently clean rows; rows left unclaimed by `max_states` are not included.
    Manual construction is not validated against negative or non-sensical counts.
    """

    created: int = 0
    updated: int = 0
    skipped: int = 0
    claimed: int = 0
    reconciled: int = 0
    failed: int = 0
    documents: int = 0


class InvalidSearchReconciliationManagerPathError(TypeError):
    """
    Raised by manager-path resolution when a stored path is not a manager class.

    `reconcile_search_indexes()` catches this per state, records the message in
    `SearchIndexState.last_error`, increments `failed`, and continues.
    """

    def __init__(self, manager_path: str) -> None:
        """Build the manager-path validation error message."""
        super().__init__(f"{manager_path} must resolve to a GeneralManager class.")


def manager_import_path(manager_class: type[GeneralManager]) -> str:
    """
    Return the import path used to identify a searchable manager.

    The format is `<manager_class.__module__>.<manager_class.__name__>`. The
    helper does not validate that the class is importable, concrete, or a
    `GeneralManager` subclass at runtime.
    """
    return f"{manager_class.__module__}.{manager_class.__name__}"


def _callable_path(value: object) -> str | None:
    """Return a stable import-like path for a callable when one is available."""
    if value is None:
        return None
    module = getattr(value, "__module__", "")
    name = getattr(value, "__qualname__", getattr(value, "__name__", repr(value)))
    return f"{module}.{name}" if module else name


def _manager_or_path(value: type[GeneralManager] | str) -> str:
    """Return a stable manager import path while preserving dotted strings."""
    if isinstance(value, str):
        return value
    return manager_import_path(value)


def _index_payload(index_config: IndexConfig) -> dict[str, object]:
    """Serialize index configuration into the schema fingerprint payload."""
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
    """
    Build a stable fingerprint for one manager/index search configuration.

    The fingerprint includes the manager import path, index fields, filters,
    sorts, boosts, min score, type label, update strategy, and import-like paths
    for custom `document_id` and `to_document` callables. It returns a SHA-256
    hex digest. Field order follows `index_config.iter_fields()`, filters and
    sorts keep configured order, duplicate entries remain in the payload, and
    JSON object keys are sorted before hashing. Callable paths use
    `<__module__>.<__qualname__>` when present, then `<__module__>.<__name__>`
    when `__name__` is used, then `repr` with the module prefix when a module is
    available. Defaults are whatever the resolved `SearchConfigSpec` exposes.
    JSON serialization errors from malformed config values propagate.
    """
    config = get_search_config(manager_class)
    payload = {
        "manager_path": manager_import_path(manager_class),
        "index": _index_payload(index_config),
        "document_id": _callable_path(config.document_id if config else None),
        "to_document": _callable_path(config.to_document if config else None),
        "type_label": config.type_label if config else None,
        "update_strategy": config.update_strategy if config else None,
        "invalidation_rules": [
            {
                "source": _manager_or_path(rule.source),
                "resolver": _callable_path(rule.resolve),
                "index": index_config.name,
                "relation": rule.relation,
            }
            for rule in config.invalidation_rules
            if rule.indexes is None or index_config.name in rule.indexes
        ]
        if config
        else [],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def iter_search_index_targets() -> Iterable[SearchIndexTarget]:
    """
    Yield all configured searchable manager/index targets.

    Managers come from the search registry. Managers without search config are
    skipped. Duplicate configured index names yield duplicate targets in
    configuration order. The generator is lazy and propagates errors from
    manager/config discovery.
    """
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
    """
    Ensure durable state rows exist and mark missing or changed targets dirty.

    Creates one `SearchIndexState` per manager path and index name, marks new
    rows dirty for initialization, marks existing rows dirty when `force=True`,
    and marks rows dirty when the schema fingerprint changed. Existing unchanged
    rows are counted as unchanged. Obsolete rows for removed managers or indexes
    are not deleted. Each target is handled in its own transaction with
    `select_for_update()`. Duplicate configured index names address the same
    durable row repeatedly and increment loop counts per processed target.
    Dirty reasons are `initialization`, `forced`, or `schema_changed`; marking a
    row dirty preserves its first `dirty_since` timestamp and overwrites
    `dirty_reason`.
    Database errors propagate.
    """
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
    """
    Mark all configured search indexes for a manager dirty.

    Returns the number of configured index entries marked. Managers without
    search config return `0`. State rows are created when missing as
    initialization work, while stored schema mismatches become schema-change
    work. Duplicate configured index names are processed once per config entry
    against the same durable row, preserving the wrapper's count semantics.
    Repeated processing preserves the first `dirty_since`, advances
    `dirty_generation`, and does not downgrade stronger work to a data-change
    reason. Database errors propagate.
    """
    config = get_search_config(manager_class)
    if config is None:
        return 0
    marked = 0
    for index_config in config.indexes:
        _mark_search_index_config_dirty(manager_class, index_config, reason=reason)
        marked += 1
    return marked


def _mark_search_index_config_dirty(
    manager_class: type[GeneralManager],
    index_config: IndexConfig,
    *,
    reason: str,
) -> DirtySearchIndex:
    """Mark one configured manager/index entry under its per-pair row lock."""
    manager_path = manager_import_path(manager_class)
    target_fingerprint = build_search_schema_fingerprint(
        manager_class,
        index_config,
    )
    with transaction.atomic():
        state, created = SearchIndexState.objects.select_for_update().get_or_create(
            manager_path=manager_path,
            index_name=index_config.name,
            defaults={"schema_fingerprint": target_fingerprint},
        )
        was_clean = state.dirty_since is None
        fingerprint_current = state.schema_fingerprint == target_fingerprint
        if created:
            dirty_reason = SEARCH_INDEX_DIRTY_REASON_INITIALIZATION
        elif not fingerprint_current:
            state.schema_fingerprint = target_fingerprint
            state.save(update_fields=["schema_fingerprint", "updated_at"])
            dirty_reason = SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED
        elif (
            reason == SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED
            and state.dirty_since is not None
            and state.dirty_reason
            in {
                SEARCH_INDEX_DIRTY_REASON_INITIALIZATION,
                SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED,
                SEARCH_INDEX_DIRTY_REASON_FORCED,
            }
        ):
            dirty_reason = state.dirty_reason
        else:
            dirty_reason = reason

        state.mark_dirty(dirty_reason)
        acknowledgeable = (
            not created
            and fingerprint_current
            and was_clean
            and reason == SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED
        )
        return DirtySearchIndex(
            state_id=state.pk,
            manager_path=manager_path,
            index_name=index_config.name,
            generation=state.dirty_generation,
            acknowledgeable=acknowledgeable,
        )


def mark_search_index_dirty(
    manager_class: type[GeneralManager],
    index_name: str,
    *,
    reason: str = SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
) -> DirtySearchIndex | None:
    """Mark one configured manager/index pair dirty and return its fence token."""
    config = get_search_config(manager_class)
    if config is None:
        return None
    for index_config in config.indexes:
        if index_config.name == index_name:
            return _mark_search_index_config_dirty(
                manager_class,
                index_config,
                reason=reason,
            )
    return None


def mark_existing_search_index_dirty(
    manager_path: str,
    index_name: str,
) -> bool:
    """Redirty an existing durable pair when its manager class cannot import.

    This recovery path intentionally never creates state or changes its schema
    fingerprint because those operations require the unavailable manager
    configuration. The row is locked while its current reason is inspected;
    stronger initialization, schema-change, and forced reasons are preserved.
    """
    with transaction.atomic():
        try:
            state = SearchIndexState.objects.select_for_update().get(
                manager_path=manager_path,
                index_name=index_name,
            )
        except SearchIndexState.DoesNotExist:
            return False
        reason = (
            state.dirty_reason
            if state.dirty_reason
            in {
                SEARCH_INDEX_DIRTY_REASON_INITIALIZATION,
                SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED,
                SEARCH_INDEX_DIRTY_REASON_FORCED,
            }
            else SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED
        )
        state.mark_dirty(reason)
    return True


def acknowledge_search_index_dirty(token: DirtySearchIndex) -> bool:
    """Clear an incremental data mark only while its generation remains current."""
    if not token.acknowledgeable:
        return False
    now = timezone.now()
    updated = SearchIndexState.objects.filter(
        pk=token.state_id,
        dirty_generation=token.generation,
        dirty_reason=SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED,
        dirty_since__isnull=False,
        claim_token="",
    ).update(
        dirty_since=None,
        dirty_reason="",
        last_error="",
        updated_at=now,
    )
    return updated == 1


def _resolve_manager_path(manager_path: str) -> type[GeneralManager]:
    """Import and return the manager class for a stored manager path."""
    manager_class = import_string(manager_path)
    if not isinstance(manager_class, type) or not issubclass(
        manager_class, GeneralManager
    ):
        raise InvalidSearchReconciliationManagerPathError(manager_path)
    return manager_class


def _claim_dirty_states(
    *,
    max_states: int | None = None,
    claim_ttl_seconds: int = 300,
) -> list[SearchIndexState]:
    """Atomically claim dirty states that are unclaimed or expired."""
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


def _release_claim(state_id: int, claim_token: str) -> None:
    """Release only the claim still owned by the captured worker token."""
    SearchIndexState.objects.filter(
        pk=state_id,
        claim_token=claim_token,
    ).update(
        claim_token="",
        claimed_at=None,
        claim_expires_at=None,
        updated_at=timezone.now(),
    )


def _release_claim_with_error(
    state_id: int,
    claim_token: str,
    error: str,
) -> None:
    """Record a current worker failure without overwriting newer work."""
    SearchIndexState.objects.filter(
        pk=state_id,
        claim_token=claim_token,
    ).update(
        claim_token="",
        claimed_at=None,
        claim_expires_at=None,
        last_error=error,
        dirty_generation=F("dirty_generation") + 1,
        updated_at=timezone.now(),
    )


def reconcile_search_indexes(
    *,
    force: bool = False,
    max_states: int | None = None,
) -> SearchReconcileResult:
    """
    Reconcile dirty search index states.

    Ensures state rows first, claims dirty states ordered by oldest
    `dirty_since`, optionally limits the claim count with `max_states`, and
    rebuilds each claimed manager/index pair with
    `SearchIndexer.reindex_manager_index()`. When no states are dirty, returns
    the ensure counts plus the number of clean rows as `skipped`.

    `force=True` affects the ensure phase before claiming. `max_states` limits
    only how many dirty rows are claimed in the current sweep; unclaimed dirty
    rows remain dirty and are not counted as skipped. Claims use
    `select_for_update()` plus a claim token/expiry so concurrent reconcilers do
    not process the same unexpired claim; expired claims are eligible like
    unclaimed dirty rows. Ensure counts are included in the returned result on
    both skipped and claimed sweeps. Successful reconciliation clears
    `last_error`, while a new failure overwrites it. Backend writes inside
    `reindex_manager_index()` may have partial effects before that method raises.

    Per-state import, validation, backend, and serialization failures are caught:
    the state claim is released, `last_error` is stored, and `failed` is
    incremented. Database errors while ensuring, claiming, releasing, or clearing
    states propagate.
    """
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
        state_id = state.pk
        claim_token = state.claim_token
        dirty_generation = state.dirty_generation
        try:
            manager_class = _resolve_manager_path(state.manager_path)
            document_count = indexer.reindex_manager_index(
                manager_class,
                state.index_name,
            )
            documents += document_count
            cleared = state.clear_dirty(
                claim_token=claim_token,
                dirty_generation=dirty_generation,
            )
            if cleared:
                reconciled += 1
                logger.info(
                    "search index reconciled",
                    context={
                        "manager": state.manager_path,
                        "index": state.index_name,
                        "documents": document_count,
                    },
                )
            else:
                _release_claim(state_id, claim_token)
        except Exception as exc:
            failed += 1
            _release_claim_with_error(
                state_id,
                claim_token,
                str(exc),
            )
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
