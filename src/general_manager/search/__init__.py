"""Search configuration primitives and backend helpers."""

from general_manager.search.backend import (
    SearchBackend,
    SearchBackendError,
    SearchBackendNotConfiguredError,
    SearchDocument,
    SearchHit,
    SearchResult,
)
from general_manager.search.backend_registry import (
    configure_search_backend,
    configure_search_backend_from_settings,
    get_search_backend,
)
from general_manager.search.config import (
    FieldConfig,
    IndexConfig,
    SearchConfigProtocol,
    SearchConfigSpec,
    iter_index_names,
    resolve_search_config,
)
from general_manager.search.indexer import SearchIndexer

__all__ = [
    "FieldConfig",
    "IndexConfig",
    "SearchBackend",
    "SearchBackendError",
    "SearchBackendNotConfiguredError",
    "SearchConfigProtocol",
    "SearchConfigSpec",
    "SearchDocument",
    "SearchHit",
    "SearchIndexer",
    "SearchResult",
    "configure_search_backend",
    "configure_search_backend_from_settings",
    "get_search_backend",
    "iter_index_names",
    "resolve_search_config",
]
