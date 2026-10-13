"""Execute generated schemas for native list constructor inputs."""

from typing import ClassVar, List
from datetime import date
from uuid import UUID
from unittest.mock import patch

from django.db import models
from graphql import Undefined

from general_manager.api.graphql import GraphQL
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input
from general_manager.permission import ManagerBasedPermission
from general_manager.measurement import Measurement
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class CollectionPublicPermission(ManagerBasedPermission):
    __read__: ClassVar[list[str]] = ["public"]


class GraphQLCollectionInputTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class CollectionEmployee(GeneralManager):
            class Interface(DatabaseInterface):
                id = models.BigAutoField(primary_key=True)
                name = models.CharField(max_length=40)

            Permission = CollectionPublicPermission

        class CollectionSummary(GeneralManager):
            class Interface(CalculationInterface):
                values = Input(list[int], required=False)
                month = Input(date, required=False)
                projects = Input(list[CollectionEmployee], required=False)
                customers = Input(list[CollectionEmployee], required=False)
                measurements = Input(list[Measurement], required=False)
                raw = Input(list, required=False)
                employees = Input(list[CollectionEmployee], required=False)

            Permission = CollectionPublicPermission

        class CollectionRequired(GeneralManager):
            class Interface(CalculationInterface):
                values = Input(List[int])  # Exercise typing.List compatibility.

            Permission = CollectionPublicPermission

        class CollectionComposite(GeneralManager):
            class Interface(CalculationInterface):
                label = Input(str)
                values = Input(list[int])
                employees = Input(list[CollectionEmployee], required=False)

            Permission = CollectionPublicPermission

        class CollectionCode(GeneralManager):
            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {"id": Input(str)}
                id = models.CharField(max_length=40, primary_key=True)

            Permission = CollectionPublicPermission

        class CollectionUuid(GeneralManager):
            class Interface(DatabaseInterface):
                input_fields: ClassVar[dict[str, Input]] = {"id": Input(UUID)}
                id = models.UUIDField(primary_key=True)

            Permission = CollectionPublicPermission

        class CollectionIdentities(GeneralManager):
            class Interface(CalculationInterface):
                codes = Input(list[CollectionCode])
                tokens = Input(list[CollectionUuid])

            Permission = CollectionPublicPermission

        class CollectionNested(GeneralManager):
            class Interface(CalculationInterface):
                selections = Input(list[CollectionComposite])

            Permission = CollectionPublicPermission

        cls.Code = CollectionCode
        cls.Token = CollectionUuid
        cls.Employee = CollectionEmployee
        cls.Summary = CollectionSummary
        cls.general_manager_classes = [
            CollectionEmployee,
            CollectionSummary,
            CollectionRequired,
            CollectionComposite,
            CollectionNested,
            CollectionCode,
            CollectionUuid,
            CollectionIdentities,
        ]

    def test_schema_nullability_and_no_defaults(self):
        schema = GraphQL.get_schema().graphql_schema
        args = schema.query_type.fields["collectionSummary"].args
        self.assertEqual(str(args["values"].type), "[Int!]")
        self.assertEqual(str(args["employeesId"].type), "[ID!]")
        self.assertEqual(str(args["raw"].type), "String")
        self.assertIs(args["values"].default_value, Undefined)
        required = schema.query_type.fields["collectionRequired"].args
        self.assertEqual(str(required["values"].type), "[Int!]!")

    def test_optional_omission_null_empty_and_order(self):
        for arguments, expected in [
            ("", None),
            ("(values: null)", None),
            ("(values: [])", []),
            ("(values: [2, 1, 2])", [2, 1, 2]),
            ("(values: 7)", [7]),
        ]:
            response = self.query("{ collectionSummary" + arguments + " { values } }")
            self.assertResponseNoErrors(response)
            self.assertEqual(
                response.json()["data"]["collectionSummary"]["values"], expected
            )

    def test_required_and_invalid_elements(self):
        for argument in ["", "(values: null)", "(values: [null])", '(values: ["1"])']:
            response = self.query("{ collectionRequired" + argument + " { values } }")
            self.assertTrue(response.json().get("errors"))
        response = self.query("{ collectionRequired(values: []) { values } }")
        self.assertResponseNoErrors(response)

    def test_large_manager_ids_and_duplicates(self):
        identity = 2**60 + 3
        self.Employee.Interface._model.objects.create(pk=identity, name="Selected")
        response = self.query(
            "query($ids: [ID!]) { collectionSummary(employeesId: $ids) { employees { id name } } }",
            variables={"ids": [str(identity), str(identity)]},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["collectionSummary"]["employees"],
            [{"id": str(identity), "name": "Selected"}] * 2,
        )

    def test_composite_manager_list_contains_nested_collection(self):
        self.Employee.Interface._model.objects.create(pk=17, name="Nested")
        response = self.query("""{
          collectionNested(selectionsId: [{label: "first", values: [2, 1, 2], employees: ["17"]},
                                         {label: "empty", values: []}]) {
            selections { label values employees { id } }
          }
        }""")
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["collectionNested"]["selections"],
            [
                {"label": "first", "values": [2, 1, 2], "employees": [{"id": "17"}]},
                {"label": "empty", "values": [], "employees": None},
            ],
        )

    def test_string_and_uuid_identifiers_preserve_native_identity(self):
        token = UUID("00000000-0000-0000-0000-00000000ab01")
        self.Code.Interface._model.objects.create(pk="00017")
        self.Token.Interface._model.objects.create(pk=token)
        response = self.query(
            'query($tokens: [ID!]!) { collectionIdentities(codesId: ["00017"], tokensId: $tokens) { codes { id } tokens { id } } }',
            variables={"tokens": [str(token)]},
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["collectionIdentities"],
            {"codes": [{"id": "00017"}], "tokens": [{"id": str(token)}]},
        )

    def test_month_and_three_selections(self):
        response = self.query(
            """query($month: Date!, $employees: [ID!], $projects: [ID!], $customers: [ID!]) {
          collectionSummary(month: $month, employeesId: $employees,
                            projectsId: $projects, customersId: $customers) {
            month employees { id } projects { id } customers { id }
          }
        }""",
            variables={
                "month": "2026-10-01",
                "employees": [],
                "projects": [],
                "customers": None,
            },
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["collectionSummary"],
            {"month": "2026-10-01", "employees": [], "projects": [], "customers": None},
        )

    def test_measurement_list_output(self):
        response = self.query(
            '{ collectionSummary(measurements: ["2 meter"]) { measurements { value unit } } }'
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["collectionSummary"]["measurements"],
            [{"value": 2.0, "unit": "meter"}],
        )

    def test_collection_and_member_property_permissions_are_preserved(self):
        self.Employee.Interface._model.objects.create(pk=17, name="Private")
        from general_manager.api.graphql_resolvers import check_read_permission_for_user

        def check(instance, user, field):
            return field not in {"values", "name"} and check_read_permission_for_user(
                instance, user, field
            )

        with patch(
            "general_manager.api.graphql_resolvers.check_read_permission_for_user",
            side_effect=check,
        ):
            response = self.query(
                '{ collectionSummary(values: [1], employeesId: ["17"]) { values employees { id name } } }'
            )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["collectionSummary"],
            {"values": None, "employees": [{"id": "17", "name": None}]},
        )

    def test_collection_output_filters_denied_members_preserving_order_and_duplicates(
        self,
    ):
        class RestrictedEmployeePermission(CollectionPublicPermission):
            def can_read_instance(self):
                return self.instance.id != 18

        for identity in [17, 18, 19]:
            self.Employee.Interface._model.objects.create(
                pk=identity, name=f"Employee {identity}"
            )
        with patch.object(self.Employee, "Permission", RestrictedEmployeePermission):
            response = self.query(
                "query($ids: [ID!]) { collectionSummary(employeesId: $ids) { employees { id name } } }",
                variables={"ids": ["17", "18", "19", "17", "18"]},
            )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["collectionSummary"]["employees"],
            [
                {"id": "17", "name": "Employee 17"},
                {"id": "19", "name": "Employee 19"},
                {"id": "17", "name": "Employee 17"},
            ],
        )

    def test_subscription_dependencies_include_typed_manager_members(self):
        self.Employee.Interface._model.objects.create(pk=17, name="Selected")
        summary = self.Summary(values=[1], employees_id=[17, 17])
        dependencies = GraphQL._resolve_subscription_dependencies(
            self.Summary, summary, None
        )
        self.assertEqual(dependencies, [(self.Employee, {"id": 17})])

    def test_manager_element_validation(self):
        for argument in ["[null]", '["not-an-integer"]']:
            response = self.query(
                "{ collectionSummary(employeesId: "
                + argument
                + ") { employees { id } } }"
            )
            self.assertTrue(response.json().get("errors"))
        response = self.query(
            '{ collectionNested(selectionsId: [{label: "missing"}]) { selections { label } } }'
        )
        self.assertTrue(response.json().get("errors"))
