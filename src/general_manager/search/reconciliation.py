"""Search index reconciliation services."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from django.db import transaction

from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.search.config import IndexConfig
from general_manager.search.models import (
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
