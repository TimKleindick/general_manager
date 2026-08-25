"""Contract tests for planned-chat application manager catalogs."""

from __future__ import annotations

from collections.abc import Mapping

import graphene
import pytest
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.planned.catalog import load_manager_catalog
from general_manager.chat.settings import ChatConfigurationError, validate_chat_settings


_CATALOG_BACKEND_UNAVAILABLE = "catalog backend unavailable"


@pytest.fixture
def exposed_schema_index() -> dict[str, dict[str, object]]:
    return {
        "MaterialManager": {"manager": "MaterialManager"},
        "PartManager": {"manager": "PartManager"},
    }


def catalog_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "domain": "manufacturing",
        "aliases": ["part"],
        "use_when": "The request concerns manufactured parts.",
        "distinguish_from": [],
    }
    entry.update(overrides)
    return entry


def dotted_catalog_source() -> Mapping[str, Mapping[str, object]]:
    return {"PartManager": catalog_entry()}


def failing_catalog_source() -> Mapping[str, Mapping[str, object]]:
    raise RuntimeError(_CATALOG_BACKEND_UNAVAILABLE)


class CatalogSettingsProvider:
    def complete(self, messages: list[object], tools: list[object]):
        del messages, tools
        yield from ()


def test_catalog_rejects_hidden_manager_key(
    exposed_schema_index: dict[str, dict[str, object]],
) -> None:
    with pytest.raises(ChatConfigurationError, match="chat-exposed"):
        load_manager_catalog({"HiddenManager": catalog_entry()}, exposed_schema_index)


def test_catalog_accepts_duplicate_aliases_as_ambiguity(
    exposed_schema_index: dict[str, dict[str, object]],
) -> None:
    catalog = load_manager_catalog(
        {
            "PartManager": catalog_entry(aliases=["item"]),
            "MaterialManager": catalog_entry(aliases=["item"]),
        },
        exposed_schema_index,
    )

    assert catalog.entries["PartManager"].aliases == ("item",)


@pytest.mark.parametrize(
    "missing", ["domain", "aliases", "use_when", "distinguish_from"]
)
def test_catalog_requires_every_documented_entry_field(
    exposed_schema_index: dict[str, dict[str, object]], missing: str
) -> None:
    entry = catalog_entry()
    entry.pop(missing)

    with pytest.raises(ChatConfigurationError, match=missing):
        load_manager_catalog({"PartManager": entry}, exposed_schema_index)


def test_catalog_accepts_empty_sequences_and_normalizes_copied_metadata(
    exposed_schema_index: dict[str, dict[str, object]],
) -> None:
    source = {
        "PartManager": catalog_entry(
            domain="  manufacturing  ",
            aliases=[" Component ", "component"],
            use_when="  Designed\ncomponents  ",
            distinguish_from=[],
        )
    }

    catalog = load_manager_catalog(source, exposed_schema_index)
    source["PartManager"]["aliases"].append("changed")  # type: ignore[index]

    assert catalog.entries["PartManager"].domain == "manufacturing"
    assert catalog.entries["PartManager"].aliases == ("component",)
    assert catalog.entries["PartManager"].use_when == "Designed components"
    assert catalog.entries["PartManager"].distinguish_from == ()


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (catalog_entry(domain=""), "domain"),
        (catalog_entry(use_when=" "), "use_when"),
        (catalog_entry(aliases="part"), "aliases"),
        (catalog_entry(aliases=[""]), "aliases"),
        (catalog_entry(distinguish_from="MaterialManager"), "distinguish_from"),
        (catalog_entry(distinguish_from=["HiddenManager"]), "chat-exposed"),
        (catalog_entry(distinguish_from=["PartManager"]), "itself"),
    ],
)
def test_catalog_rejects_invalid_metadata(
    exposed_schema_index: dict[str, dict[str, object]],
    entry: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ChatConfigurationError, match=message):
        load_manager_catalog({"PartManager": entry}, exposed_schema_index)


def test_catalog_loads_mapping_callable_and_dotted_callable(
    exposed_schema_index: dict[str, dict[str, object]],
) -> None:
    mapping_catalog = load_manager_catalog(
        {"PartManager": catalog_entry()}, exposed_schema_index
    )
    callable_catalog = load_manager_catalog(dotted_catalog_source, exposed_schema_index)
    dotted_catalog = load_manager_catalog(
        "tests.unit.test_chat_planned_catalog.dotted_catalog_source",
        exposed_schema_index,
    )

    assert mapping_catalog.entries == callable_catalog.entries == dotted_catalog.entries


@pytest.mark.parametrize(
    "source",
    [
        "tests.unit.test_chat_planned_catalog.missing_catalog_source",
        failing_catalog_source,
        ["not", "a", "catalog"],
    ],
)
def test_catalog_source_errors_become_configuration_errors(
    exposed_schema_index: dict[str, dict[str, object]], source: object
) -> None:
    with pytest.raises(ChatConfigurationError, match="catalog"):
        load_manager_catalog(source, exposed_schema_index)


def test_catalog_fingerprint_is_stable_for_equivalent_normalized_entries(
    exposed_schema_index: dict[str, dict[str, object]],
) -> None:
    first = load_manager_catalog(
        {
            "PartManager": catalog_entry(
                aliases=["Part", "component"],
                distinguish_from=["MaterialManager"],
            ),
            "MaterialManager": catalog_entry(aliases=[]),
        },
        exposed_schema_index,
    )
    second = load_manager_catalog(
        {
            "MaterialManager": catalog_entry(aliases=[]),
            "PartManager": catalog_entry(
                aliases=["component", " part "],
                distinguish_from=["MaterialManager"],
            ),
        },
        exposed_schema_index,
    )

    assert first.fingerprint == second.fingerprint


@override_settings(
    GENERAL_MANAGER={
        "CHAT": {
            "provider": "tests.unit.test_chat_planned_catalog.CatalogSettingsProvider",
            "planned": {
                "enabled": True,
                "catalog": "tests.unit.test_chat_planned_catalog.missing_catalog_source",
            },
        }
    }
)
def test_enabled_planned_settings_validate_catalog_source_at_startup() -> None:
    class Query(graphene.ObjectType):
        ping = graphene.String()

    GraphQL._schema = graphene.Schema(query=Query)
    try:
        with pytest.raises(ChatConfigurationError, match="catalog"):
            validate_chat_settings()
    finally:
        GraphQL.reset_registry()
