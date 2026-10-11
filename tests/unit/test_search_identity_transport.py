"""Explicit search identity metadata and provider transport contracts."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import pytest

from general_manager.search.backend import SearchDocument
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.backends.meilisearch import MeilisearchBackend
from general_manager.search.config import IndexConfig

A = 9007199254740995
B = 9007199254740996


def document(value, identity_fields):
    return SearchDocument(
        id=str(value),
        type="LowLevelIdentity",
        identification={"id": value},
        index="owned_transport",
        data={"key": value, "quantity": 17},
        field_boosts={},
        identity_fields=identity_fields,
    )


def test_document_identity_metadata_does_not_coerce_native_values():
    value = UUID("00112233-4455-6677-8899-aabbccddeeff")
    doc = document(value, {"key": UUID})
    assert doc.data["key"] is value
    assert doc.identification["id"] is value
    assert doc.identity_fields == {"key": UUID}


@pytest.mark.parametrize("native_type", [int, str, UUID])
def test_config_explicit_identity_alias(native_type):
    config = IndexConfig(
        name="owned_transport",
        fields=["key"],
        filters=["key"],
        identity_fields={"key": native_type},
    )
    assert config.identity_fields == {"key": native_type}


@pytest.mark.parametrize("native_type", [bool, float, object, list[int], None, "int"])
def test_config_rejects_unsupported_identity_types(native_type):
    with pytest.raises((ValueError, TypeError), match=r"\bkey\b"):
        IndexConfig(
            name="owned_transport", fields=["key"], identity_fields={"key": native_type}
        )


def test_reserved_identity_envelope_cannot_be_configured():
    with pytest.raises(ValueError, match="reserved"):
        IndexConfig(name="owned_transport", fields=["gm_identity"])


def test_low_level_dev_explicit_identity_filter_retains_native_mapping():
    from general_manager.search.identity import SearchIdentityFilterGroup

    backend = DevSearchBackend()
    backend.upsert(
        "owned_transport", [document(A, {"key": int}), document(B, {"key": int})]
    )
    filters = SearchIdentityFilterGroup(
        {"key__in": ["000" + str(A)]}, identity_fields={"key": int}
    )
    assert isinstance(filters, Mapping)
    assert dict(filters) == {"key__in": ["000" + str(A)]}
    result = backend.search("owned_transport", "", filters=filters)
    assert [hit.identification["id"] for hit in result.hits] == [A]
    assert result.hits[0].data["key"] == A


def test_meilisearch_identity_tokens_preserve_case_and_source_values():
    from general_manager.search.identity import SearchIdentityFilterGroup

    first = document("Aa0001", {"key": str})
    second = document("aa0001", {"key": str})
    payload = MeilisearchBackend._document_payload(first)
    other = MeilisearchBackend._document_payload(second)
    assert payload["data"] == first.data
    assert payload["key"] == "Aa0001"
    assert payload["quantity"] == 17
    assert payload["gm_identity"] != other["gm_identity"]
    expr = MeilisearchBackend._build_filter_expression(
        SearchIdentityFilterGroup({"key": "Aa0001"}, identity_fields={"key": str}), None
    )
    assert "gm_identity." in expr
    assert "Aa0001" not in expr
    assert first.data["key"] == "Aa0001"


def test_identity_token_length_is_bounded_for_long_opaque_keys():
    from general_manager.search.identity import identity_token

    assert len(identity_token(str, "a" * 10000)) < 100
    assert identity_token(str, "A" * 10000) != identity_token(str, "a" * 10000)
    assert identity_token(str, "0001") != identity_token(str, "1")
    assert identity_token(int, "0001") == identity_token(int, 1)
    assert identity_token(str, "1") != identity_token(int, 1)


def test_unmarked_range_and_pattern_filters_keep_dev_behavior():
    backend = DevSearchBackend()
    backend.upsert(
        "owned_transport",
        [
            SearchDocument(
                id="a",
                type="Business",
                identification={"id": 1},
                index="owned_transport",
                data={"quantity": 17, "name": "Alpha"},
                field_boosts={},
            ),
            SearchDocument(
                id="b",
                type="Business",
                identification={"id": 2},
                index="owned_transport",
                data={"quantity": 21, "name": "Beta"},
                field_boosts={},
            ),
        ],
    )
    result = backend.search("owned_transport", "", filters={"quantity__gte": 18})
    assert [hit.id for hit in result.hits] == ["b"]
    result = backend.search("owned_transport", "", filters={"name__startswith": "Al"})
    assert [hit.id for hit in result.hits] == ["a"]


def test_mixed_explicit_and_registered_plain_or_groups_bind_independently(monkeypatch):
    from general_manager.search import identity, registry
    from general_manager.search.identity import SearchIdentityFilterGroup

    class RegisteredIdentity:
        pass

    config = IndexConfig(
        name="owned_transport",
        fields=["key"],
        filters=["key"],
        identity_fields={"key": int},
    )
    monkeypatch.setattr(
        registry, "iter_index_configs", lambda _name: [(RegisteredIdentity, config)]
    )
    monkeypatch.setattr(registry, "get_index_config", lambda _manager, _name: config)
    monkeypatch.setattr(registry, "get_type_label", lambda manager: manager.__name__)
    monkeypatch.setattr(
        identity,
        "get_search_identity_fields",
        lambda _manager, config: dict(config.identity_fields),
    )
    explicit = SearchIdentityFilterGroup(
        {"quantity": 17}, identity_fields={}, type_label="Business"
    )
    filters = [explicit, {"key": A}]
    bound = identity.bind_search_filters("owned_transport", filters, None)
    assert bound[0] is explicit
    assert isinstance(bound[1], SearchIdentityFilterGroup)
    assert bound[1].identity_fields == {"key": int}
    assert bound[1].type_label == "RegisteredIdentity"
    assert filters[1] == {"key": A}


def test_explicit_dev_identity_does_not_match_missing_or_unmarked_document_fields():
    from general_manager.search.identity import SearchIdentityFilterGroup

    backend = DevSearchBackend()
    backend.upsert(
        "owned_transport",
        [
            document(A, {}),
            SearchDocument(
                id="missing",
                type="LowLevelIdentity",
                identification={"id": B},
                index="owned_transport",
                data={},
                field_boosts={},
                identity_fields={"key": int},
            ),
        ],
    )
    assert (
        backend.search(
            "owned_transport",
            "",
            filters=SearchIdentityFilterGroup(
                {"key": A},
                identity_fields={"key": int},
            ),
        ).total
        == 0
    )
    assert (
        backend.search(
            "owned_transport",
            "",
            filters=SearchIdentityFilterGroup(
                {"key": None},
                identity_fields={"key": int},
            ),
        ).total
        == 0
    )
    assert MeilisearchBackend._document_payload(document(A, {}))["gm_identity"] == {}
    missing = backend._indexes["owned_transport"].documents["missing"]
    assert MeilisearchBackend._document_payload(missing)["gm_identity"] == {}


def test_indexer_sends_native_contracts_without_provider_shadow_fields(monkeypatch):
    from general_manager.search import indexer
    from general_manager.search.registry import SearchIndexSettings

    class NativeManager:
        pass

    config = IndexConfig(
        name="owned_transport",
        fields=["key"],
        filters=["key"],
        identity_fields={"key": int},
    )
    settings = SearchIndexSettings(
        searchable_fields=("key",),
        filterable_fields=("key", "type"),
        sortable_fields=(),
        field_boosts={},
    )
    monkeypatch.setattr(indexer, "collect_index_settings", lambda _name: settings)
    monkeypatch.setattr(
        indexer, "iter_index_configs", lambda _name: [(NativeManager, config)]
    )
    monkeypatch.setattr(indexer, "get_type_label", lambda manager: manager.__name__)
    monkeypatch.setattr(
        indexer,
        "get_search_identity_fields",
        lambda _manager, config: dict(config.identity_fields),
    )
    backend = DevSearchBackend()
    indexer._ensure_index(backend, "owned_transport")
    actual = backend._indexes["owned_transport"].settings
    assert actual["filterable_fields"] == ("key", "type")
    assert actual["identity_fields"] == {"NativeManager": {"key": int}}
