from __future__ import annotations

"""Type-only imports for public API re-exports."""

__all__ = [
    "DevSearchBackend",
    "FieldConfig",
    "IndexConfig",
    "MeilisearchBackend",
    "OpenSearchBackend",
    "SearchBackend",
    "SearchBackendClientMissingError",
    "SearchBackendError",
    "SearchBackendNotConfiguredError",
    "SearchBackendNotImplementedError",
    "SearchConfigProtocol",
    "SearchConfigSpec",
    "SearchDocument",
    "SearchHit",
    "SearchIndexer",
    "SearchResult",
    "TypesenseBackend",
    "configure_search_backend",
    "configure_search_backend_from_settings",
    "get_search_backend",
    "iter_index_names",
    "resolve_search_config",
]

from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.config import FieldConfig
from general_manager.search.config import IndexConfig
from general_manager.search.backends.meilisearch import MeilisearchBackend
from general_manager.search.backends.opensearch import OpenSearchBackend
from general_manager.search.backend import SearchBackend
from general_manager.search.backend import SearchBackendClientMissingError
from general_manager.search.backend import SearchBackendError
from general_manager.search.backend import SearchBackendNotConfiguredError
from general_manager.search.backend import SearchBackendNotImplementedError
from general_manager.search.config import SearchConfigProtocol
from general_manager.search.config import SearchConfigSpec
from general_manager.search.backend import SearchDocument
from general_manager.search.backend import SearchHit
from general_manager.search.indexer import SearchIndexer
from general_manager.search.backend import SearchResult
from general_manager.search.backends.typesense import TypesenseBackend
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.backend_registry import (
    configure_search_backend_from_settings,
)
from general_manager.search.backend_registry import get_search_backend
from general_manager.search.config import iter_index_names
from general_manager.search.config import resolve_search_config
