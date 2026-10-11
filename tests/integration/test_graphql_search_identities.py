"""Native identity regressions through the generated global GraphQL field."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Annotated, ClassVar
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.db import models

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_search import (
    normalize_filter_input,
    normalize_id_filter_value,
)
from general_manager.api.property import graph_ql_property
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input
from general_manager.permission import ManagerBasedPermission
from general_manager.permission.base_permission import ReadPermissionPlan
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.backends.meilisearch import MeilisearchBackend
from general_manager.search.backend import SearchResult
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from general_manager.utils.testing import GeneralManagerTransactionTestCase

TOKEN = UUID("00000000-0000-0000-0000-00000000ab01")
LARGE_KEY = 2**53 + 1


class SearchIdentityPermission(ManagerBasedPermission):
    __read__: ClassVar[list[str]] = ["public"]


def search_config(*fields):
    return type(
        "SearchConfig",
        (),
        {
            "indexes": [
                IndexConfig(name="global", fields=list(fields), filters=list(fields))
            ]
        },
    )


class GraphQLSearchIdentityTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class SearchCode(GeneralManager):
            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {"id": Input(str)}
                code = models.CharField(primary_key=True, max_length=32)
                id = models.IntegerField(default=7)
                external_id = models.IntegerField(default=7)
                native_reference_id = models.IntegerField(default=7)
                external_number = models.BigIntegerField(unique=True)
                token = models.UUIDField(unique=True)
                status = models.CharField(max_length=20, default="public")

            Permission = SearchIdentityPermission
            SearchConfig = search_config(
                "code",
                "external_id",
                "native_reference_id",
                "record_key",
                "record_keys",
                "status",
            )

            @graph_ql_property(is_identifier=True)
            def record_key(self) -> str:
                return self.code

            @graph_ql_property(is_identifier=True)
            def record_keys(self) -> list[str]:
                return [self.code]

        class SearchInteger(GeneralManager):
            class Interface(DatabaseInterface):
                key = models.BigIntegerField(primary_key=True)
                native_reference = models.ForeignKey(
                    "general_manager.SearchInteger", null=True, on_delete=models.CASCADE
                )
                code_reference = models.ForeignKey(
                    "general_manager.SearchCode",
                    null=True,
                    on_delete=models.CASCADE,
                    related_name="code_sources",
                )
                uuid_reference = models.ForeignKey(
                    "general_manager.SearchCode",
                    to_field="token",
                    null=True,
                    on_delete=models.CASCADE,
                    related_name="uuid_sources",
                )
                number_reference = models.ForeignKey(
                    "general_manager.SearchCode",
                    to_field="external_number",
                    null=True,
                    on_delete=models.CASCADE,
                    related_name="number_sources",
                )
                business_number = models.IntegerField(default=7)
                external_id = models.IntegerField(default=7)
                status = models.CharField(max_length=20, default="public")

            Permission = SearchIdentityPermission
            SearchConfig = search_config(
                "key",
                "native_reference_id",
                "code_reference_id",
                "uuid_reference_id",
                "number_reference_id",
                "business_number",
                "external_id",
                "status",
                "record_key",
                "record_keys",
                "key_tuple",
                "key_set",
                "optional_keys",
                "absent_keys",
                "inferred_key",
                "business_ids",
            )

            @graph_ql_property(
                is_identifier=True, filterable=True, query_annotation=models.F("key")
            )
            def record_key(self) -> int:
                return self.key

            @graph_ql_property(is_identifier=True)
            def record_keys(self) -> list[int]:
                return [self.key]

            @graph_ql_property(is_identifier=True)
            def key_tuple(self) -> Annotated[tuple[int, ...] | None, "identity"]:
                return (self.key,)

            @graph_ql_property(is_identifier=True)
            def key_set(self) -> set[int]:
                return {self.key}

            @graph_ql_property(is_identifier=True)
            def optional_keys(self) -> list[int | None]:
                return [self.key, None]

            @graph_ql_property(is_identifier=True)
            def absent_keys(self) -> list[int] | None:
                return None

            @graph_ql_property(filterable=True, query_annotation=models.F("key"))
            def inferred_key(self) -> int:
                return self.key

            @graph_ql_property
            def business_ids(self) -> list[int]:
                return [self.business_number]

        class SearchUuid(GeneralManager):
            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {"id": Input(UUID)}
                token = models.UUIDField(primary_key=True)

            Permission = SearchIdentityPermission
            SearchConfig = search_config("token", "record_key", "record_keys")

            @graph_ql_property(is_identifier=True)
            def record_key(self) -> UUID:
                return self.token

            @graph_ql_property(is_identifier=True)
            def record_keys(self) -> list[UUID]:
                return [self.token]

        class SearchAuto(GeneralManager):
            class Interface(DatabaseInterface):
                serial = models.BigAutoField(primary_key=True)

            Permission = SearchIdentityPermission
            SearchConfig = search_config("serial")

        class SearchComposite(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(int)
                account = Input(str)

            Permission = SearchIdentityPermission
            SearchConfig = search_config("account", "external_id", "record_key")

            @graph_ql_property
            def external_id(self) -> int:
                return self.id

            @graph_ql_property(is_identifier=True)
            def record_key(self) -> int:
                return self.id

        cls.Integer = SearchInteger
        cls.Code = SearchCode
        cls.Uuid = SearchUuid
        cls.Auto = SearchAuto
        cls.Composite = SearchComposite
        cls.general_manager_classes = [
            SearchInteger,
            SearchCode,
            SearchUuid,
            SearchAuto,
            SearchComposite,
        ]

    def setUp(self) -> None:
        super().setUp()
        self.backend = DevSearchBackend()
        configure_search_backend(self.backend)
        self.Code.Interface._model.objects.create(
            code="007", id=7, external_number=LARGE_KEY, token=TOKEN
        )
        for key in (7, 2**31, LARGE_KEY):
            self.Integer.Interface._model.objects.create(
                key=key,
                native_reference_id=key,
                code_reference_id="007",
                uuid_reference_id=TOKEN,
                number_reference_id=LARGE_KEY,
            )
            self.Auto.Interface._model.objects.create(serial=key)
        self.Uuid.Interface._model.objects.create(token=TOKEN)
        indexer = SearchIndexer(self.backend)
        for manager in (self.Integer, self.Code, self.Uuid, self.Auto):
            indexer.reindex_manager(manager)
        indexer.index_instance(self.Composite(id=7, account="007"))

    def tearDown(self) -> None:
        configure_search_backend(None)
        super().tearDown()

    def execute_search(self, manager, filters, *, page=1, page_size=10):
        return GraphQL.get_schema().execute(
            """query($filters: JSONString, $types: [String], $page: Int, $size: Int) {
                search(query: "", filters: $filters, types: $types, page: $page, pageSize: $size) {
                    total totalIsExact pageInfo { totalCount currentPage totalPages }
                    results { __typename }
                }
            }""",
            variable_values={
                "filters": json.dumps(filters, default=str),
                "types": (
                    [m.__name__ for m in manager]
                    if isinstance(manager, list)
                    else [manager.__name__]
                    if manager
                    else None
                ),
                "page": page,
                "size": page_size,
            },
            context_value=SimpleNamespace(user=AnonymousUser()),
        )

    def assert_total(self, manager, filters, expected):
        result = self.execute_search(manager, filters)
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(result.data["search"]["total"], expected)
        return result.data["search"]

    def test_orm_pk_reference_and_to_field_ids_match_native_values(self) -> None:
        cases = [
            (self.Integer, "key", LARGE_KEY),
            (self.Integer, "native_reference_id", 7),
            (self.Integer, "native_reference_id", 2**31),
            (self.Integer, "native_reference_id", LARGE_KEY),
            (self.Integer, "code_reference_id", "007"),
            (self.Integer, "uuid_reference_id", TOKEN),
            (self.Integer, "number_reference_id", LARGE_KEY),
            (self.Auto, "serial", LARGE_KEY),
            (self.Uuid, "token", TOKEN),
            (self.Code, "code", "007"),
        ]
        for manager, field, value in cases:
            for suffix in ("", "__exact", "__in"):
                native = [value] if suffix == "__in" else value
                serialized = [str(value)] if suffix == "__in" else str(value)
                with self.subTest(
                    manager=manager.__name__, field=field, suffix=suffix, value=value
                ):
                    expected = self.backend.search(
                        "global",
                        "",
                        filters={field + suffix: native},
                        types=[manager.__name__],
                    ).total
                    self.assertGreater(expected, 0)
                    self.assert_total(manager, {field + suffix: serialized}, expected)
                    self.assert_total(manager, {field + suffix: native}, expected)

    def test_declared_properties_convert_scalars_and_collection_candidates(
        self,
    ) -> None:
        for manager, value in (
            (self.Integer, LARGE_KEY),
            (self.Uuid, TOKEN),
            (self.Code, "007"),
            (self.Composite, 7),
        ):
            for suffix in ("", "__exact", "__in"):
                with self.subTest(manager=manager.__name__, suffix=suffix):
                    operand = [str(value)] if suffix == "__in" else str(value)
                    self.assert_total(manager, {"record_key" + suffix: operand}, 1)
            if manager is self.Composite:
                continue
            for suffix, operand in (
                ("", [str(value)]),
                ("__exact", [str(value)]),
                ("__in", [str(value)]),
            ):
                with self.subTest(manager=manager.__name__, collection_suffix=suffix):
                    self.assert_total(manager, {"record_keys" + suffix: operand}, 1)
        for field, operand in (
            ("key_tuple", [str(LARGE_KEY)]),
            ("key_set", [str(LARGE_KEY)]),
            ("optional_keys", [str(LARGE_KEY), None]),
            ("absent_keys", None),
        ):
            with self.subTest(field=field):
                self.assert_total(
                    self.Integer,
                    {field: operand},
                    3 if operand is None or field == "optional_keys" else 1,
                )

    def test_business_keys_and_native_operators_keep_raw_adapter_behavior(self) -> None:
        for manager, field in (
            (self.Integer, "business_number"),
            (self.Integer, "external_id"),
            (self.Code, "external_id"),
            (self.Composite, "external_id"),
        ):
            with self.subTest(manager=manager.__name__, field=field):
                self.assert_total(manager, {field: "7"}, 0)
                self.assert_total(manager, {field + "__in": ["7"]}, 0)
                self.assertGreater(
                    self.assert_total(
                        manager, {field: 7}, 3 if manager is self.Integer else 1
                    )["total"],
                    0,
                )
        for filters, expected in (
            ({"key__gte": 2**31}, 2),
            ({"key__gte": str(2**31)}, 0),
            ({"code__startswith": "00"}, 1),
            ({"code": "7"}, 0),
            ({"code": 7}, 0),
        ):
            self.assert_total(
                self.Code if "code" in next(iter(filters)) else self.Integer,
                filters,
                expected,
            )
        # The raw adapter continues comparing business/identity values without metadata.
        self.assertEqual(
            self.backend.search(
                "global",
                "",
                filters={"native_reference_id": "7"},
                types=[self.Integer.__name__],
            ).total,
            0,
        )
        self.assertEqual(
            normalize_filter_input(self.Integer, {"native_reference_id": "7"})[
                "filter"
            ],
            {"native_reference_id": 7},
        )

    def test_shared_index_normalizes_each_manager_without_mutating_input(self) -> None:
        filters = {"record_key": "007"}
        original = deepcopy(filters)
        with (
            patch(
                "general_manager.api.graphql_search.parse_search_filters",
                return_value=filters,
            ),
            patch.object(self.backend, "search", wraps=self.backend.search) as search,
        ):
            self.assert_total([self.Integer, self.Code, self.Composite], filters, 3)
        self.assertEqual(filters, original)
        calls = {
            call.kwargs["types"][0]: call.kwargs["filters"]
            for call in search.call_args_list
        }
        self.assertEqual(calls[self.Integer.__name__], {"record_key": 7})
        self.assertEqual(calls[self.Code.__name__], {"record_key": "007"})

    def test_shared_spelling_can_be_an_identity_or_a_business_value(self) -> None:
        filters = [{"field": "native_reference_id", "values": ["007"]}]
        with patch.object(self.backend, "search", wraps=self.backend.search) as search:
            self.assert_total([self.Integer, self.Code], filters, 1)
        calls = {
            call.kwargs["types"][0]: call.kwargs["filters"]
            for call in search.call_args_list
        }
        self.assertEqual(calls[self.Integer.__name__], {"native_reference_id__in": [7]})
        self.assertEqual(
            calls[self.Code.__name__], {"native_reference_id__in": ["007"]}
        )
        self.assert_total(self.Integer, {"inferred_key": str(LARGE_KEY)}, 0)
        self.assert_total(self.Integer, {"business_ids": ["7"]}, 0)
        # The search envelope reserves `id`; its native business semantics are
        # still verified at the shared normalizer boundary.
        self.assertEqual(normalize_id_filter_value(self.Code, "id", "7"), "7")
        self.assertEqual(
            normalize_id_filter_value(self.Composite, "id__in", ["7"]), ["7"]
        )

    def test_backend_transport_preserves_business_lookups_and_production_grammar(
        self,
    ) -> None:
        filters = {
            "native_reference_id__in": [str(LARGE_KEY)],
            "business_number": "7",
            "external_id__in": ["7"],
            "key__gte": 7,
            "status__startswith": "pub",
        }
        expected = {**filters, "native_reference_id__in": [LARGE_KEY]}
        with patch.object(
            self.backend, "search", return_value=SearchResult(hits=[], total=0)
        ) as search:
            self.assert_total(self.Integer, filters, 0)
        self.assertEqual(search.call_args.kwargs["filters"], expected)
        self.assertEqual(filters["native_reference_id__in"], [str(LARGE_KEY)])
        self.assertEqual(
            MeilisearchBackend._build_filter_expression(
                expected, [self.Integer.__name__]
            ),
            '(type = "SearchInteger") AND (((native_reference_id = "9007199254740993") AND business_number = "7" AND (external_id = "7") AND key = "7" AND status = "pub"))',
        )

    def test_permissions_groups_intersection_nulls_and_pagination(self) -> None:
        groups = [
            {"filter": {"key": 7, "status": "public"}},
            {"filter": {"key": LARGE_KEY, "status": "public"}},
        ]
        plan = ReadPermissionPlan(
            filters=groups, requires_instance_check=False, decision="conditional"
        )
        original = deepcopy(groups)
        with patch(
            "general_manager.api.graphql_search.get_read_permission_filter",
            return_value=plan,
        ):
            payload = self.assert_total(
                self.Integer, {"key__in": ["7", str(2**31), str(LARGE_KEY)]}, 2
            )
            self.assertEqual(payload["pageInfo"]["totalCount"], 2)
            second = self.execute_search(
                self.Integer, {"key__in": ["7", str(LARGE_KEY)]}, page=2, page_size=1
            )
            self.assertIsNone(second.errors, second.errors)
            self.assertEqual(len(second.data["search"]["results"]), 1)
            self.assertEqual(second.data["search"]["total"], 2)
            self.assert_total(self.Integer, {"key": str(2**31)}, 0)
        self.assertEqual(groups, original)
        self.Integer.Interface._model.objects.filter(pk=7).update(
            native_reference_id=None
        )
        SearchIndexer(self.backend).index_instance(self.Integer(7))
        self.assert_total(self.Integer, {"native_reference_id": None}, 1)
        self.assert_total(
            self.Integer, {"native_reference_id__in": [None, str(LARGE_KEY)]}, 2
        )
        denied = ReadPermissionPlan(
            filters=[], requires_instance_check=False, decision="deny_all"
        )
        with (
            patch(
                "general_manager.api.graphql_search.get_read_permission_filter",
                return_value=denied,
            ),
            patch.object(self.backend, "search", wraps=self.backend.search) as search,
        ):
            self.assert_total(self.Integer, {"key": "invalid"}, 0)
            search.assert_not_called()

    def test_declared_constructor_and_reference_converters_are_reused(self) -> None:
        with patch.dict(
            self.Integer.Interface.input_fields,
            {"id": Input(int, normalizer=abs)},
        ):
            self.assert_total(self.Integer, {"key": "-7"}, 1)
            self.assert_total(self.Integer, {"native_reference_id": "-7"}, 0)

        target = self.Integer.Interface._model._meta.get_field(
            "native_reference"
        ).target_field
        original_to_python = target.to_python

        def reference_conversion(value):
            if isinstance(value, str):
                value = value.removeprefix("native:")
            return original_to_python(value)

        with patch.object(
            target, "to_python", side_effect=reference_conversion
        ) as convert:
            self.assert_total(self.Integer, {"native_reference_id": "native:7"}, 1)
        convert.assert_any_call("native:7")

    def test_scalar_identity_equality_lists_preserve_backend_or_grammar(self) -> None:
        for field in ("key", "native_reference_id", "record_key"):
            for suffix in ("", "__exact"):
                for values, native in (
                    (["7", str(LARGE_KEY)], [7, LARGE_KEY]),
                    ([], []),
                    ([None, "7"], [None, 7]),
                ):
                    with (
                        self.subTest(field=field, suffix=suffix, values=values),
                        patch.object(
                            self.backend,
                            "search",
                            return_value=SearchResult(hits=[], total=0),
                        ) as search,
                    ):
                        self.assert_total(self.Integer, {field + suffix: values}, 0)
                        self.assertEqual(
                            search.call_args.kwargs["filters"], {field + suffix: native}
                        )
                        self.assertEqual(
                            MeilisearchBackend._build_filter_expression(
                                {field + suffix: native}, [self.Integer.__name__]
                            ),
                            MeilisearchBackend._build_filter_expression(
                                {field + suffix: values}, [self.Integer.__name__]
                            ),
                        )
        # Development scalar/list equality is deliberately still false.
        self.assert_total(self.Integer, {"key": ["7", str(LARGE_KEY)]}, 0)

    def test_final_instance_checks_and_exclusions_apply_after_normalization(
        self,
    ) -> None:
        groups = [{"filter": {"status": "public"}, "exclude": {"key": 7}}]
        plan = ReadPermissionPlan(filters=groups, requires_instance_check=True)
        with (
            patch(
                "general_manager.api.graphql_search.get_read_permission_filter",
                return_value=plan,
            ),
            patch(
                "general_manager.api.graphql_search.can_read_instance",
                side_effect=lambda instance, _info: instance.key == LARGE_KEY,
            ),
        ):
            self.assert_total(
                self.Integer, {"key__in": ["7", str(2**31), str(LARGE_KEY)]}, 1
            )
        self.assertEqual(
            groups, [{"filter": {"status": "public"}, "exclude": {"key": 7}}]
        )

    def test_invalid_identity_operands_fail_before_backend_search(self) -> None:
        for manager, filters in (
            (self.Integer, {"key": "7.1"}),
            (self.Integer, {"key": 7.5}),
            (self.Integer, {"key": False}),
            (self.Integer, {"native_reference_id__in": ["7", "bad"]}),
            (self.Integer, {"record_key": "bad"}),
            (self.Integer, {"record_keys": ["7", "bad"]}),
            (self.Uuid, {"token": "007"}),
        ):
            with (
                self.subTest(filters=filters),
                patch.object(
                    self.backend, "search", wraps=self.backend.search
                ) as search,
            ):
                result = self.execute_search(manager, filters)
                self.assertIsNotNone(result.errors)
                search.assert_not_called()
