"""Manage search indexes and reindex managers."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from general_manager.logging import get_logger
from general_manager.search.backend_registry import get_search_backend
from general_manager.search.indexer import SearchIndexer
from general_manager.search.registry import (
    collect_index_settings,
    get_index_names,
    iter_searchable_managers,
)

logger = get_logger("search.command")


class Command(BaseCommand):
    help = "Create or update search indexes and optionally reindex data."

    def add_arguments(self, parser) -> None:  # type: ignore[override]
        """
        Add CLI arguments to control index creation/update and optional reindexing.
        
        Parameters:
            parser: Argument parser to which the following options are added:
                --index: repeatable; specify one or more index names to create or update.
                --reindex: store-true flag that triggers reindexing of configured managers.
                --manager: repeatable; specify one or more manager class names to reindex.
        """
        parser.add_argument(
            "--index",
            action="append",
            dest="indexes",
            help="Index name to create/update. Repeatable.",
        )
        parser.add_argument(
            "--reindex",
            action="store_true",
            dest="reindex",
            help="Reindex configured managers.",
        )
        parser.add_argument(
            "--manager",
            action="append",
            dest="managers",
            help="Manager class name to reindex. Repeatable.",
        )

    def handle(self, *_: Any, **options: Any) -> None:
        """
        Create or update search indexes and optionally reindex configured managers.
        
        Accepts command-line options via `options`:
        - `indexes`: iterable of index names to create or update; if omitted, all known indexes are targeted. Unknown names are reported to stderr and ignored; if none remain, the command exits early.
        - `reindex`: truthy value to trigger reindexing of searchable managers after ensuring indexes.
        - `managers`: iterable of manager class names to restrict which managers are reindexed when `reindex` is set.
        
        Side effects:
        - Ensures each target index exists and has its searchable, filterable, sortable fields and field boosts configured on the search backend.
        - When `reindex` is true, reindexes each searchable manager (filtered by `managers` when provided) and logs completion per manager.
        """
        index_names = options.get("indexes")
        reindex = bool(options.get("reindex"))
        manager_filters = set(options.get("managers") or [])

        backend = get_search_backend()
        indexer = SearchIndexer(backend)

        if index_names:
            target_indexes = set(index_names)
            available = set(get_index_names())
            unknowns = target_indexes - available
            if unknowns:
                self.stderr.write(
                    f"Unknown index names ignored: {', '.join(sorted(unknowns))}"
                )
                target_indexes = target_indexes & available
            if not target_indexes:
                self.stdout.write("No valid index names provided; nothing to do.")
                return
        else:
            target_indexes = get_index_names()

        for index_name in sorted(target_indexes):
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
            logger.info(
                "search index ensured",
                context={"index": index_name},
            )

        if reindex:
            for manager_class in iter_searchable_managers():
                if manager_filters and manager_class.__name__ not in manager_filters:
                    continue
                indexer.reindex_manager(manager_class)
                logger.info(
                    "search reindex complete",
                    context={"manager": manager_class.__name__},
                )