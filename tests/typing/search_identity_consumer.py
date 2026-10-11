"""Strictly typed public consumers of native search identity provenance."""

from collections.abc import Mapping
from uuid import UUID

from general_manager.manager.general_manager import GeneralManager
from general_manager.search.backend import SearchBackend, SearchDocument, SearchResult
from general_manager.search.config import IndexConfig
from general_manager.search.identity import (
    SearchIdentityFilterGroup,
    build_search_filters,
)


def search_manager(
    backend: SearchBackend, manager: type[GeneralManager]
) -> SearchResult:
    filters = build_search_filters(
        manager, "global", {"customer_id__in": ["9007199254740995"]}
    )
    return backend.search("global", "", filters=filters)


def search_manual(backend: SearchBackend, key: UUID) -> SearchResult:
    fields: Mapping[str, type] = {"customer_alias": UUID}
    config = IndexConfig(
        name="owned",
        fields=["customer_alias"],
        filters=["customer_alias"],
        identity_fields=fields,
    )
    backend.ensure_index(
        config.name,
        {
            "filterable_fields": ["type", "customer_alias"],
            "identity_fields": {"Record": fields},
        },
    )
    backend.upsert(
        config.name,
        [
            SearchDocument(
                id="owned-doc",
                type="Record",
                identification={"id": key},
                index=config.name,
                data={"customer_alias": key},
                field_boosts={},
                identity_fields=fields,
            )
        ],
    )
    return backend.search(
        config.name,
        "",
        filters=SearchIdentityFilterGroup(
            {"customer_alias__in": [str(key)]},
            identity_fields=fields,
            type_label="Record",
        ),
    )
