"""Deterministic ranking tests for planned-chat manager resolution."""

from __future__ import annotations

import pytest

from general_manager.chat.planned.catalog import load_manager_catalog
from general_manager.chat.planned.resolver import ManagerResolver


@pytest.fixture
def schema_index() -> dict[str, dict[str, object]]:
    return {
        "ArchiveManager": {
            "description": "Shared business term archive.",
            "fields": ["shared"],
            "filters": [],
            "relations": [],
        },
        "MaterialManager": {
            "description": "Raw materials used in manufacturing.",
            "fields": ["composition"],
            "filters": ["density_gt"],
            "relations": [],
        },
        "PartManager": {
            "description": "Designed components held in inventory.",
            "fields": ["component_code", "shared"],
            "filters": ["component_code"],
            "relations": [{"name": "material", "target": "MaterialManager"}],
        },
        "ProjectManager": {
            "description": "Shared business term project.",
            "fields": ["shared"],
            "filters": [],
            "relations": [],
        },
        "SupplierManager": {
            "description": "Shared business term supplier.",
            "fields": ["shared"],
            "filters": [],
            "relations": [],
        },
        "WarehouseManager": {
            "description": "Shared business term warehouse.",
            "fields": ["shared"],
            "filters": [],
            "relations": [],
        },
    }


def _entry(
    *, domain: str = "operations", aliases: list[str] | None = None, use_when: str = ""
) -> dict[str, object]:
    return {
        "domain": domain,
        "aliases": aliases or [],
        "use_when": use_when or "The request concerns operational records.",
        "distinguish_from": [],
    }


@pytest.fixture
def resolver(schema_index: dict[str, dict[str, object]]) -> ManagerResolver:
    catalog = load_manager_catalog(
        {
            "ArchiveManager": _entry(aliases=["shared business term"]),
            "MaterialManager": _entry(aliases=["item"], domain="materials"),
            "PartManager": _entry(
                aliases=["component", "item"],
                domain="manufacturing components",
                use_when="Designed component requests.",
            ),
            "ProjectManager": _entry(aliases=["shared business term"]),
            "SupplierManager": _entry(aliases=["shared business term"]),
            "WarehouseManager": _entry(aliases=["shared business term"]),
        },
        schema_index,
    )
    paths = {
        ("ArchiveManager", "PartManager"): ["part"],
        ("ArchiveManager", "ProjectManager"): ["project", "part"],
    }

    def path_finder(source: str, destination: str) -> list[str] | None:
        return paths.get((source, destination))

    return ManagerResolver(schema_index, catalog, path_finder=path_finder)


def test_resolver_prefers_unique_exact_alias_before_schema_overlap(
    resolver: ManagerResolver,
) -> None:
    candidates = resolver.resolve("component")

    assert candidates[0].manager == "PartManager"
    assert candidates[0].exact is True


def test_resolver_caps_candidates_and_explanations(resolver: ManagerResolver) -> None:
    candidates = resolver.resolve("shared business term")

    assert len(candidates) <= 5
    assert all(len(candidate.explanations) <= 3 for candidate in candidates)


def test_resolver_marks_exact_manager_name_and_normalizes_query(
    resolver: ManagerResolver,
) -> None:
    candidates = resolver.resolve("  PART-manager ")

    assert candidates[0].manager == "PartManager"
    assert candidates[0].exact is True


def test_resolver_keeps_duplicate_normalized_manager_names_ambiguous() -> None:
    schema_index = {
        "PartManager": {
            "description": "",
            "fields": [],
            "filters": [],
            "relations": [],
        },
        "part-manager": {
            "description": "",
            "fields": [],
            "filters": [],
            "relations": [],
        },
    }
    catalog = load_manager_catalog(
        {manager: _entry() for manager in schema_index}, schema_index
    )

    candidates = ManagerResolver(schema_index, catalog).resolve("part manager")

    assert all(candidate.exact is False for candidate in candidates)


def test_resolver_keeps_duplicate_aliases_ambiguous(resolver: ManagerResolver) -> None:
    candidates = resolver.resolve("item")

    assert [candidate.manager for candidate in candidates[:2]] == [
        "MaterialManager",
        "PartManager",
    ]
    assert all(candidate.exact is False for candidate in candidates[:2])


def test_resolver_ranks_catalog_sources_before_schema_sources(
    resolver: ManagerResolver,
) -> None:
    candidates = resolver.resolve("manufacturing components")

    assert candidates[0].manager == "PartManager"
    assert "catalog domain" in candidates[0].explanations


def test_resolver_uses_schema_source_counts_and_alphabetical_ties(
    resolver: ManagerResolver,
) -> None:
    candidates = resolver.resolve("component code")

    assert candidates[0].manager == "PartManager"
    tied = resolver.resolve("shared")
    assert [candidate.manager for candidate in tied] == [
        "ArchiveManager",
        "ProjectManager",
        "SupplierManager",
        "WarehouseManager",
        "PartManager",
    ]


def test_resolver_uses_shortest_anchor_path_after_match_counts(
    resolver: ManagerResolver,
) -> None:
    candidates = resolver.resolve("shared business term", anchors=("ArchiveManager",))

    assert candidates[0].manager == "ArchiveManager"
    assert candidates[1].manager == "ProjectManager"


def test_resolver_excludes_unmatched_and_hidden_managers(
    resolver: ManagerResolver,
) -> None:
    assert resolver.resolve("unmatched phrase") == ()
    assert all(
        candidate.manager != "HiddenManager"
        for candidate in resolver.resolve("shared business term")
    )


def test_resolver_cache_separates_schema_catalog_and_anchors(
    resolver: ManagerResolver,
    schema_index: dict[str, dict[str, object]],
) -> None:
    first = resolver.resolve("catalog-only")
    assert first == ()

    schema_index["PartManager"]["description"] = "Catalog-only schema term."
    after_schema_change = resolver.resolve("catalog-only")
    assert after_schema_change[0].manager == "PartManager"

    catalog = load_manager_catalog(
        {
            **{manager: _entry(aliases=[]) for manager in schema_index},
            "MaterialManager": _entry(aliases=["catalog-only"]),
        },
        schema_index,
    )
    resolver.catalog = catalog
    after_catalog_change = resolver.resolve("catalog-only")
    assert after_catalog_change[0].manager == "MaterialManager"

    resolver.resolve("shared business term")
    cache_size_without_anchor = len(resolver.cache)
    resolver.resolve("shared business term", anchors=("ArchiveManager",))
    assert len(resolver.cache) == cache_size_without_anchor + 1


def test_resolver_cache_is_instance_owned(
    schema_index: dict[str, dict[str, object]],
) -> None:
    catalog = load_manager_catalog(
        {"PartManager": _entry(aliases=["component"])}, schema_index
    )
    first = ManagerResolver(schema_index, catalog)
    second = ManagerResolver(schema_index, catalog)

    assert first.resolve("component") == second.resolve("component")
    assert first.cache is not second.cache
