from django.contrib.auth import get_user_model
from django.db.models import (
    CASCADE,
    CharField,
    ForeignKey,
    IntegerField,
)
from django.utils.crypto import get_random_string
from unittest.mock import patch

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement import Measurement
from general_manager.measurement.measurement_field import MeasurementField
from general_manager.permission.base_permission import ReadPermissionPlan
from general_manager.permission.manager_based_permission import (
    AdditiveManagerPermission,
)
from general_manager.utils.testing import GeneralManagerTransactionTestCase


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
                distance = MeasurementField(base_unit="meter", null=True, blank=True)
                commercial = ForeignKey(
                    "general_manager.GroupCommercial",
                    on_delete=CASCADE,
                )

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

    def test_root_group_page_exposes_keys_members_count_and_sums(self) -> None:
        """A grouped relation must retain each original member relation object."""
        first_commercial = self.group_commercial.Factory.create(name="First")
        second_commercial = self.group_commercial.Factory.create(name="Second")
        self.group_project.Factory.create(
            name="Shared",
            amount=2,
            commercial=first_commercial,
        )
        self.group_project.Factory.create(
            name="Shared",
            amount=3,
            commercial=second_commercial,
        )

        response = self.query(
            """
            query {
              groupProjectGroups(groupBy: ["name"]) {
                groups {
                  keys { name }
                  members {
                    items { commercial { id name } }
                    pageInfo { totalCount }
                  }
                  count
                  sums { amount }
                }
                pageInfo { totalCount }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["groupProjectGroups"]
        shared_group = next(
            group for group in payload["groups"] if group["keys"]["name"] == "Shared"
        )
        self.assertEqual(shared_group["count"], 2)
        self.assertEqual(shared_group["sums"]["amount"], 5)
        self.assertEqual(shared_group["members"]["pageInfo"]["totalCount"], 2)
        self.assertEqual(
            {
                member["commercial"]["name"]
                for member in shared_group["members"]["items"]
            },
            {"First", "Second"},
        )

    def test_measurement_sum_accepts_target_unit(self) -> None:
        commercial = self.group_commercial.Factory.create(name="Commercial")
        self.group_project.Factory.create(
            name="Shared",
            amount=2,
            distance=Measurement(1, "meter"),
            commercial=commercial,
        )

        response = self.query(
            """
            query {
              groupProjectGroups(groupBy: ["name"]) {
                groups { sums { distance(targetUnit: "centimeter") { value unit } } }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupProjectGroups"]["groups"],
            [{"sums": {"distance": {"value": 100.0, "unit": "centimeter"}}}],
        )

    def test_group_by_accepts_camel_case_scalar_key(self) -> None:
        """GraphQL key spelling resolves to the interface's snake-case field."""
        commercial = self.group_commercial.Factory.create(name="Commercial")
        self.group_project.Factory.create(
            name="Shared",
            group_key="Camel",
            amount=2,
            commercial=commercial,
        )

        response = self.query(
            """
            query {
              groupProjectGroups(groupBy: ["groupKey"]) {
                groups { keys { groupKey } count }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupProjectGroups"]["groups"],
            [{"keys": {"groupKey": "Camel"}, "count": 1}],
        )

    def test_group_ordering_rejects_fields_that_are_not_selected_keys(self) -> None:
        """Ordering by an aggregate-like field must not read it during grouping."""
        commercial = self.group_commercial.Factory.create(name="Commercial")
        self.group_project.Factory.create(
            name="Shared",
            amount=2,
            commercial=commercial,
        )

        response = self.query(
            """
            query {
              groupProjectGroups(
                groupBy: ["name"]
                orderBy: [{field: amount}]
              ) {
                groups { count }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "must be selected grouping keys",
            response.json()["errors"][0]["message"],
        )

    def test_relation_group_sibling_uses_the_same_explicit_page_shape(self) -> None:
        """A relation list receives a sibling groups field instead of groupBy."""
        commercial = self.group_commercial.Factory.create(name="Commercial")
        self.group_project.Factory.create(
            name="Shared",
            amount=2,
            commercial=commercial,
        )

        response = self.query(
            """
            query {
              groupCommercialList {
                items {
                  groupProjectGroups(groupBy: ["name"]) {
                    groups { keys { name } count }
                  }
                }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        groups = response.json()["data"]["groupCommercialList"]["items"][0][
            "groupProjectGroups"
        ]["groups"]
        self.assertEqual(groups, [{"keys": {"name": "Shared"}, "count": 1}])

    def test_grouping_fails_closed_when_a_selected_key_is_denied(self) -> None:
        """A denied key must fail before grouping can disclose its value."""
        commercial = self.group_commercial.Factory.create(name="Commercial")
        self.group_project.Factory.create(
            name="Secret", amount=2, commercial=commercial
        )

        with patch(
            "general_manager.api.graphql_groups.check_read_permission",
            side_effect=lambda _member, _info, field: field != "name",
        ):
            response = self.query(
                """
                query {
                  groupProjectGroups(groupBy: ["name"]) { groups { count } }
                }
                """
            )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "Permission denied to read grouping key 'name'",
            response.json()["errors"][0]["message"],
        )

    def test_denied_sum_field_returns_a_graphql_field_error(self) -> None:
        """A sum does not read values when any member denies that field."""
        commercial = self.group_commercial.Factory.create(name="Commercial")
        self.group_project.Factory.create(
            name="Shared", amount=2, commercial=commercial
        )

        with patch(
            "general_manager.api.graphql_groups.check_read_permission",
            side_effect=lambda _member, _info, field: field != "amount",
        ):
            response = self.query(
                """
                query {
                  groupProjectGroups(groupBy: ["name"]) {
                    groups { sums { amount } }
                  }
                }
                """
            )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "Permission denied to read sum field 'amount'",
            response.json()["errors"][0]["message"],
        )

    def test_denied_members_do_not_contribute_to_group_count_or_sum(self) -> None:
        """Row authorization happens before an explicit group is formed."""
        commercial = self.group_commercial.Factory.create(name="Commercial")
        self.group_project.Factory.create(
            name="Shared", amount=2, commercial=commercial
        )
        self.group_project.Factory.create(
            name="Shared", amount=9, commercial=commercial
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
                    groups { count sums { amount } }
                  }
                }
                """
            )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupProjectGroups"]["groups"],
            [{"count": 1, "sums": {"amount": 2}}],
        )
