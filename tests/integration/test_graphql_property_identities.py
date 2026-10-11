from __future__ import annotations

from typing import Annotated, ClassVar
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.db import models
from graphql import get_named_type

from general_manager.api.graphql import GraphQL
from general_manager.api.property import graph_ql_property
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input
from general_manager.permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class PropertyIdentityPermission(ManagerBasedPermission):
    __read__: ClassVar[list[str]] = ["public"]
    restricted_key: ClassVar[dict[str, list[str]]] = {"read": ["isAdmin"]}


def integer_key(record) -> int:
    return record.id


def integer_keys(record) -> list[int]:
    return list(type(record).filter(id=record.id).values_list("id", flat=True))


class GraphQLPropertyIdentityTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class PropertyBigRecord(GeneralManager):
            class Interface(DatabaseInterface):
                id = models.BigAutoField(primary_key=True)
                sequence = models.IntegerField(default=7)

            Permission = PropertyIdentityPermission
            record_key = graph_ql_property(
                is_identifier=True, filterable=True, query_annotation=models.F("id")
            )(integer_key)
            record_keys = graph_ql_property(is_identifier=True, cache="none")(
                integer_keys
            )
            dependency_key = graph_ql_property(is_identifier=True, cache="dependency")(
                integer_key
            )
            dependency_keys = graph_ql_property(is_identifier=True, cache="dependency")(
                integer_keys
            )
            restricted_key = graph_ql_property(is_identifier=True)(integer_key)

            @graph_ql_property(is_identifier=True, cache="dependency")
            def key_tuple(self) -> tuple[int, ...]:
                return (self.id,)

            @graph_ql_property(is_identifier=True, cache="timeout", timeout=30)
            def key_set(self) -> set[int]:
                return {self.id}

            @graph_ql_property(is_identifier=True)
            def optional_key(self) -> int | None:
                return None

            @graph_ql_property(is_identifier=True)
            def optional_keys(self) -> list[int | None] | None:
                return [self.id, None]

            @graph_ql_property(is_identifier=True)
            def absent_keys(self) -> list[int] | None:
                return None

            @graph_ql_property(is_identifier=True)
            def empty_keys(self) -> list[int]:
                return []

            @graph_ql_property
            def external_id(self) -> int:
                return self.sequence

            @graph_ql_property
            def business_ids(self) -> list[int]:
                return [self.sequence]

        class PropertyBigIntegerRecord(GeneralManager):
            class Interface(DatabaseInterface):
                id = models.BigIntegerField(primary_key=True)

            Permission = PropertyIdentityPermission
            record_key = graph_ql_property(is_identifier=True)(integer_key)
            record_keys = graph_ql_property(is_identifier=True)(integer_keys)

        class PropertyUuidRecord(GeneralManager):
            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {"id": Input(UUID)}
                id = models.UUIDField(primary_key=True, default=uuid4)

            Permission = PropertyIdentityPermission

            @graph_ql_property(is_identifier=True)
            def record_key(self) -> UUID:
                return self.id

            @graph_ql_property(is_identifier=True)
            def record_keys(self) -> list[UUID]:
                return list(type(self).filter(id=self.id).values_list("id", flat=True))

        class PropertyCodeRecord(GeneralManager):
            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {"id": Input(str)}
                code = models.CharField(max_length=32, primary_key=True)
                id = models.IntegerField(default=23)

            Permission = PropertyIdentityPermission

            @graph_ql_property(is_identifier=True)
            def record_key(self) -> str:
                return self.code

            @graph_ql_property(is_identifier=True)
            def record_keys(self) -> list[str]:
                return list(
                    type(self).filter(code=self.code).values_list("code", flat=True)
                )

        class PropertyReference(GeneralManager):
            class Interface(DatabaseInterface):
                owner = models.ForeignKey(
                    "general_manager.PropertyBigRecord", on_delete=models.CASCADE
                )

            Permission = PropertyIdentityPermission

            @graph_ql_property(is_identifier=True)
            def reference_key(self) -> int:
                return self.owner_id

            @graph_ql_property(is_identifier=True)
            def reference_keys(self) -> list[int]:
                return list(
                    type(self).filter(id=self.id).values_list("owner_id", flat=True)
                )

        class PropertyExactCalculation(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(int)
                account = Input(str)

            Permission = PropertyIdentityPermission

            @graph_ql_property(is_identifier=True)
            def record_key(self) -> Annotated[int, "native identity"]:
                return 2**100 + 1

            @graph_ql_property(is_identifier=True)
            def record_keys(self) -> list[int]:
                return [2**100 + 1]

        cls.BigRecord = PropertyBigRecord
        cls.BigIntegerRecord = PropertyBigIntegerRecord
        cls.UuidRecord = PropertyUuidRecord
        cls.CodeRecord = PropertyCodeRecord
        cls.Reference = PropertyReference
        cls.general_manager_classes = [
            PropertyBigRecord,
            PropertyBigIntegerRecord,
            PropertyUuidRecord,
            PropertyCodeRecord,
            PropertyReference,
            PropertyExactCalculation,
        ]

    def setUp(self) -> None:
        super().setUp()
        self.user = get_user_model().objects.create_user(username="property-identities")
        self.client.force_login(self.user)

    def test_schema_and_introspection_expose_explicit_scalar_and_list_ids(self) -> None:
        schema = GraphQL.get_schema()
        for manager in self.general_manager_classes:
            fields = schema.graphql_schema.get_type(f"{manager.__name__}Type").fields
            scalar = "referenceKey" if manager is self.Reference else "recordKey"
            collection = "referenceKeys" if manager is self.Reference else "recordKeys"
            with self.subTest(manager=manager.__name__):
                self.assertEqual(str(fields[scalar].type), "ID")
                self.assertEqual(str(fields[collection].type), "[ID]")
        self.assertIn("recordKey: ID", str(schema))
        introspection = {t["name"]: t for t in schema.introspect()["__schema"]["types"]}
        fields = {
            f["name"]: f for f in introspection["PropertyBigRecordType"]["fields"]
        }
        self.assertEqual(fields["recordKeys"]["type"]["ofType"]["name"], "ID")
        filters = get_named_type(
            schema.graphql_schema.query_type.fields["propertyBigRecordList"]
            .args["filter"]
            .type
        ).fields
        self.assertEqual(str(filters["recordKey"].type), "ID")
        self.assertEqual(str(filters["recordKey_Exact"].type), "ID")
        self.assertEqual(str(filters["recordKey_In"].type), "[ID]")
        self.assertEqual(str(filters["recordKey_Gt"].type), "Int")
        self.assertTrue(
            self.BigRecord.Interface.get_graph_ql_properties()[
                "record_key"
            ].is_identifier
        )

    def test_real_primary_key_projections_serialize_like_direct_keys(self) -> None:
        for manager, pk, field in (
            (self.BigRecord, 7, "id"),
            (self.BigRecord, 2**31, "id"),
            (self.BigIntegerRecord, 2**60 + 1, "id"),
            (self.UuidRecord, uuid4(), "id"),
            (self.CodeRecord, "000042", "code"),
        ):
            with self.subTest(manager=manager.__name__, pk=pk):
                row = manager.Interface._model.objects.create(pk=pk)
                query_name = manager.__name__[0].lower() + manager.__name__[1:]
                response = self.query(
                    f"query($id: ID!) {{ {query_name}(id: $id) {{ {field} recordKey recordKeys }} }}",
                    variables={"id": str(pk)},
                )
                self.assertResponseNoErrors(response)
                self.assertEqual(
                    response.json()["data"][query_name],
                    {
                        field: str(pk),
                        "recordKey": str(pk),
                        "recordKeys": [str(pk)],
                    },
                )
                instance = manager(pk)
                self.assertEqual(instance.record_key, pk)
                self.assertIsInstance(instance.record_key, type(pk))
                self.assertEqual(instance.record_keys, [pk])
                self.assertEqual(
                    list(manager.filter(**{field: pk}).values_list(field, flat=True)),
                    [pk],
                )
                self.assertEqual(
                    list(manager.filter(**{field: pk}).values(field)), [{field: pk}]
                )
                row.refresh_from_db()
                self.assertIsInstance(row.pk, type(pk))

    def test_optional_empty_collection_cache_and_business_contracts(self) -> None:
        self.BigRecord.Interface._model.objects.create(pk=2**31)
        for _ in range(2):
            response = self.query("""
                query { propertyBigRecord(id: "2147483648") {
                  keyTuple keySet optionalKey optionalKeys absentKeys emptyKeys
                  externalId businessIds restrictedKey
                } }
            """)
            self.assertResponseNoErrors(response)
            self.assertEqual(
                response.json()["data"]["propertyBigRecord"],
                {
                    "keyTuple": ["2147483648"],
                    "keySet": ["2147483648"],
                    "optionalKey": None,
                    "optionalKeys": ["2147483648", None],
                    "absentKeys": None,
                    "emptyKeys": [],
                    "externalId": 7,
                    "businessIds": [7],
                    "restrictedKey": None,
                },
            )
        schema = GraphQL.get_schema().graphql_schema
        fields = schema.get_type("PropertyBigRecordType").fields
        self.assertEqual(str(fields["externalId"].type), "Int")
        self.assertEqual(str(fields["businessIds"].type), "[Int]")
        self.assertEqual(
            str(schema.get_type("PropertyCodeRecordType").fields["id"].type), "Int"
        )
        args = schema.query_type.fields["propertyExactCalculation"].args
        self.assertEqual(str(args["id"].type), "Int!")
        self.assertEqual(str(args["account"].type), "String!")

    def test_fk_projections_keep_native_reference_values(self) -> None:
        owner = self.BigRecord.Interface._model.objects.create(pk=2**31)
        reference = self.Reference.Interface._model.objects.create(owner=owner)
        response = self.query(
            """
            query($id: ID!) { propertyReference(id: $id) {
                ownerId owner { id } referenceKey referenceKeys
            } }
        """,
            variables={"id": str(reference.pk)},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["propertyReference"],
            {
                "ownerId": str(owner.pk),
                "owner": {"id": str(owner.pk)},
                "referenceKey": str(owner.pk),
                "referenceKeys": [str(owner.pk)],
            },
        )
        self.assertEqual(self.Reference(reference.pk).reference_keys, [owner.pk])

    def test_exact_python_integer_identity_keeps_all_digits(self) -> None:
        response = self.query("""
            query { propertyExactCalculation(id: 7, account: "example") {
                id account recordKey recordKeys
            } }
        """)
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["propertyExactCalculation"],
            {
                "id": 7,
                "account": "example",
                "recordKey": str(2**100 + 1),
                "recordKeys": [str(2**100 + 1)],
            },
        )

    def test_grouped_schema_preserves_native_identity_and_business_types(self) -> None:
        schema = GraphQL.get_schema().graphql_schema
        for manager in self.general_manager_classes:
            fields = schema.get_type(f"{manager.__name__}GroupType").fields
            scalar = "referenceKey" if manager is self.Reference else "recordKey"
            collection = "referenceKeys" if manager is self.Reference else "recordKeys"
            with self.subTest(manager=manager.__name__):
                self.assertEqual(str(fields[scalar].type), "ID")
                self.assertEqual(str(fields[collection].type), "[ID]")
        fields = schema.get_type("PropertyBigRecordGroupType").fields
        self.assertEqual(str(fields["externalId"].type), "Int")
        self.assertEqual(str(fields["businessIds"].type), "[Int]")

    def test_grouped_integer_identities_keep_native_aggregation_and_nulls(self) -> None:
        for pk in (2, 10):
            self.BigRecord.Interface._model.objects.create(pk=pk)
        response = self.query("""
            query { propertyBigRecordGroups(groupBy: ["sequence"]) {
                items {
                    recordKey recordKeys dependencyKey dependencyKeys optionalKey optionalKeys absentKeys
                    emptyKeys externalId businessIds
                }
                pageInfo { totalCount }
            } }
        """)
        self.assertResponseNoErrors(response)
        items = response.json()["data"]["propertyBigRecordGroups"]["items"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["recordKey"], "12")
        self.assertCountEqual(item["recordKeys"], ["2", "10"])
        self.assertEqual(item["dependencyKey"], "12")
        self.assertCountEqual(item["dependencyKeys"], ["2", "10"])
        self.assertEqual(
            response.json()["data"]["propertyBigRecordGroups"]["pageInfo"],
            {"totalCount": 1},
        )
        self.assertIsNone(item["optionalKey"])
        self.assertCountEqual(item["optionalKeys"], ["2", None, "10", None])
        self.assertIsNone(item["absentKeys"])
        self.assertEqual(item["emptyKeys"], [])
        self.assertEqual(item["externalId"], 14)
        self.assertEqual(item["businessIds"], [7, 7])

    def test_grouped_string_and_large_integer_identities_serialize_as_ids(self) -> None:
        for code in ("a", "b"):
            self.CodeRecord.Interface._model.objects.create(pk=code)
        pk = 2**60 + 1
        self.BigIntegerRecord.Interface._model.objects.create(pk=pk)
        response = self.query("""
            query {
                propertyCodeRecordGroups(groupBy: ["id"]) {
                    items { recordKey recordKeys }
                }
                propertyBigIntegerRecordGroups(groupBy: ["id"]) {
                    items { id recordKey recordKeys }
                }
            }
        """)
        self.assertResponseNoErrors(response)
        data = response.json()["data"]
        codes = data["propertyCodeRecordGroups"]["items"]
        self.assertEqual(len(codes), 1)
        self.assertCountEqual(codes[0]["recordKey"].split(", "), ["a", "b"])
        self.assertCountEqual(codes[0]["recordKeys"], ["a", "b"])
        self.assertEqual(
            data["propertyBigIntegerRecordGroups"]["items"],
            [{"id": str(pk), "recordKey": str(pk), "recordKeys": [str(pk)]}],
        )

    def test_grouped_unsupported_identity_annotations_report_field_errors(self) -> None:
        self.BigRecord.Interface._model.objects.create(pk=2)
        self.UuidRecord.Interface._model.objects.create()
        for manager, field, native_field in (
            (self.BigRecord, "keyTuple", "key_tuple"),
            (self.BigRecord, "keySet", "key_set"),
            (self.UuidRecord, "recordKey", "record_key"),
            (self.UuidRecord, "recordKeys", "record_keys"),
        ):
            with self.subTest(manager=manager.__name__, field=field):
                query_name = (
                    manager.__name__[0].lower() + manager.__name__[1:] + "Groups"
                )
                response = self.query(
                    f'query {{ {query_name}(groupBy: ["id"]) {{ '
                    f"items {{ {field} }} pageInfo {{ totalCount }} }} }}"
                )
                result = response.json()
                self.assertEqual(
                    result["errors"][0]["message"],
                    f"{native_field} is not available for grouped results.",
                )
                self.assertEqual(
                    result["errors"][0]["path"], [query_name, "items", 0, field]
                )
                self.assertEqual(
                    result["data"][query_name],
                    {"items": [{field: None}], "pageInfo": {"totalCount": 1}},
                )
