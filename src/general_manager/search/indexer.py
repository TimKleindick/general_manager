"""Search indexer and signal integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, cast

from django.dispatch import receiver

from general_manager.cache.signals import post_data_change, pre_data_change
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.search.backend import (
    SearchBackend,
    SearchBackendError,
    SearchDocument,
)
from general_manager.search.backend_registry import get_search_backend
from general_manager.search.async_tasks import dispatch_index_update
from general_manager.search.config import SearchConfigSpec
from general_manager.search.registry import (
    collect_index_settings,
    get_index_config,
    get_search_config,
    get_type_label,
)
from general_manager.search.utils import build_document_id, extract_value

logger = get_logger("search.indexer")


class MissingIndexConfigurationError(ValueError):
    """Raised when a manager is not configured for an index."""

    def __init__(self, manager_name: str, index_name: str) -> None:
        super().__init__(
            f"Manager {manager_name} not configured for index '{index_name}'."
        )


@dataclass(frozen=True)
class IndexPayload:
    """Resolved data used to build backend documents."""

    index_name: str
    documents: Sequence[SearchDocument]


def _serialize_document(
    instance: GeneralManager,
    *,
    index_name: str,
    config: SearchConfigSpec,
) -> SearchDocument:
    manager_class = instance.__class__
    index_config = get_index_config(manager_class, index_name)
    if index_config is None:
        raise MissingIndexConfigurationError(manager_class.__name__, index_name)

    identification = instance.identification
    type_label = get_type_label(manager_class)
    if config.document_id is not None:
        doc_id = config.document_id(instance)
    else:
        doc_id = build_document_id(type_label, identification)

    data: dict[str, Any] = {}
    provided_data: Mapping[str, Any] = {}
    if config.to_document is not None:
        provided_data = dict(config.to_document(instance))
    for field_config in index_config.iter_fields():
        data[field_config.name] = provided_data.get(
            field_config.name,
            extract_value(instance, field_config.name),
        )
    for filter_field in index_config.filters:
        data.setdefault(
            filter_field,
            provided_data.get(filter_field, extract_value(instance, filter_field)),
        )

    return SearchDocument(
        id=doc_id,
        type=type_label,
        identification=identification,
        index=index_name,
        data=data,
        field_boosts=index_config.field_boosts(),
        index_boost=index_config.boost,
    )


def _collect_documents_for_instance(instance: GeneralManager) -> Sequence[IndexPayload]:
    config = get_search_config(instance.__class__)
    if config is None:
        return []
    payloads: list[IndexPayload] = []
    for index_config in config.indexes:
        document = _serialize_document(
            instance,
            index_name=index_config.name,
            config=config,
        )
        payloads.append(IndexPayload(index_config.name, [document]))
    return payloads


def _ensure_index(backend: SearchBackend, index_name: str) -> None:
    settings_payload = collect_index_settings(index_name)
    backend.ensure_index(
        index_name,
        {
            "searchable_fields": settings_payload.searchable_fields,
            "filterable_fields": settings_payload.filterable_fields,
            "sortable_fields": settings_payload.sortable_fields,
            "field_boosts": settings_payload.field_boosts,
        },
    )


class SearchIndexer:
    """Indexer that writes manager instances to a search backend."""

    def __init__(self, backend: SearchBackend | None = None) -> None:
        self.backend = backend or get_search_backend()

    def index_instance(self, instance: GeneralManager) -> None:
        payloads = _collect_documents_for_instance(instance)
        for payload in payloads:
            _ensure_index(self.backend, payload.index_name)
            self.backend.upsert(payload.index_name, payload.documents)

    def delete_instance(self, instance: GeneralManager) -> None:
        config = get_search_config(instance.__class__)
        if config is None:
            return
        type_label = get_type_label(instance.__class__)
        if config.document_id is not None:
            doc_id = config.document_id(instance)
        else:
            doc_id = build_document_id(type_label, instance.identification)
        for index_config in config.indexes:
            _ensure_index(self.backend, index_config.name)
            self.backend.delete(index_config.name, [doc_id])

    def reindex_manager(self, manager_class: type[GeneralManager]) -> None:
        config = get_search_config(manager_class)
        if config is None:
            return
        for index_config in config.indexes:
            _ensure_index(self.backend, index_config.name)

        documents_by_index: dict[str, list[SearchDocument]] = {
            index.name: [] for index in config.indexes
        }
        for instance in manager_class.all():
            manager_instance = cast(GeneralManager, instance)
            for payload in _collect_documents_for_instance(manager_instance):
                documents_by_index[payload.index_name].extend(payload.documents)

        for index_name, documents in documents_by_index.items():
            if documents:
                self.backend.upsert(index_name, documents)


@receiver(post_data_change)
def _handle_search_post_change(
    sender: type[GeneralManager] | GeneralManager,
    instance: GeneralManager | None,
    action: str | None = None,
    **_: Any,
) -> None:
    if not instance or action not in {"create", "update"}:
        return
    manager_path = f"{instance.__class__.__module__}.{instance.__class__.__name__}"
    try:
        dispatch_index_update(
            action="index",
            manager_path=manager_path,
            identification=instance.identification,
            instance=instance,
        )
    except (SearchBackendError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning(
            "search indexing failed",
            context={"manager": instance.__class__.__name__, "action": action},
            exc_info=exc,
        )


@receiver(pre_data_change)
def _handle_search_pre_delete(
    sender: type[GeneralManager] | GeneralManager,
    instance: GeneralManager | None,
    action: str | None = None,
    **_: Any,
) -> None:
    if instance is None or action != "delete":
        return
    manager_path = f"{instance.__class__.__module__}.{instance.__class__.__name__}"
    try:
        dispatch_index_update(
            action="delete",
            manager_path=manager_path,
            identification=instance.identification,
            instance=instance,
        )
    except (SearchBackendError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning(
            "search delete failed",
            context={"manager": instance.__class__.__name__, "action": action},
            exc_info=exc,
        )
