"""Pure search document serialization and backend indexing operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.search.backend import SearchBackend, SearchDocument
from general_manager.search.backend_registry import get_search_backend
from general_manager.search.config import SearchConfigSpec
from general_manager.search.registry import (
    collect_index_settings,
    get_index_config,
    get_search_config,
    get_type_label,
)
from general_manager.search.utils import (
    build_document_id,
    extract_value,
    normalize_identification,
)


class MissingIndexConfigurationError(ValueError):
    """Raised when a search-enabled manager lacks one requested index config."""

    def __init__(self, manager_name: str, index_name: str) -> None:
        """
        Initialize the exception for a manager missing configuration for a given index.

        Parameters:
            manager_name (str): Name of the manager missing the index configuration.
            index_name (str): Name of the index that is not configured for the manager.
        """
        super().__init__(
            f"Manager {manager_name} not configured for index '{index_name}'."
        )


class MissingBatchManagerError(LookupError):
    """Raised when an ORM identity batch cannot load every requested owner."""

    def __init__(
        self,
        manager_name: str,
        identification: Mapping[str, object],
    ) -> None:
        """Build a missing-owner error with the exact requested identity."""
        super().__init__(
            f"Manager {manager_name} not found for identification "
            f"{dict(identification)!r}."
        )


@dataclass(frozen=True)
class IndexPayload:
    """Resolved data used to build backend documents."""

    index_name: str
    documents: Sequence[SearchDocument]


@dataclass(frozen=True)
class SearchDeleteTarget:
    """Immutable search deletion metadata captured while a manager is readable."""

    manager_class: type[GeneralManager]
    manager_path: str
    index_name: str
    document_id: str


def capture_delete_targets(
    instance: GeneralManager,
) -> tuple[SearchDeleteTarget, ...]:
    """Capture all configured document IDs before an instance is deleted."""
    manager_class = instance.__class__
    config = get_search_config(manager_class)
    if config is None:
        return ()
    if config.document_id is not None:
        document_id = config.document_id(instance)
    else:
        document_id = build_document_id(
            get_type_label(manager_class), instance.identification
        )
    manager_path = f"{manager_class.__module__}.{manager_class.__name__}"
    return tuple(
        SearchDeleteTarget(
            manager_class=manager_class,
            manager_path=manager_path,
            index_name=index_config.name,
            document_id=document_id,
        )
        for index_config in config.indexes
    )


def _serialize_document(
    instance: GeneralManager,
    *,
    index_name: str,
    config: SearchConfigSpec,
) -> SearchDocument:
    """
    Serialize a GeneralManager instance into a SearchDocument for the given index.

    Parameters:
        instance: The manager instance to serialize.
        index_name: Target index name for the resulting document.
        config: SearchConfigSpec that may supply a custom document id and/or a provided document mapping.

    Returns:
        SearchDocument: Document including id, type label, identification, index, data, field boosts, and index boost.
    """
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

    data: dict[str, object] = {}
    provided_data: Mapping[str, object] = {}
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
    """
    Collect IndexPayloads for every search index configured for the given manager instance.

    Each returned IndexPayload contains the index name and a single serialized SearchDocument for that index.

    Parameters:
        instance (GeneralManager): Manager instance to serialize into search documents.

    Returns:
        Sequence[IndexPayload]: A list of IndexPayload objects, one per configured index; returns an empty list if the manager has no search configuration.
    """
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
    """
    Ensure the search index exists in the backend with the appropriate settings.

    Collects index settings for the given index name and instructs the backend to create or update the index's searchable fields, filterable fields, sortable fields, and field boosts.

    Parameters:
        index_name (str): Name of the index to ensure exists and be configured.
    """
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
        """
        Initialize a SearchIndexer with a search backend.

        If `backend` is `None`, the process-default backend is resolved through
        `get_search_backend()`. Backend objects must implement the documented
        `SearchBackend` protocol. Backend selection follows the process-local
        backend registry semantics from `get_search_backend()`; no additional
        cache or thread-safety behavior is added by the indexer. Backend
        configuration/import/constructor errors from registry resolution
        propagate.
        """
        self.backend = get_search_backend() if backend is None else backend

    def index_instance(self, instance: GeneralManager) -> None:
        """
        Index a GeneralManager instance across all configured search indexes.

        Ensures each target index exists in the backend and upserts one document
        per `IndexConfig` configured on the instance manager's `SearchConfig`.
        Search configuration is discovered from the instance class through
        GeneralManager's search registry. Does nothing if the manager class has
        no search configuration. The default document id uses the same type label
        and `instance.identification` path as `delete_instance()`.

        Index configs are processed in configured order. The method is not
        atomic across indexes; earlier backend writes remain if a later index
        fails. Duplicate `IndexConfig.name` entries are not deduplicated.
        Duplicate names therefore repeat ensure/upsert work and use the first
        matching index config returned by the search registry when serializing
        that name.
        Passing a non-GeneralManager object is not runtime-validated and fails
        through normal attribute access.

        Parameters:
            instance (GeneralManager): The manager instance to index.

        Raises:
            MissingIndexConfigurationError: If a configured `IndexConfig.name` cannot be resolved again for the instance manager.
            Exception: Backend `ensure_index` and `upsert`, custom document id, custom document mapping, and field extraction errors propagate.
        """
        payloads = _collect_documents_for_instance(instance)
        for payload in payloads:
            _ensure_index(self.backend, payload.index_name)
            self.backend.upsert(payload.index_name, payload.documents)

    def index_instance_index(
        self,
        instance: GeneralManager,
        index_name: str,
    ) -> None:
        """Index one instance into one named configured search index."""
        config = get_search_config(instance.__class__)
        if config is None:
            return
        if get_index_config(instance.__class__, index_name) is None:
            raise MissingIndexConfigurationError(
                instance.__class__.__name__, index_name
            )
        document = _serialize_document(
            instance,
            index_name=index_name,
            config=config,
        )
        _ensure_index(self.backend, index_name)
        self.backend.upsert(index_name, [document])

    def index_manager_index_batch(
        self,
        manager_class: type[GeneralManager],
        index_name: str,
        identifications: Sequence[Mapping[str, object]],
    ) -> int:
        """Index a bounded identity batch for one exact manager/index pair.

        Identifications are copied and canonically deduplicated in first-seen
        order. Standard single-id ORM managers are loaded with one public
        ``filter(pk__in=...)`` call; all other manager shapes are reconstructed
        individually. The backend write occurs only after every requested owner
        has been resolved and serialized.
        """
        config = get_search_config(manager_class)
        index_config = get_index_config(manager_class, index_name)
        if config is None or index_config is None:
            raise MissingIndexConfigurationError(manager_class.__name__, index_name)

        unique_identifications: list[dict[str, object]] = []
        seen: set[str] = set()
        for identification in identifications:
            copied = dict(identification)
            normalized = normalize_identification(copied)
            if normalized in seen:
                continue
            seen.add(normalized)
            unique_identifications.append(copied)

        if not unique_identifications:
            return 0

        interface = manager_class.Interface
        input_fields = getattr(interface, "input_fields", None)
        is_standard_orm_id = (
            isinstance(interface, type)
            and issubclass(interface, OrmInterfaceBase)
            and isinstance(input_fields, Mapping)
            and tuple(input_fields) == ("id",)
        )
        if is_standard_orm_id:
            requested_ids = [item["id"] for item in unique_identifications]
            loaded_by_identification = {
                normalize_identification(instance.identification): instance
                for instance in manager_class.filter(pk__in=requested_ids)
            }
            instances: list[GeneralManager] = []
            for identification in unique_identifications:
                instance = loaded_by_identification.get(
                    normalize_identification(identification)
                )
                if instance is None:
                    raise MissingBatchManagerError(
                        manager_class.__name__, identification
                    )
                instances.append(instance)
        else:
            instances = [
                manager_class(**identification)
                for identification in unique_identifications
            ]

        documents = [
            _serialize_document(
                instance,
                index_name=index_config.name,
                config=config,
            )
            for instance in instances
        ]
        _ensure_index(self.backend, index_config.name)
        self.backend.upsert(index_config.name, documents)
        return len(documents)

    def delete_instance(self, instance: GeneralManager) -> None:
        """
        Delete an instance's search document from all configured indexes.

        Determines the document id using the manager's configured `document_id`
        callable if present; otherwise builds the same default id used by
        `index_instance()` from the manager type label and the instance's
        `identification`. For each index configured for the manager, ensures the
        index exists in the backend and asks the backend to delete that one
        document id. Does nothing if the manager class has no search
        configuration.

        Index configs are processed in configured order. The method is not
        atomic across indexes; earlier backend deletes remain if a later index
        fails. Missing backend documents are delegated to backend delete
        semantics. Passing a non-GeneralManager object is not runtime-validated
        and fails through normal attribute access.

        Parameters:
            instance (GeneralManager): The manager instance whose document should be removed from the search indexes.

        Raises:
            Exception: Backend `ensure_index` and `delete`, custom document id, and document id construction errors propagate.
        """
        self.delete_documents(capture_delete_targets(instance))

    def delete_documents(self, targets: Sequence[SearchDeleteTarget]) -> None:
        """Delete captured immutable document IDs, grouping work by index."""
        ids_by_index: dict[str, list[str]] = {}
        for target in targets:
            ids_by_index.setdefault(target.index_name, []).append(target.document_id)
        for index_name, document_ids in ids_by_index.items():
            _ensure_index(self.backend, index_name)
            self.backend.delete(index_name, document_ids)

    def reindex_manager(self, manager_class: type[GeneralManager]) -> None:
        """
        Rebuilds all search indexes for a given manager class by collecting every instance's documents and upserting them to the backend.

        Ensures each configured index exists, iterates `manager_class.all()`,
        serializes one document per instance and `IndexConfig`, groups documents
        by index name, and calls backend `upsert` once per index that has current
        documents. If the manager class has no search configuration, the
        function returns without action.

        This method does not delete stale backend documents. Use
        `reindex_manager_index()` when stale document cleanup is required for a
        single manager/index pair. Indexes are ensured even when
        `manager_class.all()` returns no instances. The method is not atomic
        across indexes; earlier ensures or upserts remain if a later index fails.
        Documents belonging to other manager type labels are preserved because
        no delete operation is issued. Upsert calls follow the first occurrence
        order of configured index names. Duplicate index names collapse into one
        backend upsert call for that name, but serialization still produces one
        document per duplicate config and instance.

        Parameters:
            manager_class (type[GeneralManager]): The manager class whose instances will be reindexed.

        Raises:
            MissingIndexConfigurationError: If a configured index name is missing while serializing an instance.
            Exception: Manager iteration, backend `ensure_index` and `upsert`, custom document id, custom document mapping, and field extraction errors propagate.
        """
        config = get_search_config(manager_class)
        if config is None:
            return
        for index_config in config.indexes:
            _ensure_index(self.backend, index_config.name)

        documents_by_index: dict[str, list[SearchDocument]] = {
            index.name: [] for index in config.indexes
        }
        for instance in manager_class.all():
            for payload in _collect_documents_for_instance(instance):
                documents_by_index[payload.index_name].extend(payload.documents)

        for index_name, documents in documents_by_index.items():
            if documents:
                self.backend.upsert(index_name, documents)

    def reindex_manager_index(
        self,
        manager_class: type[GeneralManager],
        index_name: str,
    ) -> int:
        """
        Rebuild one manager's documents for one configured search index.

        Returns `0` without action when the manager class has no search
        configuration. Otherwise, ensures the target index, serializes one
        document for every instance returned by `manager_class.all()`, upserts
        current documents when present, lists existing backend document ids using
        `backend.list_document_ids(index_name, types=[get_type_label(manager_class)])`,
        deletes stale ids for that type after successful upsert, and returns the
        number of current documents serialized. The backend's type filter and
        GeneralManager type-label/document-id convention define the id namespace
        that protects other manager classes.

        The method is not atomic. If ensure, serialization, or upsert fails, the
        stale-delete phase is not reached. If stale deletion fails after an
        upsert, the new documents remain written. If duplicate index configs use
        the requested `index_name`, the first matching config is used, one
        document is serialized per manager instance, and the return value counts
        those serialized documents.

        Raises:
            MissingIndexConfigurationError: If `manager_class` has search configuration but `index_name` is not configured for that manager.
            Exception: Manager iteration, backend `ensure_index`, `upsert`, `list_document_ids`, and `delete`, custom document id, custom document mapping, and field extraction errors propagate.
        """
        config = get_search_config(manager_class)
        if config is None:
            return 0
        index_config = get_index_config(manager_class, index_name)
        if index_config is None:
            raise MissingIndexConfigurationError(manager_class.__name__, index_name)

        _ensure_index(self.backend, index_config.name)
        documents: list[SearchDocument] = []
        for instance in manager_class.all():
            document = _serialize_document(
                instance,
                index_name=index_config.name,
                config=config,
            )
            documents.append(document)

        current_ids = {document.id for document in documents}
        if documents:
            self.backend.upsert(index_config.name, documents)
        existing_ids = self.backend.list_document_ids(
            index_config.name,
            types=[get_type_label(manager_class)],
        )
        stale_ids = sorted(existing_ids - current_ids)
        if stale_ids:
            self.backend.delete(index_config.name, stale_ids)
        return len(documents)
