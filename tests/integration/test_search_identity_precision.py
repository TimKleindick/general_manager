"""Native identity precision through real search providers."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest

from general_manager import GeneralManager, Input
from general_manager.api.graphql_search import normalize_id_filter_value
from general_manager.api.property import graph_ql_property
from general_manager.interface import CalculationInterface
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.backends.meilisearch import MeilisearchBackend
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from general_manager.search.identity import build_search_filters

A = 9007199254740995
B = 9007199254740996
TOKEN_A = UUID("00112233-4455-6677-8899-aabbccddee01")
TOKEN_B = UUID("00112233-4455-6677-8899-aabbccddee02")


@pytest.fixture(params=["dev", "meilisearch"])
def precision_index(request, monkeypatch):
    for name in (
        "all_classes",
        "read_only_classes",
        "pending_attribute_initialization",
        "pending_graphql_interfaces",
    ):
        monkeypatch.setattr(
            GeneralManagerMeta, name, list(getattr(GeneralManagerMeta, name))
        )
    index_name = f"gm_identity_precision_{uuid4().hex}"

    class PrecisionRecord532(GeneralManager):
        class Interface(CalculationInterface):
            id = Input(int, possible_values=(A, B))

        @graph_ql_property(is_identifier=True, cache="none")
        def customer_id(self) -> int:
            return self.id

        @graph_ql_property(is_identifier=True, cache="none")
        def related_ids(self) -> list[int | None] | None:
            return [self.id, None] if self.id == A else [self.id]

        @graph_ql_property(is_identifier=True, cache="none")
        def nullable_key(self) -> int | None:
            return None if self.id == A else self.id

        @graph_ql_property(is_identifier=True, cache="none")
        def uuid_key(self) -> UUID:
            return TOKEN_A if self.id == A else TOKEN_B

        @graph_ql_property(is_identifier=True, cache="none")
        def opaque_key(self) -> str:
            return "Aa0001" if self.id == A else "aa0001"

        @graph_ql_property(is_identifier=True, cache="none")
        def opaque_zero(self) -> str:
            return "0001" if self.id == A else "1"

        @graph_ql_property(cache="none")
        def business_number(self) -> int:
            return 17

        @graph_ql_property(cache="none")
        def literal_key(self) -> str:
            return "0001" if self.id == A else "1"

        @graph_ql_property(cache="none")
        def external_id(self) -> int:
            return 17

    manager = PrecisionRecord532
    fields = ("customer_id", "business_number", "literal_key", "external_id")
    manager.SearchConfig = type(
        "SearchConfig",
        (),
        {"indexes": [IndexConfig(name=index_name, fields=fields, filters=fields)]},
    )
    if request.param == "meilisearch":
        url = os.getenv("MEILISEARCH_URL")
        if not url:
            pytest.skip("Set MEILISEARCH_URL to run real provider regressions.")
        pytest.importorskip("meilisearch")
        backend = MeilisearchBackend(
            url=url,
            api_key=os.getenv("MEILISEARCH_API_KEY"),
            task_timeout_in_ms=15000,
        )
    else:
        backend = DevSearchBackend()
    try:
        for key in (A, B):
            SearchIndexer(backend).index_instance(manager(key))
        yield backend, manager, index_name
    finally:
        if isinstance(backend, MeilisearchBackend):
            backend._wait_for_task(backend._client.delete_index(index_name))


def identifiers(result):
    return sorted(hit.identification["id"] for hit in result.hits)


def reindex_fields(backend, manager, index_name, fields):
    manager.SearchConfig.indexes = [
        IndexConfig(name=index_name, fields=fields, filters=fields)
    ]
    for key in (A, B):
        SearchIndexer(backend).index_instance(manager(key))


def test_adjacent_integer_identity_equality_and_membership(precision_index, subtests):
    backend, manager, index_name = precision_index
    for lookup in ("customer_id", "customer_id__exact", "customer_id__in"):
        for operand in (A, str(A), "000" + str(A)):
            with subtests.test(lookup=lookup, operand=operand):
                supplied = [operand] if lookup.endswith("__in") else operand
                native = normalize_id_filter_value(manager, lookup, supplied)
                result = backend.search(
                    index_name,
                    "",
                    filters=build_search_filters(manager, index_name, {lookup: native}),
                )
                assert identifiers(result) == [A]
                assert result.total == 1
                assert result.hits[0].data["customer_id"] == A
                assert type(manager(A).customer_id) is int


def test_unmarked_business_and_literal_controls(precision_index, subtests):
    backend, manager, index_name = precision_index
    for filters, expected in (
        ({"business_number": 17}, [A, B]),
        ({"external_id": 17}, [A, B]),
        ({"literal_key": "0001"}, [A]),
        ({"literal_key": "1"}, [B]),
        ({"customer_id": A, "business_number": 17}, [A]),
        ([{"customer_id": A, "literal_key": "0001"}, {"customer_id": B}], [A, B]),
    ):
        with subtests.test(filters=filters):
            assert (
                identifiers(
                    backend.search(
                        index_name,
                        "",
                        filters=build_search_filters(manager, index_name, filters),
                    )
                )
                == expected
            )


def test_nullable_list_uuid_and_opaque_identity_shapes(precision_index, subtests):
    backend, manager, index_name = precision_index
    fields = ("related_ids", "nullable_key", "uuid_key", "opaque_key", "opaque_zero")
    reindex_fields(backend, manager, index_name, fields)
    for filters, expected in (
        ({"related_ids": A}, [A]),
        ({"related_ids__in": [A]}, [A]),
        ({"related_ids__in": [None]}, [A]),
        ({"related_ids__in": []}, []),
        ({"nullable_key": None}, [A]),
        ({"uuid_key": str(TOKEN_A).upper()}, [A]),
        ({"uuid_key__in": [TOKEN_A]}, [A]),
        ({"opaque_key": "Aa0001"}, [A]),
        ({"opaque_key__in": ["aa0001"]}, [B]),
        ({"opaque_zero": "0001"}, [A]),
        ({"opaque_zero": "1"}, [B]),
    ):
        with subtests.test(filters=filters):
            # Dev retains its existing scalar-vs-list exact comparison grammar.
            if isinstance(backend, DevSearchBackend) and filters == {"related_ids": A}:
                expected = []
            assert (
                identifiers(
                    backend.search(
                        index_name,
                        "",
                        filters=build_search_filters(manager, index_name, filters),
                    )
                )
                == expected
            )
    assert manager(A).related_ids == [A, None]
    assert manager(A).uuid_key is TOKEN_A
    assert manager(A).opaque_zero == "0001"


def test_custom_document_alias_has_explicit_native_identity_provenance(precision_index):
    backend, manager, index_name = precision_index
    manager.SearchConfig = type(
        "SearchConfig",
        (),
        {
            "indexes": [
                IndexConfig(
                    name=index_name,
                    fields=["customer_alias"],
                    filters=["customer_alias"],
                    identity_fields={"customer_alias": int},
                )
            ],
            "to_document": staticmethod(
                lambda instance: {"customer_alias": instance.id}
            ),
        },
    )
    for key in (A, B):
        SearchIndexer(backend).index_instance(manager(key))
    filters = build_search_filters(
        manager, index_name, {"customer_alias__in": [str(A)]}
    )
    result = backend.search(index_name, "", filters=filters)
    assert identifiers(result) == [A]
    assert result.hits[0].data["customer_alias"] == A


def test_stale_and_mixed_meilisearch_documents_require_reindex(precision_index):
    backend, manager, index_name = precision_index
    if not isinstance(backend, MeilisearchBackend):
        pytest.skip("Persistent provider migration regression.")
    from general_manager.search.indexer import _collect_documents_for_instance

    legacy = []
    for key in (A, B):
        documents = _collect_documents_for_instance(manager(key))[0].documents
        payload = backend._document_payload(documents[0])
        payload.pop("gm_identity")
        legacy.append(payload)
    index = backend._client.index(index_name)
    backend._wait_for_task(index.delete_all_documents())
    backend._wait_for_task(index.add_documents(legacy))
    cold = MeilisearchBackend(
        url=os.environ["MEILISEARCH_URL"], task_timeout_in_ms=15000
    )
    filters = {"customer_id": A}
    assert cold.search(index_name, "", filters=filters).total == 0
    assert cold.search(index_name, "", filters={"customer_id__in": str(A)}).total == 0
    assert cold.search(index_name, "", filters={"business_number": 17}).total == 2
    SearchIndexer(backend).index_instance(manager(B))
    assert cold.search(index_name, "", filters=filters).total == 0
    assert identifiers(cold.search(index_name, "", filters={"customer_id": B})) == [B]
    SearchIndexer(backend).reindex_manager_index(manager, index_name)
    assert identifiers(cold.search(index_name, "", filters=filters)) == [A]
    assert identifiers(
        cold.search(index_name, "", filters={"customer_id__in": [A, B]})
    ) == [A, B]


def test_shared_index_native_kinds_and_business_roles_are_scoped(precision_index):
    backend, integer_manager, index_name = precision_index
    from general_manager.search.identity import bind_search_filters

    class OpaqueSharedPrecision532(GeneralManager):
        class Interface(CalculationInterface):
            id = Input(str, possible_values=(str(A), "000" + str(A), "Aa0001"))

        @graph_ql_property(is_identifier=True)
        def customer_id(self) -> str:
            return self.id

    class BusinessSharedPrecision532(GeneralManager):
        class Interface(CalculationInterface):
            id = Input(int, possible_values=(17,))

        @graph_ql_property
        def customer_id(self) -> int:
            return 17

    class UuidSharedPrecision532(GeneralManager):
        class Interface(CalculationInterface):
            id = Input(UUID, possible_values=(TOKEN_A,))

        @graph_ql_property(is_identifier=True)
        def customer_id(self) -> UUID:
            return self.id

    for manager in (
        OpaqueSharedPrecision532,
        BusinessSharedPrecision532,
        UuidSharedPrecision532,
    ):
        manager.SearchConfig = type(
            "SearchConfig",
            (),
            {
                "indexes": [
                    IndexConfig(
                        name=index_name,
                        fields=["customer_id"],
                        filters=["customer_id"],
                    )
                ]
            },
        )
        SearchIndexer(backend).reindex_manager(manager)
    filters = {"customer_id": "000" + str(A)}
    bound = bind_search_filters(index_name, filters, None)
    result = backend.search(index_name, "", filters=bound)
    assert sorted(
        (hit.type, hit.identification["id"]) for hit in result.hits
    ) == sorted(
        [
            (integer_manager.__name__, A),
            (OpaqueSharedPrecision532.__name__, "000" + str(A)),
        ]
    )
    assert filters == {"customer_id": "000" + str(A)}
    business = backend.search(
        index_name,
        "",
        filters=bind_search_filters(index_name, {"customer_id": 17}, None),
    )
    assert [(hit.type, hit.identification["id"]) for hit in business.hits] == [
        (BusinessSharedPrecision532.__name__, 17)
    ]
    typed = backend.search(
        index_name,
        "",
        filters=bind_search_filters(index_name, filters, [integer_manager.__name__]),
        types=[integer_manager.__name__],
    )
    assert identifiers(typed) == [A]
    opaque = backend.search(
        index_name,
        "",
        filters=bind_search_filters(
            index_name,
            {"customer_id": "Aa0001"},
            None,
        ),
    )
    assert [(hit.type, hit.identification["id"]) for hit in opaque.hits] == [
        (OpaqueSharedPrecision532.__name__, "Aa0001")
    ]
    mixed = backend.search(
        index_name,
        "",
        filters=bind_search_filters(
            index_name,
            {"customer_id__in": [str(A), "Aa0001"]},
            None,
        ),
    )
    assert sorted((hit.type, hit.identification["id"]) for hit in mixed.hits) == sorted(
        [
            (integer_manager.__name__, A),
            (OpaqueSharedPrecision532.__name__, str(A)),
            (OpaqueSharedPrecision532.__name__, "Aa0001"),
        ]
    )
    assert (
        backend.search(
            index_name,
            "",
            filters=bind_search_filters(
                index_name,
                {"customer_id": False},
                [integer_manager.__name__],
            ),
        ).total
        == 0
    )


def test_scalar_in_keeps_provider_grammar_without_numeric_fallback(precision_index):
    backend, manager, index_name = precision_index
    filters = build_search_filters(manager, index_name, {"customer_id__in": str(A)})
    result = backend.search(index_name, "", filters=filters)
    assert identifiers(result) == (
        [A] if isinstance(backend, MeilisearchBackend) else []
    )


def test_unregistered_manual_documents_have_supported_identity_settings(
    precision_index,
):
    backend, _manager, index_name = precision_index
    from general_manager.search.backend import SearchDocument
    from general_manager.search.identity import SearchIdentityFilterGroup

    owned = index_name + "_manual"
    try:
        backend.ensure_index(
            owned,
            {
                "filterable_fields": ["key"],
                "identity_fields": {"UnregisteredManual": {"key": int}},
            },
        )
        backend.upsert(
            owned,
            [
                SearchDocument(
                    id=str(key),
                    type="UnregisteredManual",
                    identification={"id": key},
                    index=owned,
                    data={"key": key},
                    field_boosts={},
                    identity_fields={"key": int},
                )
                for key in (A, B)
            ],
        )
        result = backend.search(
            owned,
            "",
            filters=SearchIdentityFilterGroup(
                {"key__in": [str(A)]},
                identity_fields={"key": int},
            ),
        )
        assert identifiers(result) == [A]
        assert result.hits[0].data["key"] == A
        assert (
            backend.search(
                owned,
                "",
                filters=SearchIdentityFilterGroup(
                    {"key__in": []},
                    identity_fields={"key": int},
                ),
            ).total
            == 0
        )
    finally:
        if isinstance(backend, MeilisearchBackend):
            backend._wait_for_task(backend._client.delete_index(owned))


def test_distinct_identity_lookups_remain_anded_without_key_collisions(
    precision_index, subtests
):
    backend, _manager, index_name = precision_index
    from general_manager.search.identity import bind_search_filters

    for filters in (
        {"customer_id": A, "customer_id__exact": B},
        {"customer_id": [A], "customer_id__in": [B]},
        {"customer_id": "Aa0001", "customer_id__in": [str(A), "Aa0001"]},
        {"customer_id__in": [str(A), "Aa0001"], "customer_id": "Aa0001"},
    ):
        with subtests.test(filters=filters):
            bound = bind_search_filters(index_name, filters, None)
            assert backend.search(index_name, "", filters=bound).total == 0


def test_business_only_or_group_preserves_unregistered_documents(precision_index):
    backend, manager, index_name = precision_index
    from general_manager.search.backend import SearchDocument
    from general_manager.search.identity import bind_search_filters

    backend.upsert(
        index_name,
        [
            SearchDocument(
                id="unregistered-business",
                type="UnregisteredBusiness",
                identification={"id": 42},
                index=index_name,
                data={"business_number": 29},
                field_boosts={},
            )
        ],
    )
    filters = [{"customer_id": A}, {"business_number": 29}]
    bound = bind_search_filters(index_name, filters, None)
    result = backend.search(index_name, "", filters=bound)
    assert sorted(
        (hit.type, hit.identification["id"]) for hit in result.hits
    ) == sorted(
        [
            (manager.__name__, A),
            ("UnregisteredBusiness", 42),
        ]
    )
    assert filters == [{"customer_id": A}, {"business_number": 29}]
