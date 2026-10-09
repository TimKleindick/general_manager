from __future__ import annotations

from typing import ClassVar
from types import SimpleNamespace
from uuid import UUID, uuid4

import graphene
from django.contrib.auth import get_user_model
from django.db import models
from django.test import override_settings
from graphql import Undefined, get_named_type

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_search import normalize_filter_input
from general_manager.interface import (
    CalculationInterface,
    DatabaseInterface,
    RequestInterface,
)
from general_manager.interface.requests import RequestField, RequestQueryOperation
from general_manager.interface.base_interface import InvalidInputConstraintError
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase

DEFAULT_TOKEN_ID = UUID("00000000-0000-0000-0000-00000000ab01")


class IdentityPublicPermission(ManagerBasedPermission):
    __read__: ClassVar[list[str]] = ["public"]
    __create__: ClassVar[list[str]] = ["public"]
    __update__: ClassVar[list[str]] = ["public"]
    __delete__: ClassVar[list[str]] = ["public"]


class GraphQLIdentityScalarIntegrationTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        uuid_default_calls: list[UUID] = []
        relation_default_calls: list[UUID] = []
        optional_business_calls: list[tuple[str, dict[str, object]]] = []

        def default_identity() -> UUID:
            value = uuid4()
            uuid_default_calls.append(value)
            return value

        def default_token_identity() -> UUID:
            relation_default_calls.append(DEFAULT_TOKEN_ID)
            return DEFAULT_TOKEN_ID

        class IdentityAutoRecord(GeneralManager):
            class Interface(DatabaseInterface):
                id = models.AutoField(primary_key=True)
                name = models.CharField(max_length=100)
                external_id = models.IntegerField(default=9)
                external_code = models.UUIDField(unique=True, default=uuid4)
                amount = models.IntegerField(default=7)
                big_amount = models.BigIntegerField(default=2**40)

            Permission = IdentityPublicPermission

        class IdentityBigRecord(GeneralManager):
            class Interface(DatabaseInterface):
                id = models.BigAutoField(primary_key=True)
                name = models.CharField(max_length=100)

            Permission = IdentityPublicPermission

        class IdentityStructuredRelationTarget(GeneralManager):
            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {
                    "id": Input(int),
                    "context": Input(
                        str, required=False, validator=lambda value: value == "tenant"
                    ),
                }
                id = models.BigAutoField(primary_key=True)
                code = models.UUIDField(unique=True, default=uuid4)
                name = models.CharField(max_length=100)

            Permission = IdentityPublicPermission

        class IdentityStructuredForeignKeySource(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                selection = models.ForeignKey(
                    "general_manager.IdentityStructuredRelationTarget",
                    on_delete=models.CASCADE,
                )

            Permission = IdentityPublicPermission

        class IdentityStructuredCollectionSource(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                members = models.ManyToManyField(
                    "general_manager.IdentityStructuredRelationTarget", blank=True
                )

            Permission = IdentityPublicPermission

        class IdentityStructuredCodedCollectionSource(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                members = models.ManyToManyField(
                    "general_manager.IdentityStructuredRelationTarget",
                    through="general_manager.IdentityStructuredCodedMembership",
                    related_name="coded_sources",
                    blank=True,
                )

            Permission = IdentityPublicPermission

        class IdentityStructuredCodedMembership(GeneralManager):
            class Interface(DatabaseInterface):
                source = models.ForeignKey(
                    "general_manager.IdentityStructuredCodedCollectionSource",
                    on_delete=models.CASCADE,
                )
                target = models.ForeignKey(
                    "general_manager.IdentityStructuredRelationTarget",
                    to_field="code",
                    on_delete=models.CASCADE,
                )

            Permission = IdentityPublicPermission

        class IdentityStructuredAlternateSource(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                selection = models.ForeignKey(
                    "general_manager.IdentityStructuredRelationTarget",
                    to_field="code",
                    on_delete=models.CASCADE,
                    related_name="alternate_sources",
                )

            Permission = IdentityPublicPermission

        class IdentityUuidRecord(GeneralManager):
            class Interface(DatabaseInterface):
                # ORM interfaces explicitly declare their public identifier type.
                input_fields: ClassVar[dict[str, Input]] = {"id": Input(UUID)}
                id = models.UUIDField(primary_key=True, default=default_identity)
                name = models.CharField(max_length=100)

            Permission = IdentityPublicPermission

        class IdentityCodeRecord(GeneralManager):
            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {"id": Input(str)}
                code = models.CharField(max_length=32, primary_key=True)
                name = models.CharField(max_length=100)

            Permission = IdentityPublicPermission

        class IdentityIntegerRecord(GeneralManager):
            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {
                    "id": Input(int, required=False)
                }
                id = models.IntegerField(primary_key=True)
                name = models.CharField(max_length=100)

            Permission = IdentityPublicPermission

        class IdentityBusinessIdRecord(GeneralManager):
            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {"id": Input(str)}
                code = models.CharField(primary_key=True, max_length=32)
                id = models.IntegerField(default=23)
                name = models.CharField(max_length=100)

            Permission = IdentityPublicPermission

        class IdentityDefaultRelation(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                owner = models.ForeignKey(
                    "general_manager.IdentityAutoRecord",
                    on_delete=models.CASCADE,
                    default=31,
                )
                token = models.ForeignKey(
                    "general_manager.IdentityUuidRecord",
                    on_delete=models.CASCADE,
                    default=default_token_identity,
                )

            Permission = IdentityPublicPermission

        class IdentityStringRecord(GeneralManager):
            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {"id": Input(str)}
                id = models.CharField(max_length=32, primary_key=True)
                name = models.CharField(max_length=100)

            Permission = IdentityPublicPermission

        class IdentityBigIntegerRecord(GeneralManager):
            class Interface(DatabaseInterface):
                id = models.BigIntegerField(primary_key=True)
                name = models.CharField(max_length=100)

            Permission = IdentityPublicPermission

        class IdentityOneToOneRecord(GeneralManager):
            class Interface(DatabaseInterface):
                owner = models.OneToOneField(
                    "general_manager.IdentityAutoRecord",
                    primary_key=True,
                    on_delete=models.CASCADE,
                )
                name = models.CharField(max_length=100)

            Permission = IdentityPublicPermission

        class IdentityAsset(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                owner = models.ForeignKey(
                    "general_manager.IdentityAutoRecord", on_delete=models.CASCADE
                )
                token = models.ForeignKey(
                    "general_manager.IdentityUuidRecord", on_delete=models.CASCADE
                )
                category = models.ForeignKey(
                    "general_manager.IdentityCodeRecord", on_delete=models.CASCADE
                )
                alternate = models.ForeignKey(
                    "general_manager.IdentityAutoRecord",
                    to_field="external_code",
                    null=True,
                    blank=True,
                    on_delete=models.CASCADE,
                    related_name="alternate_assets",
                )
                labels = models.ManyToManyField(
                    "general_manager.IdentityUuidRecord", blank=True
                )
                categories = models.ManyToManyField(
                    "general_manager.IdentityCodeRecord", blank=True
                )

            Permission = IdentityPublicPermission

        class IdentityCompositeCalculation(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(int)
                quantity = Input(int)
                period = Input(str)

            Permission = IdentityPublicPermission

        class IdentityOptionalBusinessId(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(int, required=False)
                account = Input(str)

            Permission = IdentityPublicPermission

            def update(self, **kwargs):
                optional_business_calls.append(("update", dict(self.identification)))
                return self

            def delete(self, **kwargs) -> None:
                optional_business_calls.append(("delete", dict(self.identification)))

        class IdentityCalculationSelection(GeneralManager):
            class Interface(CalculationInterface):
                selection = Input(IdentityCompositeCalculation)
                owner = Input(IdentityAutoRecord)

            Permission = IdentityPublicPermission

        class IdentityCompositeRequest(GeneralManager):
            class Interface(RequestInterface):
                id = Input(int)
                account = Input(str)
                year = Input(int)
                identification_fields = ("id", "account", "year")
                label = RequestField(str)

                class Meta:
                    query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                        "detail": RequestQueryOperation(
                            name="detail",
                            method="GET",
                            path="/accounts/{account}/{year}/{id}",
                        ),
                        "list": RequestQueryOperation(
                            name="list", method="GET", path="/accounts"
                        ),
                    }

            Permission = IdentityPublicPermission

        cls.AutoRecord = IdentityAutoRecord
        cls.BigRecord = IdentityBigRecord
        cls.StructuredTarget = IdentityStructuredRelationTarget
        cls.StructuredForeignKey = IdentityStructuredForeignKeySource
        cls.StructuredCollection = IdentityStructuredCollectionSource
        cls.StructuredCodedCollection = IdentityStructuredCodedCollectionSource
        cls.StructuredCodedMembership = IdentityStructuredCodedMembership
        cls.StructuredAlternate = IdentityStructuredAlternateSource
        cls.UuidRecord = IdentityUuidRecord
        cls.CodeRecord = IdentityCodeRecord
        cls.IntegerRecord = IdentityIntegerRecord
        cls.BusinessIdRecord = IdentityBusinessIdRecord
        cls.DefaultRelation = IdentityDefaultRelation
        cls.StringRecord = IdentityStringRecord
        cls.OneToOneRecord = IdentityOneToOneRecord
        cls.uuid_default_calls = uuid_default_calls
        cls.relation_default_calls = relation_default_calls
        cls.OptionalBusinessId = IdentityOptionalBusinessId
        cls.optional_business_calls = optional_business_calls
        cls.Asset = IdentityAsset
        cls.general_manager_classes = [
            IdentityAutoRecord,
            IdentityBigRecord,
            IdentityStructuredRelationTarget,
            IdentityStructuredForeignKeySource,
            IdentityStructuredCollectionSource,
            IdentityStructuredCodedCollectionSource,
            IdentityStructuredCodedMembership,
            IdentityStructuredAlternateSource,
            IdentityUuidRecord,
            IdentityCodeRecord,
            IdentityIntegerRecord,
            IdentityBusinessIdRecord,
            IdentityDefaultRelation,
            IdentityStringRecord,
            IdentityBigIntegerRecord,
            IdentityOneToOneRecord,
            IdentityAsset,
            IdentityCompositeCalculation,
            IdentityOptionalBusinessId,
            IdentityCalculationSelection,
            IdentityCompositeRequest,
        ]

    def setUp(self) -> None:
        super().setUp()
        self.user = get_user_model().objects.create_user(username="identity-tests")
        self.client.force_login(self.user)
        self.uuid_default_calls.clear()
        self.relation_default_calls.clear()
        self.optional_business_calls.clear()

    def _schema(self):
        schema = GraphQL.get_schema()
        assert schema is not None
        return schema

    def _filter_fields(self, query_name: str):
        field = self._schema().graphql_schema.query_type.fields[query_name]
        return get_named_type(field.args["filter"].type).fields

    def _assert_identifier_output(self, manager, query_name: str, pk: object) -> None:
        record = manager.Interface._model.objects.create(pk=pk, name="Selected")
        query = f"""
        query($id: ID!) {{
          {query_name}(id: $id) {{ id name }}
          {query_name}List {{ items {{ id name }} }}
        }}
        """
        response = self.query(query, variables={"id": str(record.pk)})
        self.assertResponseNoErrors(response)
        data = response.json()["data"]
        self.assertEqual(data[query_name], {"id": str(pk), "name": "Selected"})
        self.assertEqual(
            data[f"{query_name}List"]["items"],
            [{"id": str(pk), "name": "Selected"}],
        )

    def test_auto_primary_key_outputs_id_string(self) -> None:
        self._assert_identifier_output(self.AutoRecord, "identityAutoRecord", 31)

    def test_big_auto_primary_key_round_trips_above_graphql_int_limit(self) -> None:
        self._assert_identifier_output(
            self.BigRecord, "identityBigRecord", 2_147_483_648
        )

    def test_uuid_primary_key_has_id_output(self) -> None:
        self.assertEqual(
            str(
                self._schema()
                .graphql_schema.get_type("IdentityUuidRecordType")
                .fields["id"]
                .type
            ),
            "ID",
        )
        self._assert_identifier_output(self.UuidRecord, "identityUuidRecord", uuid4())

    def test_non_id_named_primary_key_is_id_and_round_trips(self) -> None:
        record = self.CodeRecord.Interface._model.objects.create(
            code="CAT-001", name="Category"
        )
        graphql_type = self._schema().graphql_schema.get_type("IdentityCodeRecordType")
        self.assertEqual(str(graphql_type.fields["code"].type), "ID")
        response = self.query(
            "query($id: ID!) { identityCodeRecord(id: $id) { code name } }",
            variables={"id": record.pk},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["identityCodeRecord"],
            {"code": "CAT-001", "name": "Category"},
        )

    def test_raw_foreign_key_outputs_are_id_and_relations_stay_objects(self) -> None:
        fields = self._schema().graphql_schema.get_type("IdentityAssetType").fields
        for name in ("ownerId", "tokenId", "categoryId"):
            with self.subTest(field=name):
                self.assertEqual(str(fields[name].type), "ID")
        self.assertEqual(
            get_named_type(fields["owner"].type).name, "IdentityAutoRecordType"
        )
        self.assertEqual(
            get_named_type(fields["token"].type).name, "IdentityUuidRecordType"
        )
        self.assertEqual(
            get_named_type(fields["category"].type).name, "IdentityCodeRecordType"
        )
        self.assertEqual(
            get_named_type(fields["labelsList"].type).name, "IdentityUuidRecordPage"
        )

    def test_identity_equality_filters_use_id_and_ranges_keep_native_types(
        self,
    ) -> None:
        for query, names in (
            ("identityAutoRecordList", ("id",)),
            ("identityBigRecordList", ("id",)),
            ("identityUuidRecordList", ("id",)),
            ("identityCodeRecordList", ("code",)),
            ("identityAssetList", ("ownerId", "tokenId", "categoryId")),
        ):
            fields = self._filter_fields(query)
            for name in names:
                with self.subTest(query=query, field=name):
                    self.assertIn(f"{name}_In", fields)
                    self.assertEqual(str(fields[name].type), "ID")
                    self.assertEqual(str(fields[f"{name}_Exact"].type), "ID")
                    self.assertEqual(str(fields[f"{name}_In"].type), "[ID]")
        self.assertEqual(
            str(self._filter_fields("identityAutoRecordList")["id_Gt"].type), "Int"
        )
        self.assertEqual(
            str(self._filter_fields("identityBigRecordList")["id_Gt"].type), "Int"
        )

    def test_equality_filter_variables_round_trip_integer_uuid_and_code_ids(
        self,
    ) -> None:
        for manager, query_name, field_name, pk in (
            (self.AutoRecord, "identityAutoRecordList", "id", 42),
            (self.BigRecord, "identityBigRecordList", "id", 2_147_483_648),
            (self.UuidRecord, "identityUuidRecordList", "id", uuid4()),
            (self.CodeRecord, "identityCodeRecordList", "code", "CAT-042"),
        ):
            manager.Interface._model.objects.create(pk=pk, name="Selected")
            selection = "code" if field_name == "code" else "id"
            query = f"""
            query($id: ID!, $ids: [ID]) {{
              eq: {query_name}(filter: {{{field_name}: $id}}) {{ items {{ {selection} }} }}
              exact: {query_name}(filter: {{{field_name}_Exact: $id}}) {{ items {{ {selection} }} }}
              inside: {query_name}(filter: {{{field_name}_In: $ids}}) {{ items {{ {selection} }} }}
            }}
            """
            response = self.query(query, variables={"id": str(pk), "ids": [str(pk)]})
            with self.subTest(manager=manager.__name__):
                self.assertResponseNoErrors(response)
                for result in response.json()["data"].values():
                    self.assertEqual(result["items"], [{selection: str(pk)}])

    def test_raw_foreign_key_filter_values_normalize_to_python_types(self) -> None:
        token_id = uuid4()
        filters = normalize_filter_input(
            self.Asset,
            {
                "owner_id": "42",
                "owner_id__exact": "42",
                "owner_id__in": ["42", "43"],
                "token_id": str(token_id),
                "token_id__exact": str(token_id),
                "token_id__in": [str(token_id)],
                "category_id": "CAT-042",
                "category_id__in": ["CAT-042"],
                "owner_id__gt": "40",
            },
        )["filter"]
        self.assertEqual(filters["owner_id"], 42)
        self.assertEqual(filters["owner_id__exact"], 42)
        self.assertEqual(filters["owner_id__in"], [42, 43])
        self.assertEqual(filters["token_id"], token_id)
        self.assertIsInstance(filters["token_id"], UUID)
        self.assertEqual(filters["token_id__exact"], token_id)
        self.assertEqual(filters["token_id__in"], [token_id])
        self.assertEqual(filters["category_id"], "CAT-042")
        self.assertEqual(filters["category_id__in"], ["CAT-042"])
        self.assertEqual(filters["owner_id__gt"], "40")

    def test_raw_foreign_key_equality_filters_accept_id_variables(self) -> None:
        owner = self.AutoRecord.Interface._model.objects.create(name="Owner")
        token = self.UuidRecord.Interface._model.objects.create(name="Token")
        category = self.CodeRecord.Interface._model.objects.create(
            code="CAT-FILTER", name="Category"
        )
        self.Asset.Interface._model.objects.create(
            name="Selected", owner=owner, token=token, category=category
        )
        query = """
        query($owner: ID!, $owners: [ID], $token: ID!, $tokens: [ID],
              $category: ID!, $categories: [ID]) {
          owner: identityAssetList(filter: {ownerId: $owner}) { items { name } }
          ownerExact: identityAssetList(filter: {ownerId_Exact: $owner}) { items { name } }
          owners: identityAssetList(filter: {ownerId_In: $owners}) { items { name } }
          token: identityAssetList(filter: {tokenId: $token}) { items { name } }
          tokenExact: identityAssetList(filter: {tokenId_Exact: $token}) { items { name } }
          tokens: identityAssetList(filter: {tokenId_In: $tokens}) { items { name } }
          category: identityAssetList(filter: {categoryId: $category}) { items { name } }
          categoryExact: identityAssetList(filter: {categoryId_Exact: $category}) { items { name } }
          categories: identityAssetList(filter: {categoryId_In: $categories}) { items { name } }
        }
        """
        response = self.query(
            query,
            variables={
                "owner": str(owner.pk),
                "owners": [str(owner.pk)],
                "token": str(token.pk),
                "tokens": [str(token.pk)],
                "category": category.pk,
                "categories": [category.pk],
            },
        )
        self.assertResponseNoErrors(response)
        for result in response.json()["data"].values():
            self.assertEqual(result["items"], [{"name": "Selected"}])

    def test_declared_and_non_id_primary_key_filter_values_use_native_python_types(
        self,
    ) -> None:
        token_id = uuid4()
        for manager, key, value, expected in (
            (self.AutoRecord, "id", "42", 42),
            (self.UuidRecord, "id", str(token_id), token_id),
            (self.CodeRecord, "code", 42, "42"),
        ):
            with self.subTest(manager=manager.__name__):
                filters = normalize_filter_input(
                    manager,
                    {key: value, f"{key}__exact": value, f"{key}__in": [value]},
                )["filter"]
                self.assertEqual(filters[key], expected)
                self.assertEqual(filters[f"{key}__exact"], expected)
                self.assertEqual(filters[f"{key}__in"], [expected])

    def test_relation_mutations_accept_id_variables_and_persist_native_keys(
        self,
    ) -> None:
        owner = self.AutoRecord.Interface._model.objects.create(name="Owner")
        token = self.UuidRecord.Interface._model.objects.create(name="Token")
        category = self.CodeRecord.Interface._model.objects.create(
            code="CAT-A", name="Category"
        )
        label = self.UuidRecord.Interface._model.objects.create(name="Label")
        response = self.query(
            """
            mutation($owner: ID!, $token: ID!, $category: ID!, $labels: [ID], $categories: [ID]) {
              createIdentityAsset(name: "Asset", owner: $owner, token: $token,
                                  category: $category, labelsList: $labels, categoriesList: $categories) {
                success
                IdentityAsset {
                  id ownerId tokenId categoryId
                  owner { id name } token { id name } category { code name }
                  labelsList { items { id name } }
                }
              }
            }
            """,
            variables={
                "owner": str(owner.pk),
                "token": str(token.pk),
                "category": category.pk,
                "labels": [str(label.pk)],
                "categories": [category.pk],
            },
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["createIdentityAsset"]
        self.assertTrue(payload["success"])
        asset = self.Asset.Interface._model.objects.get(
            pk=payload["IdentityAsset"]["id"]
        )
        self.assertIsInstance(asset.owner_id, int)
        self.assertEqual(asset.token_id, token.pk)
        self.assertIsInstance(asset.token_id, UUID)
        self.assertEqual(asset.category_id, category.pk)
        self.assertEqual(list(asset.labels.values_list("pk", flat=True)), [label.pk])
        self.assertEqual(
            list(asset.categories.values_list("pk", flat=True)), [category.pk]
        )
        self.assertEqual(payload["IdentityAsset"]["ownerId"], str(owner.pk))
        self.assertEqual(payload["IdentityAsset"]["tokenId"], str(token.pk))
        self.assertEqual(payload["IdentityAsset"]["categoryId"], category.pk)

    def test_mutation_identity_arguments_cannot_be_overwritten_by_write_fields(
        self,
    ) -> None:
        mutation_fields = self._schema().graphql_schema.mutation_type.fields
        for name in (
            "IdentityAutoRecord",
            "IdentityBigRecord",
            "IdentityUuidRecord",
            "IdentityCodeRecord",
            "IdentityIntegerRecord",
            "IdentityStringRecord",
            "IdentityOneToOneRecord",
        ):
            for operation in ("update", "delete"):
                with self.subTest(manager=name, operation=operation):
                    identifier = mutation_fields[f"{operation}{name}"].args["id"]
                    self.assertEqual(str(identifier.type), "ID!")
                    self.assertIs(identifier.default_value, Undefined)
        self.assertNotIn("id", mutation_fields["createIdentityAutoRecord"].args)
        self.assertNotIn("id", mutation_fields["createIdentityBigRecord"].args)
        uuid_id = mutation_fields["createIdentityUuidRecord"].args["id"]
        self.assertEqual(str(uuid_id.type), "ID")
        self.assertIs(uuid_id.default_value, Undefined)
        self.assertEqual(
            str(mutation_fields["createIdentityCodeRecord"].args["code"].type), "ID!"
        )

    def test_update_and_delete_accept_large_id_variables(self) -> None:
        pk = 2_147_483_648
        self.BigRecord.Interface._model.objects.create(pk=pk, name="Before")
        response = self.query(
            """
            mutation($id: ID!) {
              updateIdentityBigRecord(id: $id, name: "After") {
                success IdentityBigRecord { id name }
              }
            }
            """,
            variables={"id": str(pk)},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["updateIdentityBigRecord"]["IdentityBigRecord"],
            {"id": str(pk), "name": "After"},
        )
        self.assertEqual(
            self.BigRecord.Interface._model.objects.get(pk=pk).name, "After"
        )
        response = self.query(
            "mutation($id: ID!) { deleteIdentityBigRecord(id: $id) { success } }",
            variables={"id": str(pk)},
        )
        self.assertResponseNoErrors(response)
        self.assertTrue(response.json()["data"]["deleteIdentityBigRecord"]["success"])

    def test_uuid_and_non_id_primary_key_mutations_accept_id_variables(self) -> None:
        for manager, suffix, pk, selection in (
            (self.UuidRecord, "IdentityUuidRecord", uuid4(), "id"),
            (self.CodeRecord, "IdentityCodeRecord", "CAT-UPDATE", "code"),
        ):
            manager.Interface._model.objects.create(pk=pk, name="Before")
            response = self.query(
                f"""
                mutation($id: ID!) {{
                  update{suffix}(id: $id, name: "After") {{
                    success {suffix} {{ {selection} name }}
                  }}
                }}
                """,
                variables={"id": str(pk)},
            )
            with self.subTest(manager=suffix, operation="update"):
                self.assertResponseNoErrors(response)
                self.assertEqual(
                    response.json()["data"][f"update{suffix}"][suffix],
                    {selection: str(pk), "name": "After"},
                )
                self.assertEqual(
                    manager.Interface._model.objects.get(pk=pk).name, "After"
                )
            response = self.query(
                f"mutation($id: ID!) {{ delete{suffix}(id: $id) {{ success }} }}",
                variables={"id": str(pk)},
            )
            with self.subTest(manager=suffix, operation="delete"):
                self.assertResponseNoErrors(response)
                self.assertTrue(response.json()["data"][f"delete{suffix}"]["success"])

    def test_create_generated_and_explicit_primary_keys(self) -> None:
        for suffix in ("IdentityAutoRecord", "IdentityBigRecord", "IdentityUuidRecord"):
            with self.subTest(manager=suffix):
                response = self.query(
                    f'mutation {{ create{suffix}(name: "Created") {{ success {suffix} {{ id }} }} }}'
                )
                self.assertResponseNoErrors(response)
                payload = response.json()["data"][f"create{suffix}"]
                self.assertTrue(payload["success"])
                self.assertIsInstance(payload[suffix]["id"], str)
        response = self.query(
            """
            mutation($code: ID!) {
              createIdentityCodeRecord(code: $code, name: "Created") {
                success IdentityCodeRecord { code name }
              }
            }
            """,
            variables={"code": "CAT-CREATE"},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["createIdentityCodeRecord"]["IdentityCodeRecord"],
            {"code": "CAT-CREATE", "name": "Created"},
        )

    def test_plain_numbers_and_composite_calculation_inputs_stay_native(self) -> None:
        schema = self._schema().graphql_schema
        fields = schema.get_type("IdentityAutoRecordType").fields
        self.assertEqual(str(fields["externalId"].type), "Int")
        self.assertEqual(str(fields["amount"].type), "Int")
        self.assertEqual(str(fields["bigAmount"].type), "BigIntScalar")
        args = schema.query_type.fields["identityCompositeCalculation"].args
        self.assertEqual(str(args["id"].type), "Int!")
        self.assertEqual(str(args["quantity"].type), "Int!")
        self.assertEqual(str(args["period"].type), "String!")
        request_args = schema.query_type.fields["identityCompositeRequest"].args
        self.assertEqual(str(request_args["id"].type), "Int!")
        self.assertEqual(str(request_args["account"].type), "String!")
        self.assertEqual(str(request_args["year"].type), "Int!")
        native_filters = self._filter_fields("identityAutoRecordList")
        self.assertEqual(str(native_filters["externalId_Exact"].type), "Int")

    def test_manual_primary_keys_named_id_are_create_arguments_and_persist(
        self,
    ) -> None:
        for manager, suffix, pk in (
            (self.IntegerRecord, "IdentityIntegerRecord", 2_147_483_648),
            (self.StringRecord, "IdentityStringRecord", "STRING-PK"),
            (self.UuidRecord, "IdentityUuidRecord", uuid4()),
        ):
            with self.subTest(manager=suffix):
                field = self._schema().graphql_schema.mutation_type.fields[
                    f"create{suffix}"
                ]
                self.assertIn("id", field.args)
                expected_type = "ID" if manager is self.UuidRecord else "ID!"
                self.assertEqual(str(field.args["id"].type), expected_type)
                response = self.query(
                    f'mutation($id: ID!) {{ create{suffix}(id: $id, name: "Manual") {{ success {suffix} {{ id name }} }} }}',
                    variables={"id": str(pk)},
                )
                self.assertResponseNoErrors(response)
                self.assertEqual(
                    response.json()["data"][f"create{suffix}"][suffix],
                    {"id": str(pk), "name": "Manual"},
                )
                self.assertEqual(
                    manager.Interface._model.objects.get(pk=pk).name, "Manual"
                )

    def test_big_integer_primary_key_range_filters_keep_bigint_scalar(self) -> None:
        fields = self._filter_fields("identityBigIntegerRecordList")
        self.assertEqual(str(fields["id"].type), "ID")
        for suffix in ("Gt", "Gte", "Lt", "Lte"):
            self.assertEqual(str(fields[f"id_{suffix}"].type), "BigIntScalar")

    def test_primary_one_to_one_relation_preserves_object_and_raw_id(self) -> None:
        owner = self.AutoRecord.Interface._model.objects.create(name="Owner")
        self.OneToOneRecord.Interface._model.objects.create(owner=owner, name="Detail")
        fields = (
            self._schema().graphql_schema.get_type("IdentityOneToOneRecordType").fields
        )
        self.assertEqual(
            get_named_type(fields["owner"].type).name, "IdentityAutoRecordType"
        )
        self.assertEqual(str(fields["ownerId"].type), "ID")
        response = self.query(
            """
            query($id: ID!) {
              identityOneToOneRecord(id: $id) {
                name ownerId owner { id name }
              }
            }
            """,
            variables={"id": str(owner.pk)},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["identityOneToOneRecord"],
            {
                "name": "Detail",
                "ownerId": str(owner.pk),
                "owner": {"id": str(owner.pk), "name": "Owner"},
            },
        )

    def test_fk_to_field_uses_target_field_type_for_raw_id_normalization(self) -> None:
        external_code = uuid4()
        filters = normalize_filter_input(
            self.Asset,
            {
                "alternate_id": str(external_code),
                "alternate_id__exact": str(external_code),
                "alternate_id__in": [str(external_code)],
            },
        )["filter"]
        self.assertEqual(filters["alternate_id"], external_code)
        self.assertIsInstance(filters["alternate_id"], UUID)
        self.assertEqual(filters["alternate_id__exact"], external_code)
        self.assertEqual(filters["alternate_id__in"], [external_code])
        owner = self.AutoRecord.Interface._model.objects.create(
            name="Owner", external_code=external_code
        )
        token = self.UuidRecord.Interface._model.objects.create(name="Token")
        category = self.CodeRecord.Interface._model.objects.create(
            code="CAT-ALTERNATE", name="Category"
        )
        self.Asset.Interface._model.objects.create(
            name="Selected",
            owner=owner,
            token=token,
            category=category,
            alternate=owner,
        )
        response = self.query(
            """
            query($alternate: ID!, $alternates: [ID]) {
              eq: identityAssetList(filter: {alternateId: $alternate}) {
                items { name alternateId }
              }
              exact: identityAssetList(filter: {alternateId_Exact: $alternate}) {
                items { name alternateId }
              }
              inside: identityAssetList(filter: {alternateId_In: $alternates}) {
                items { name alternateId }
              }
            }
            """,
            variables={
                "alternate": str(external_code),
                "alternates": [str(external_code)],
            },
        )
        self.assertResponseNoErrors(response)
        for result in response.json()["data"].values():
            self.assertEqual(
                result["items"],
                [{"name": "Selected", "alternateId": str(external_code)}],
            )

    def test_callable_uuid_default_runs_at_create_time_and_manual_id_persists(
        self,
    ) -> None:
        schema = self._schema()
        str(schema)
        schema.introspect()
        self.assertEqual(self.uuid_default_calls, [])
        response = self.query(
            'mutation { createIdentityUuidRecord(name: "Generated") { IdentityUuidRecord { id } } }'
        )
        self.assertResponseNoErrors(response)
        generated_id = response.json()["data"]["createIdentityUuidRecord"][
            "IdentityUuidRecord"
        ]["id"]
        self.assertEqual(self.uuid_default_calls, [UUID(generated_id)])
        manual_id = uuid4()
        response = self.query(
            'mutation($id: ID!) { createIdentityUuidRecord(id: $id, name: "Manual") { IdentityUuidRecord { id } } }',
            variables={"id": str(manual_id)},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["createIdentityUuidRecord"]["IdentityUuidRecord"][
                "id"
            ],
            str(manual_id),
        )

    def test_composite_manager_reference_uses_structured_identification_input(
        self,
    ) -> None:
        args = (
            self._schema()
            .graphql_schema.query_type.fields["identityCalculationSelection"]
            .args
        )
        self.assertIn("selectionId", args)
        selection_fields = get_named_type(args["selectionId"].type).fields
        self.assertEqual(str(selection_fields["id"].type), "Int!")
        self.assertEqual(str(selection_fields["quantity"].type), "Int!")
        self.assertEqual(str(selection_fields["period"].type), "String!")
        self.assertEqual(str(args["ownerId"].type), "ID!")
        owner = self.AutoRecord.Interface._model.objects.create(name="Owner")
        response = self.query(
            """
            query($owner: ID!) {
              identityCalculationSelection(
                selectionId: {id: 7, quantity: 2, period: "week"}, ownerId: $owner
              ) {
                selection { id quantity period }
                owner { id name }
              }
            }
            """,
            variables={"owner": str(owner.pk)},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["identityCalculationSelection"],
            {
                "selection": {"id": 7, "quantity": 2, "period": "week"},
                "owner": {"id": str(owner.pk), "name": "Owner"},
            },
        )

    def test_schema_sdl_and_introspection_do_not_serialize_sentinel_or_callable_defaults(
        self,
    ) -> None:
        schema = self._schema()
        sdl = str(schema)
        self.assertIn("createIdentityAutoRecord", sdl)
        self.assertNotIn("NOT_PROVIDED", sdl)
        result = schema.introspect()
        self.assertIn("__schema", result)

    def test_generated_crud_preserves_optional_business_id_constructor_contract(
        self,
    ) -> None:
        instance = self.OptionalBusinessId(account="account")
        self.assertEqual(instance.identification, {"id": None, "account": "account"})
        update = GraphQL.generate_update_mutation_class(
            self.OptionalBusinessId, {"success": graphene.Boolean()}
        )
        delete = GraphQL.generate_delete_mutation_class(
            self.OptionalBusinessId, {"success": graphene.Boolean()}
        )
        assert update is not None
        assert delete is not None
        query_type = type(
            "OptionalBusinessCrudQuery",
            (graphene.ObjectType,),
            {"value": graphene.String()},
        )
        mutation_type = type(
            "OptionalBusinessCrudMutation",
            (graphene.ObjectType,),
            {
                "updateIdentityOptionalBusinessId": update.Field(),
                "deleteIdentityOptionalBusinessId": delete.Field(),
            },
        )
        schema = graphene.Schema(query=query_type, mutation=mutation_type)
        mutation_fields = schema.graphql_schema.mutation_type.fields
        for operation in ("update", "delete"):
            arguments = mutation_fields[f"{operation}IdentityOptionalBusinessId"].args
            self.assertEqual(str(arguments["id"].type), "Int")
            self.assertEqual(str(arguments["account"].type), "String!")
            self.assertEqual(
                str(
                    self._schema()
                    .graphql_schema.mutation_type.fields[
                        f"{operation}IdentityIntegerRecord"
                    ]
                    .args["id"]
                    .type
                ),
                "ID!",
            )
            for id_argument in ("", "id: null,"):
                with self.subTest(operation=operation, id_argument=id_argument):
                    self.optional_business_calls.clear()
                    result = schema.execute(
                        f'mutation {{ {operation}IdentityOptionalBusinessId({id_argument} account: "account") {{ success }} }}',
                        context_value=SimpleNamespace(user=self.user),
                    )
                    self.assertIsNone(result.errors)
                    self.assertTrue(
                        result.data[f"{operation}IdentityOptionalBusinessId"]["success"]
                    )
                    self.assertEqual(
                        self.optional_business_calls,
                        [(operation, {"id": None, "account": "account"})],
                    )

    def test_string_primary_key_pattern_filters_keep_native_string_inputs(self) -> None:
        self.CodeRecord.Interface._model.objects.create(code="CAT-001", name="Selected")
        self.CodeRecord.Interface._model.objects.create(code="DOG-999", name="Other")
        fields = self._filter_fields("identityCodeRecordList")
        for operator in ("Contains", "Icontains", "Startswith", "Endswith"):
            self.assertIn(f"code_{operator}", fields)
            self.assertEqual(str(fields[f"code_{operator}"].type), "String")
        response = self.query(
            """
            query($contains: String!, $icontains: String!, $prefix: String!, $suffix: String!) {
              contains: identityCodeRecordList(filter: {code_Contains: $contains}) { items { code } }
              icontains: identityCodeRecordList(filter: {code_Icontains: $icontains}) { items { code } }
              startswith: identityCodeRecordList(filter: {code_Startswith: $prefix}) { items { code } }
              endswith: identityCodeRecordList(filter: {code_Endswith: $suffix}) { items { code } }
            }
            """,
            variables={
                "contains": "AT-",
                "icontains": "cat-",
                "prefix": "CAT",
                "suffix": "001",
            },
        )
        self.assertResponseNoErrors(response)
        for result in response.json()["data"].values():
            self.assertEqual(result["items"], [{"code": "CAT-001"}])

    def test_ordinary_id_field_with_non_id_primary_key_stays_native(self) -> None:
        self.BusinessIdRecord.Interface._model.objects.create(
            code="BUSINESS", id=23, name="Selected"
        )
        schema = self._schema().graphql_schema
        fields = schema.get_type("IdentityBusinessIdRecordType").fields
        self.assertEqual(str(fields["id"].type), "Int")
        self.assertEqual(str(fields["code"].type), "ID")
        filters = self._filter_fields("identityBusinessIdRecordList")
        self.assertEqual(str(filters["id"].type), "Int")
        self.assertEqual(str(filters["id_Exact"].type), "Int")
        values = {"id": "23", "id__exact": "23", "id__in": ["23"]}
        self.assertEqual(
            normalize_filter_input(self.BusinessIdRecord, values)["filter"], values
        )
        response = self.query(
            """
            query($pk: ID!, $number: Int!) {
              detail: identityBusinessIdRecord(id: $pk) { code id name }
              filtered: identityBusinessIdRecordList(filter: {id_Exact: $number}) { items { code id name } }
            }
            """,
            variables={"pk": "BUSINESS", "number": 23},
        )
        self.assertResponseNoErrors(response)
        expected = {"code": "BUSINESS", "id": 23, "name": "Selected"}
        self.assertEqual(response.json()["data"]["detail"], expected)
        self.assertEqual(response.json()["data"]["filtered"]["items"], [expected])

    def test_malformed_uuid_and_overlong_string_primary_keys_fail_without_writes(
        self,
    ) -> None:
        for manager, suffix, invalid_id in (
            (self.UuidRecord, "IdentityUuidRecord", "not-a-uuid"),
            (self.StringRecord, "IdentityStringRecord", "x" * 33),
        ):
            with self.subTest(manager=suffix):
                response = self.query(
                    f'mutation($id: ID!) {{ create{suffix}(id: $id, name: "Invalid") {{ success }} }}',
                    variables={"id": invalid_id},
                )
                self.assertResponseHasErrors(response)
                errors = response.json()["errors"]
                self.assertEqual(errors[0]["extensions"]["code"], "BAD_USER_INPUT")
                self.assertIn("id", errors[0]["extensions"]["fieldErrors"])
                self.assertEqual(manager.Interface._model.objects.count(), 0)

    def test_required_manual_primary_key_create_rejects_missing_or_null_id(
        self,
    ) -> None:
        for manager, suffix in (
            (self.IntegerRecord, "IdentityIntegerRecord"),
            (self.StringRecord, "IdentityStringRecord"),
        ):
            for argument in ("", "id: null,"):
                with self.subTest(manager=suffix, argument=argument):
                    response = self.query(
                        f'mutation {{ create{suffix}({argument} name: "Invalid") {{ success }} }}'
                    )
                    self.assertResponseHasErrors(response)
                    self.assertNotIn("data", response.json())
                    self.assertEqual(manager.Interface._model.objects.count(), 0)

    def test_null_update_target_is_rejected_without_changing_record(self) -> None:
        self.IntegerRecord.Interface._model.objects.create(pk=42, name="Before")
        response = self.query(
            'mutation($id: ID!) { updateIdentityIntegerRecord(id: $id, name: "After") { success } }',
            variables={"id": None},
        )
        self.assertResponseHasErrors(response)
        self.assertIsNone(response.json().get("data"))
        self.assertEqual(
            self.IntegerRecord.Interface._model.objects.get(pk=42).name, "Before"
        )

    def test_defaulted_foreign_keys_have_optional_safe_arguments(self) -> None:
        schema = self._schema()
        arguments = schema.graphql_schema.mutation_type.fields[
            "createIdentityDefaultRelation"
        ].args
        self.assertEqual(str(arguments["owner"].type), "ID")
        self.assertEqual(arguments["owner"].default_value, 31)
        self.assertEqual(str(arguments["token"].type), "ID")
        self.assertIs(arguments["token"].default_value, Undefined)
        self.assertEqual(self.relation_default_calls, [])
        self.assertIn("createIdentityDefaultRelation", str(schema))
        self.assertIn("__schema", schema.introspect())
        self.assertEqual(self.relation_default_calls, [])

    def test_omitted_defaulted_foreign_keys_use_real_target_ids_at_create_time(
        self,
    ) -> None:
        self.AutoRecord.Interface._model.objects.create(pk=31, name="Default owner")
        self.UuidRecord.Interface._model.objects.create(
            pk=DEFAULT_TOKEN_ID, name="Default token"
        )
        self.relation_default_calls.clear()
        response = self.query(
            """
            mutation {
              createIdentityDefaultRelation(name: "Defaults") {
                success IdentityDefaultRelation { ownerId tokenId }
              }
            }
            """
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["createIdentityDefaultRelation"]
        self.assertTrue(payload["success"])
        self.assertEqual(
            payload["IdentityDefaultRelation"],
            {"ownerId": "31", "tokenId": str(DEFAULT_TOKEN_ID)},
        )
        record = self.DefaultRelation.Interface._model.objects.get()
        self.assertEqual(record.owner_id, 31)
        self.assertEqual(record.token_id, DEFAULT_TOKEN_ID)
        self.assertIsInstance(record.token_id, UUID)
        self.assertEqual(self.relation_default_calls, [DEFAULT_TOKEN_ID])

    def _structured_relation_targets(self):
        return [
            self.StructuredTarget.create(
                id=2_147_483_648 + offset,
                name=f"Target {offset}",
                code=uuid4(),
                creator_id=self.user.id,
                ignore_permission=True,
            )
            for offset in (0, 1)
        ]

    def _assert_structured_relation_mutations(
        self, source, *, collection=False, alternate=False, existing=False
    ) -> None:
        first, second = self._structured_relation_targets()
        suffix = source.__name__
        argument = "membersList" if collection else "selection"
        reference = '{id: $target, context: "tenant"}'
        if collection:
            reference = f"[{reference}]"
        if existing:
            values = (
                {}
                if collection
                else {"selection_id": first.code if alternate else first.id}
            )
            row = source.Interface._model.objects.create(name="Before", **values)
            if collection:
                row.members.add(first.id)
        else:
            response = self.query(
                f"""
                mutation($target: ID!) {{
                  create{suffix}(name: "Created", {argument}: {reference}) {{
                    success {suffix} {{ id }}
                  }}
                }}
                """,
                variables={"target": str(first.id)},
            )
            self.assertResponseNoErrors(response)
            payload = response.json()["data"][f"create{suffix}"]
            self.assertTrue(payload["success"])
            row = source.Interface._model.objects.get(pk=payload[suffix]["id"])
        if collection:
            values = list(row.members.values_list("pk", flat=True))
            self.assertEqual(values, [first.id])
            self.assertTrue(all(isinstance(value, int) for value in values))
        else:
            self.assertEqual(row.selection_id, first.code if alternate else first.id)
            self.assertIsInstance(row.selection_id, UUID if alternate else int)

        response = self.query(
            f"""
            mutation($id: ID!, $target: ID!) {{
              update{suffix}(id: $id, name: "Updated", {argument}: {reference}) {{
                success {suffix} {{ id }}
              }}
            }}
            """,
            variables={"id": str(row.pk), "target": str(second.id)},
        )
        self.assertResponseNoErrors(response)
        self.assertTrue(response.json()["data"][f"update{suffix}"]["success"])
        row.refresh_from_db()
        self.assertEqual(row.name, "Updated")
        if collection:
            values = list(row.members.values_list("pk", flat=True))
            self.assertEqual(values, [second.id])
            self.assertTrue(all(isinstance(value, int) for value in values))
        else:
            self.assertEqual(row.selection_id, second.code if alternate else second.id)
            self.assertIsInstance(row.selection_id, UUID if alternate else int)

    def test_structured_orm_foreign_key_mutations_persist_native_primary_keys(
        self,
    ) -> None:
        self._assert_structured_relation_mutations(self.StructuredForeignKey)

    def test_structured_orm_collection_mutations_persist_native_primary_keys(
        self,
    ) -> None:
        self._assert_structured_relation_mutations(
            self.StructuredCollection, collection=True
        )

    def test_structured_orm_to_field_mutations_persist_actual_target_field_values(
        self,
    ) -> None:
        self._assert_structured_relation_mutations(
            self.StructuredAlternate, alternate=True
        )

    def test_structured_orm_foreign_key_update_persists_native_primary_key(
        self,
    ) -> None:
        self._assert_structured_relation_mutations(
            self.StructuredForeignKey, existing=True
        )

    def test_structured_orm_collection_update_persists_native_primary_keys(
        self,
    ) -> None:
        self._assert_structured_relation_mutations(
            self.StructuredCollection, collection=True, existing=True
        )

    def _assert_coded_collection_membership(self, record_id, target) -> None:
        row = self.StructuredCodedCollection.Interface._model.objects.get(pk=record_id)
        membership = self.StructuredCodedMembership.Interface._model.objects.get(
            source_id=record_id
        )
        self.assertEqual(membership.target_id, target.code)
        self.assertIsInstance(membership.target_id, UUID)
        self.assertEqual(list(row.members.values_list("pk", flat=True)), [target.id])

    def test_structured_orm_through_collection_create_persists_target_field_value(
        self,
    ) -> None:
        target, _ = self._structured_relation_targets()
        control = self.StructuredCodedCollection.create(
            name="Direct",
            members_id_list=[target.code],
            creator_id=self.user.id,
        )
        self._assert_coded_collection_membership(control.id, target)

        response = self.query(
            """
            mutation($target: ID!) {
              createIdentityStructuredCodedCollectionSource(
                name: "Created", membersList: [{id: $target, context: "tenant"}]
              ) { success IdentityStructuredCodedCollectionSource { id } }
            }
            """,
            variables={"target": str(target.id)},
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"][
            "createIdentityStructuredCodedCollectionSource"
        ]
        self.assertTrue(payload["success"])
        self._assert_coded_collection_membership(
            payload["IdentityStructuredCodedCollectionSource"]["id"], target
        )

    def test_structured_orm_through_collection_update_persists_target_field_value(
        self,
    ) -> None:
        first, second = self._structured_relation_targets()
        source = self.StructuredCodedCollection.create(
            name="Before",
            members_id_list=[first.code],
            creator_id=self.user.id,
        )
        self._assert_coded_collection_membership(source.id, first)

        response = self.query(
            """
            mutation($id: ID!, $target: ID!) {
              updateIdentityStructuredCodedCollectionSource(
                id: $id, name: "Updated",
                membersList: [{id: $target, context: "tenant"}]
              ) { success IdentityStructuredCodedCollectionSource { id name } }
            }
            """,
            variables={"id": str(source.id), "target": str(second.id)},
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"][
            "updateIdentityStructuredCodedCollectionSource"
        ]
        self.assertTrue(payload["success"])
        self.assertEqual(
            payload["IdentityStructuredCodedCollectionSource"]["name"], "Updated"
        )
        self._assert_coded_collection_membership(source.id, second)

    def test_structured_orm_to_field_update_persists_actual_target_field_value(
        self,
    ) -> None:
        self._assert_structured_relation_mutations(
            self.StructuredAlternate, alternate=True, existing=True
        )

    def test_canonical_structured_alternate_relation_uses_related_primary_key(
        self,
    ) -> None:
        target, _ = self._structured_relation_targets()
        source = self.StructuredAlternate.Interface._model.objects.create(
            name="Alternative", selection_id=target.code
        )
        response = self.query(
            """
            query($id: ID!) {
              identityStructuredAlternateSource(id: $id) {
                name selectionId selection { id code name }
              }
            }
            """,
            variables={"id": str(source.pk)},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["identityStructuredAlternateSource"],
            {
                "name": "Alternative",
                "selectionId": str(target.code),
                "selection": {
                    "id": str(target.id),
                    "code": str(target.code),
                    "name": target.name,
                },
            },
        )

    @override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
    def test_structured_relation_mutations_preserve_constructor_context_validation(
        self,
    ) -> None:
        target, _ = self._structured_relation_targets()
        self.assertEqual(
            self.StructuredTarget(id=target.id, context="tenant").id, target.id
        )
        with self.assertRaises(InvalidInputConstraintError):
            self.StructuredTarget(id=target.id, context="invalid")
        response = self.query(
            """
            mutation($target: ID!) {
              createIdentityStructuredForeignKeySource(
                name: "Invalid", selection: {id: $target, context: "invalid"}
              ) { success }
            }
            """,
            variables={"target": str(target.id)},
        )
        self.assertResponseHasErrors(response)
        self.assertEqual(self.StructuredForeignKey.Interface._model.objects.count(), 0)
