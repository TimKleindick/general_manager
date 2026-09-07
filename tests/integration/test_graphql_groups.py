from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import CASCADE, CharField, ForeignKey, IntegerField
from django.utils.crypto import get_random_string

from general_manager.api.graphql_type import GraphQLType
from general_manager.api.property import graph_ql_property
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement import Measurement
from general_manager.measurement.measurement_field import MeasurementField
from general_manager.permission.base_permission import ReadPermissionPlan
from general_manager.permission.manager_based_permission import (
    AdditiveManagerPermission,
)
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class GroupProjectSummary(GraphQLType):
    label: str


class TestGraphQLGroups(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class GroupCommercial(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        class GroupProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                group_key = CharField(max_length=100, null=True, blank=True)
                amount = IntegerField(null=True)
                count = IntegerField(null=True, blank=True)
                distance = MeasurementField(base_unit="meter", null=True, blank=True)
                commercial = ForeignKey(
                    "general_manager.GroupCommercial",
                    on_delete=CASCADE,
                    null=True,
                    blank=True,
                )

            @graph_ql_property(cache="none")
            def summaries(self) -> list[GroupProjectSummary]:
                return [GroupProjectSummary(label=self.name)]

            @graph_ql_property(cache="none")
            def owner(self) -> GroupCommercial | None:
                return self.commercial

        cls.general_manager_classes = [GroupCommercial, GroupProject]
        cls.group_commercial = GroupCommercial
        cls.group_project = GroupProject

    def setUp(self) -> None:
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="group-user",
            password=password,
        )
        self.client.login(username="group-user", password=password)

    def test_groups_are_flat_distinct_from_ordinary_records(self) -> None:
        first = self.group_commercial.Factory.create(name="First")
        second = self.group_commercial.Factory.create(name="Second")
        self.group_project.Factory.create(
            name="Shared", amount=2, count=7, commercial=first
        )
        self.group_project.Factory.create(
            name="Shared", amount=3, count=11, commercial=second
        )

        response = self.query(
            """
            query {
              ordinary: groupProjectList(orderBy: [{field: name}]) {
                __typename
                items { __typename id name amount count }
                pageInfo { totalCount }
              }
              grouped: groupProjectGroups(groupBy: ["name"]) {
                __typename
                items { __typename name amount count }
                pageInfo { totalCount }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        payload = response.json()["data"]
        ordinary = payload["ordinary"]
        grouped = payload["grouped"]
        self.assertNotEqual(ordinary["__typename"], grouped["__typename"])
        self.assertNotEqual(
            ordinary["items"][0]["__typename"],
            grouped["items"][0]["__typename"],
        )
        self.assertEqual(ordinary["pageInfo"]["totalCount"], 2)
        self.assertEqual(grouped["pageInfo"]["totalCount"], 1)
        self.assertEqual(
            grouped["items"],
            [
                {
                    "__typename": grouped["items"][0]["__typename"],
                    "name": "Shared",
                    "amount": 5,
                    "count": 18,
                }
            ],
        )

    def test_grouped_id_retains_only_a_selected_key(self) -> None:
        project = self.group_project.Factory.create(name="Shared", amount=2)
        response = self.query(
            """
            query($order: [GroupProjectGroupsOrderBy!]) {
              aggregate: groupProjectGroups(groupBy: ["name"]) {
                items { id name }
              }
              byId: groupProjectGroups(groupBy: ["id"], orderBy: $order) {
                items { id name }
              }
            }
            """,
            variables={"order": [{"field": "id"}]},
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]
        self.assertEqual(
            payload["aggregate"]["items"], [{"id": None, "name": "Shared"}]
        )
        self.assertEqual(
            payload["byId"]["items"],
            [{"id": project.identification["id"], "name": "Shared"}],
        )

    def test_grouped_singular_relation_exposes_distinct_list_and_groups(self) -> None:
        first = self.group_commercial.Factory.create(name="First")
        second = self.group_commercial.Factory.create(name="Second")
        self.group_project.Factory.create(name="Shared", amount=2, commercial=first)
        self.group_project.Factory.create(name="Shared", amount=3, commercial=second)
        self.group_project.Factory.create(name="Single", amount=4, commercial=first)

        response = self.query(
            """
            query {
              groupProjectGroups(groupBy: ["name"]) {
                items {
                  commercialList(orderBy: [{field: name}]) {
                    items { id name }
                    pageInfo { totalCount }
                  }
                  commercialGroups(
                    groupBy: ["name"]
                    orderBy: [{field: name}]
                  ) {
                    items { name }
                    pageInfo { totalCount }
                  }
                  ownerList(orderBy: [{field: name}]) {
                    items { id name }
                    pageInfo { totalCount }
                  }
                  ownerGroups(groupBy: ["name"]) {
                    items { name }
                    pageInfo { totalCount }
                  }
                }
              }
              ordered: groupProjectGroups(
                groupBy: ["name"]
                orderBy: [{field: commercialId}]
              ) {
                items { name commercialId }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        item = response.json()["data"]["groupProjectGroups"]["items"][0]
        self.assertEqual(
            item["commercialList"],
            {
                "items": [
                    {"id": first.identification["id"], "name": "First"},
                    {"id": second.identification["id"], "name": "Second"},
                ],
                "pageInfo": {"totalCount": 2},
            },
        )
        self.assertEqual(
            item["commercialGroups"]["items"],
            [{"name": "First"}, {"name": "Second"}],
        )
        self.assertEqual(item["commercialGroups"]["pageInfo"]["totalCount"], 2)
        self.assertEqual(item["ownerList"], item["commercialList"])
        self.assertEqual(item["ownerGroups"], item["commercialGroups"])
        self.assertEqual(
            response.json()["data"]["ordered"]["items"],
            [
                {"name": "Single", "commercialId": first.identification["id"]},
                {"name": "Shared", "commercialId": None},
            ],
        )

    def test_recursive_relation_collections_are_scoped_to_each_group(self) -> None:
        first = self.group_commercial.Factory.create(name="Shared")
        second = self.group_commercial.Factory.create(name="Shared")
        for commercial, name, amount in (
            (first, "First", 1),
            (first, "First", 2),
            (second, "Second", 4),
        ):
            self.group_project.Factory.create(
                name=name,
                amount=amount,
                commercial=commercial,
            )

        response = self.query(
            """
            query {
              groupCommercialGroups(groupBy: ["name"]) {
                items {
                  name
                  groupProjectList(orderBy: [{field: name}]) {
                    items { name amount }
                    pageInfo { totalCount }
                  }
                  groupProjectGroups(
                    groupBy: ["name"]
                    orderBy: [{field: name}]
                  ) {
                    items { name amount }
                    pageInfo { totalCount }
                  }
                }
                pageInfo { totalCount }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["groupCommercialGroups"]
        self.assertEqual(payload["pageInfo"]["totalCount"], 1)
        item = payload["items"][0]
        self.assertEqual(item["name"], "Shared")
        self.assertEqual(item["groupProjectList"]["pageInfo"]["totalCount"], 3)
        self.assertEqual(
            item["groupProjectList"]["items"],
            [
                {"name": "First", "amount": 1},
                {"name": "First", "amount": 2},
                {"name": "Second", "amount": 4},
            ],
        )
        self.assertEqual(item["groupProjectGroups"]["pageInfo"]["totalCount"], 2)
        self.assertEqual(
            item["groupProjectGroups"]["items"],
            [{"name": "First", "amount": 3}, {"name": "Second", "amount": 4}],
        )

    def test_nested_collections_filter_and_paginate_independently(self) -> None:
        first = self.group_commercial.Factory.create(name="Shared")
        second = self.group_commercial.Factory.create(name="Shared")
        for commercial, name, amount in (
            (first, "First", 1),
            (first, "First", 2),
            (second, "Second", 4),
        ):
            self.group_project.Factory.create(
                name=name,
                amount=amount,
                commercial=commercial,
            )

        response = self.query(
            """
            query {
              groupCommercialGroups(groupBy: ["name"]) {
                items {
                  allPage: groupProjectList(
                    orderBy: [{field: name}]
                    page: 2
                    pageSize: 1
                  ) {
                    items { name amount }
                    pageInfo { totalCount currentPage totalPages pageSize }
                  }
                  filteredGroups: groupProjectGroups(
                    groupBy: ["name"]
                    filter: {amount_Gte: 2}
                    exclude: {name: "Second"}
                    page: 1
                    pageSize: 1
                  ) {
                    items { name amount }
                    pageInfo { totalCount currentPage totalPages pageSize }
                  }
                  emptyGroups: groupProjectGroups(
                    groupBy: ["name"]
                    filter: {name: "missing"}
                    page: 1
                    pageSize: 1
                  ) {
                    items { name }
                    pageInfo { totalCount currentPage totalPages pageSize }
                  }
                  outOfRange: groupProjectGroups(
                    groupBy: ["name"]
                    page: 3
                    pageSize: 1
                  ) {
                    items { name }
                    pageInfo { totalCount currentPage totalPages pageSize }
                  }
                }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        item = response.json()["data"]["groupCommercialGroups"]["items"][0]
        self.assertEqual(item["allPage"]["items"], [{"name": "First", "amount": 2}])
        self.assertEqual(
            item["allPage"]["pageInfo"],
            {"totalCount": 3, "currentPage": 2, "totalPages": 3, "pageSize": 1},
        )
        self.assertEqual(
            item["filteredGroups"]["items"], [{"name": "First", "amount": 2}]
        )
        self.assertEqual(
            item["filteredGroups"]["pageInfo"],
            {"totalCount": 1, "currentPage": 1, "totalPages": 1, "pageSize": 1},
        )
        self.assertEqual(item["emptyGroups"]["items"], [])
        self.assertEqual(
            item["emptyGroups"]["pageInfo"],
            {"totalCount": 0, "currentPage": 1, "totalPages": 0, "pageSize": 1},
        )
        self.assertEqual(item["outOfRange"]["items"], [])
        self.assertEqual(
            item["outOfRange"]["pageInfo"],
            {"totalCount": 2, "currentPage": 3, "totalPages": 2, "pageSize": 1},
        )

    def test_group_ordering_supports_scalar_aggregate_fields(self) -> None:
        commercial = self.group_commercial.Factory.create(name="Commercial")
        for name, amounts in (("Small", (1, 2)), ("Large", (7, 8))):
            for amount in amounts:
                self.group_project.Factory.create(
                    name=name,
                    amount=amount,
                    commercial=commercial,
                )

        response = self.query(
            """
            query {
              groupProjectGroups(
                groupBy: ["name"]
                orderBy: [{field: amount, direction: DESC}]
              ) {
                items { name amount }
                pageInfo { totalCount }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupProjectGroups"]["items"],
            [{"name": "Large", "amount": 15}, {"name": "Small", "amount": 3}],
        )

    def test_group_ordering_projects_aggregate_count_field(self) -> None:
        commercial = self.group_commercial.Factory.create(name="Commercial")
        for name, count in (("Few", 1), ("Many", 9)):
            self.group_project.Factory.create(
                name=name,
                count=count,
                amount=1,
                commercial=commercial,
            )
            self.group_project.Factory.create(
                name=name,
                count=count,
                amount=1,
                commercial=commercial,
            )

        response = self.query(
            """
            query {
              groupProjectGroups(
                groupBy: ["name"]
                orderBy: [{field: count, direction: DESC}]
              ) {
                items { name count }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupProjectGroups"]["items"],
            [{"name": "Many", "count": 18}, {"name": "Few", "count": 2}],
        )

    def test_grouped_text_and_measurement_values_keep_group_semantics(self) -> None:
        commercial = self.group_commercial.Factory.create(name="Commercial")
        for name, texts, distance in (
            ("Mixed", ("Schraube", "Mutter", "Schraube"), Measurement(1, "meter")),
            ("Null", (None, None), None),
        ):
            for text in texts:
                self.group_project.Factory.create(
                    name=name,
                    group_key=text,
                    amount=1,
                    distance=distance,
                    commercial=commercial,
                )

        response = self.query(
            """
            query {
              groupProjectGroups(groupBy: ["name"]) {
                items {
                  name
                  groupKey
                  amount
                  distance { value unit }
                  converted: distance(targetUnit: "centimeter") { value unit }
                }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertCountEqual(
            response.json()["data"]["groupProjectGroups"]["items"],
            [
                {
                    "name": "Mixed",
                    "groupKey": "Schraube, Mutter",
                    "amount": 3,
                    "distance": {"value": 3.0, "unit": "meter"},
                    "converted": {"value": 300.0, "unit": "centimeter"},
                },
                {
                    "name": "Null",
                    "groupKey": None,
                    "amount": 2,
                    "distance": None,
                    "converted": None,
                },
            ],
        )

    def test_plain_typed_lists_have_no_group_companion_and_concatenate(self) -> None:
        commercial = self.group_commercial.Factory.create(name="Commercial")
        self.group_project.Factory.create(name="First", amount=1, commercial=commercial)
        self.group_project.Factory.create(
            name="Second", amount=2, commercial=commercial
        )

        response = self.query(
            """
            query {
              grouped: groupProjectGroups(groupBy: ["commercialId"]) {
                items { summaries { label } }
              }
              output: __type(name: "GroupProjectGroupType") {
                fields(includeDeprecated: true) { name args { name } }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["grouped"]["items"],
            [{"summaries": [{"label": "First"}, {"label": "Second"}]}],
        )
        fields = {
            field["name"]: field["args"]
            for field in response.json()["data"]["output"]["fields"]
        }
        self.assertIn("id", fields)
        self.assertTrue({"keys", "sums", "members"}.isdisjoint(fields))
        self.assertEqual(fields["summaries"], [])
        self.assertNotIn("summariesList", fields)
        self.assertNotIn("summariesGroups", fields)

    def test_group_schema_uses_required_groups_and_removes_group_by_from_list(
        self,
    ) -> None:
        response = self.query(
            """
            query {
              root: __type(name: "Query") {
                fields(includeDeprecated: true) {
                  name
                  args { name type { kind ofType { kind name } } }
                }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        fields = {
            field["name"]: field for field in response.json()["data"]["root"]["fields"]
        }
        list_args = {arg["name"] for arg in fields["groupProjectList"]["args"]}
        group_args = {arg["name"] for arg in fields["groupProjectGroups"]["args"]}
        self.assertNotIn("groupBy", list_args)
        self.assertIn("groupBy", group_args)
        group_by_arg = next(
            arg
            for arg in fields["groupProjectGroups"]["args"]
            if arg["name"] == "groupBy"
        )
        self.assertEqual(group_by_arg["type"]["kind"], "NON_NULL")

    def test_denied_members_do_not_contribute_to_grouped_items(self) -> None:
        commercial = self.group_commercial.Factory.create(name="Commercial")
        self.group_project.Factory.create(
            name="Visible", amount=2, commercial=commercial
        )
        self.group_project.Factory.create(
            name="Hidden", amount=9, commercial=commercial
        )
        self.group_project.Factory.create(
            name="Visible", amount=9, commercial=commercial
        )

        class HideHighAmounts(AdditiveManagerPermission):
            def get_read_permission_plan(self) -> ReadPermissionPlan:
                return ReadPermissionPlan(filters=[], requires_instance_check=True)

            def can_read_instance(self) -> bool:
                return self.instance.amount != 9

        with patch.object(self.group_project, "Permission", HideHighAmounts):
            response = self.query(
                """
                query {
                  groupProjectGroups(groupBy: ["name"]) {
                    items { name amount }
                    pageInfo { totalCount }
                  }
                }
                """
            )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupProjectGroups"],
            {
                "items": [{"name": "Visible", "amount": 2}],
                "pageInfo": {"totalCount": 1},
            },
        )
