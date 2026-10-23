"""Authenticated native identity searches through real providers and row gates."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import ClassVar
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import models
from django.utils.crypto import get_random_string

from general_manager import GeneralManager
from general_manager.api.property import graph_ql_property
from general_manager.interface import DatabaseInterface
from general_manager.permission import ManagerBasedPermission
from general_manager.permission.base_permission import ReadPermissionPlan
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.backends.meilisearch import MeilisearchBackend
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from general_manager.utils.testing import GeneralManagerTransactionTestCase

A = 9007199254740995
B = 9007199254740996


class _AuthenticatedPrecisionTests(GeneralManagerTransactionTestCase):
    __test__ = False
    provider = "dev"

    @classmethod
    def setUpClass(cls):
        cls.index_name = "gm_authenticated_precision_" + uuid4().hex

        class AuthenticatedPrecision532(GeneralManager):
            class Interface(DatabaseInterface):
                key = models.BigIntegerField(primary_key=True)
                reference = models.ForeignKey(
                    "general_manager.AuthenticatedPrecision532",
                    null=True,
                    on_delete=models.SET_NULL,
                )
                business_number = models.IntegerField(default=17)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                constraints: ClassVar[list[dict]] = []
                denied: ClassVar[set[int]] = set()
                checked: ClassVar[list[tuple[int, int]]] = []

                def get_read_permission_plan(self):
                    if not self.request_user.is_authenticated:
                        return ReadPermissionPlan(
                            filters=[],
                            requires_instance_check=False,
                            decision="deny_all",
                        )
                    return ReadPermissionPlan(
                        filters=self.constraints,
                        requires_instance_check=True,
                        instance_check_reasons=("unfilterable_read_rule",),
                    )

                def can_read_instance(self):
                    self.checked.append((self.request_user.pk, self.instance.key))
                    return (
                        self.request_user.is_authenticated
                        and self.instance.key not in self.denied
                    )

            SearchConfig = type(
                "SearchConfig",
                (),
                {
                    "indexes": [
                        IndexConfig(
                            name=cls.index_name,
                            fields=[
                                "key",
                                "customer_id",
                                "reference_id",
                                "business_number",
                            ],
                            filters=[
                                "key",
                                "customer_id",
                                "reference_id",
                                "business_number",
                            ],
                        )
                    ]
                },
            )

            @graph_ql_property(is_identifier=True)
            def customer_id(self) -> int:
                return self.key

        cls.Record = AuthenticatedPrecision532
        cls.general_manager_classes = [AuthenticatedPrecision532]
        super().setUpClass()

    def setUp(self):
        super().setUp()
        password = get_random_string(16)
        self.user = get_user_model().objects.create_user(
            username="precision-user",
            password=password,
        )
        assert self.client.login(username="precision-user", password=password)
        if self.provider == "meilisearch":
            url = os.getenv("MEILISEARCH_URL")
            if not url:
                pytest.skip("Set MEILISEARCH_URL to run real provider regressions.")
            self.backend = MeilisearchBackend(
                url=url,
                api_key=os.getenv("MEILISEARCH_API_KEY"),
                task_timeout_in_ms=15000,
            )
        else:
            self.backend = DevSearchBackend()
        configure_search_backend(self.backend)
        self.Record.Permission.constraints = []
        self.Record.Permission.denied = set()
        self.Record.Permission.checked = []
        self.Record.Interface._model.objects.create(key=A)
        self.Record.Interface._model.objects.create(key=B, reference_id=A)
        SearchIndexer(self.backend).reindex_manager(self.Record)

    def tearDown(self):
        configure_search_backend(None)
        if isinstance(self.backend, MeilisearchBackend):
            self.backend._wait_for_task(
                self.backend._client.delete_index(self.index_name)
            )
        super().tearDown()

    def search(self, filters, *, page=1, size=10):
        response = self.query(
            """query($index: String!, $filters: JSONString, $page: Int, $size: Int) {
                search(index: $index, query: "", filters: $filters, page: $page, pageSize: $size) {
                    total totalIsExact pageInfo { totalCount currentPage totalPages }
                    results { ... on AuthenticatedPrecision532Type { key customerId businessNumber } }
                }
            }""",
            variables={
                "index": self.index_name,
                "filters": json.dumps(filters),
                "page": page,
                "size": size,
            },
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["search"]
        self.assertEqual(payload["total"], payload["pageInfo"]["totalCount"])
        self.assertTrue(payload["totalIsExact"])
        return payload

    def test_authenticated_conditional_equality_and_membership(self):
        for field in ("key", "customer_id"):
            for suffix in ("", "__exact", "__in"):
                with self.subTest(field=field, suffix=suffix):
                    self.Record.Permission.checked = []
                    value = [str(A)] if suffix == "__in" else "000" + str(A)
                    payload = self.search({field + suffix: value})
                    self.assertEqual(payload["total"], 1)
                    self.assertEqual(
                        payload["results"],
                        [{"key": str(A), "customerId": str(A), "businessNumber": 17}],
                    )
                    self.assertEqual(
                        self.Record.Permission.checked,
                        [(self.user.pk, A), (self.user.pk, B), (self.user.pk, A)],
                    )

    def test_native_permission_constraints_conflicts_or_and_counts(self):
        groups = [{"filter": {"customer_id": A}}, {"filter": {"customer_id": B}}]
        original = deepcopy(groups)
        self.Record.Permission.constraints = groups
        self.Record.Permission.denied = {B}
        payload = self.search({"customer_id__in": [str(A), str(B)]}, size=1)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["results"][0]["customerId"], str(A))
        self.assertEqual(self.search({}, page=2, size=1)["results"], [])
        self.assertEqual(groups, original)
        self.Record.Permission.constraints = [{"filter": {"customer_id": A}}]
        self.assertEqual(self.search({"customer_id": str(B)})["total"], 0)
        self.Record.Permission.checked = []
        self.assertEqual(self.search({"business_number": 17})["total"], 1)
        # Native constraints restrict preflight to A; the hit gate checks A again.
        self.assertEqual(
            self.Record.Permission.checked, [(self.user.pk, A), (self.user.pk, A)]
        )

    def test_final_native_exclusions_and_anonymous_denial(self):
        self.Record.Permission.constraints = [
            {"filter": {"business_number": 17}, "exclude": {"customer_id": B}}
        ]
        self.assertEqual(self.search({"customer_id__in": [str(A), str(B)]})["total"], 1)
        self.client.logout()
        self.Record.Permission.checked = []
        self.assertEqual(self.search({"customer_id": str(A)})["total"], 0)
        self.assertEqual(self.Record.Permission.checked, [])

    def test_actual_raw_foreign_key_identity_keeps_native_database_value(self):
        payload = self.search({"reference_id__in": ["000" + str(A)]})
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["results"][0]["key"], str(B))
        self.assertEqual(self.Record(B).reference_id, A)
        self.assertEqual(self.Record.Interface._model.objects.get(pk=B).reference_id, A)

    def test_settings_only_command_preserves_identity_filterability(self):
        call_command("search_index", indexes=[self.index_name])
        self.assertEqual(self.search({"customer_id": str(A)})["total"], 1)

    def test_user_and_permission_identity_aliases_keep_native_and_semantics(self):
        for permission_key in ("customer_id__exact", "customer_id__in"):
            with self.subTest(permission_key=permission_key):
                native = [B] if permission_key.endswith("__in") else B
                groups = [{"filter": {permission_key: native}}]
                self.Record.Permission.constraints = groups
                self.assertEqual(self.search({"customer_id": str(A)})["total"], 0)
                self.assertEqual(groups, [{"filter": {permission_key: native}}])


class TestAuthenticatedPrecisionDev(_AuthenticatedPrecisionTests):
    __test__ = True


class TestAuthenticatedPrecisionMeilisearch(_AuthenticatedPrecisionTests):
    __test__ = True
    provider = "meilisearch"
