from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import ClassVar, Literal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import CASCADE, CharField, ForeignKey, IntegerField
from django.utils.crypto import get_random_string

from general_manager.api.property import graph_ql_property
from general_manager.bucket.base_bucket import Bucket
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement import Measurement
from general_manager.permission.base_permission import (
    BasePermission,
    ReadPermissionPlan,
)
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class _GroupingFieldPermission(BasePermission):
    """Allow row reads while making field permissions observable in tests."""

    denied_fields: ClassVar[set[str]] = set()
    checks: ClassVar[list[str]] = []

    def check_permission(
        self,
        action: Literal["create", "read", "update", "delete"],
        attribute: str,
    ) -> bool:
        del action
        type(self).checks.append(attribute)
        return attribute not in type(self).denied_fields

    def check_operation_permission(
        self,
        action: Literal["create", "read", "update", "delete"],
    ) -> bool:
        del action
        return True

    def describe_operation_permissions(
        self,
        action: Literal["create", "read", "update", "delete"],
    ) -> tuple[str, ...]:
        del action
        return ()

    def get_read_permission_plan(self) -> ReadPermissionPlan:
        return ReadPermissionPlan(filters=[], requires_instance_check=False)


class TestGraphQLGroupingPermissions(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class GroupingCommercial(GeneralManager):
            class Permission(_GroupingFieldPermission):
                denied_fields: ClassVar[set[str]] = set()
                checks: ClassVar[list[str]] = []

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                group_key = CharField(max_length=100)

        class GroupingProject(GeneralManager):
            class Permission(_GroupingFieldPermission):
                denied_fields: ClassVar[set[str]] = set()
                checks: ClassVar[list[str]] = []

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                amount = IntegerField(null=True, blank=True)
                count = IntegerField(null=True)
                commercial = ForeignKey(
                    "general_manager.GroupingCommercial",
                    on_delete=CASCADE,
                )

            property_reads: ClassVar[int] = 0
            raw_payload_reads: ClassVar[int] = 0
            tuple_measurement_reads: ClassVar[int] = 0
            set_measurement_reads: ClassVar[int] = 0
            measurement_scalar_reads: ClassVar[int] = 0
            measurement_list_reads: ClassVar[int] = 0
            tuple_reads: ClassVar[int] = 0
            set_reads: ClassVar[int] = 0
            primitive_list_reads: ClassVar[int] = 0
            secret_list_reads: ClassVar[int] = 0

            @graph_ql_property(cache="none")
            def computed_amount(self) -> int:
                type(self).property_reads += 1
                return self.amount * 10

            @graph_ql_property(cache="none")
            def secret_list(self) -> Bucket[GroupingCommercial] | None:
                type(self).secret_list_reads += 1
                return GroupingCommercial.all()

            @graph_ql_property(cache="none")
            def secrets(self) -> Bucket[GroupingCommercial] | None:
                return self.secret_list

            @graph_ql_property(cache="none")
            def optional_secret_list(self) -> Bucket[GroupingCommercial] | None:
                return None

            @graph_ql_property(cache="none")
            def tuple_values(self) -> tuple[str, ...] | None:
                type(self).tuple_reads += 1
                if self.amount == 2:
                    return ("first", "second")
                if self.amount == 3:
                    return ()
                return None

            @graph_ql_property(cache="none")
            def set_values(self) -> set[str] | None:
                type(self).set_reads += 1
                if self.amount == 2:
                    return {"first", "second"}
                if self.amount == 3:
                    return set()
                return None

            @graph_ql_property(cache="none")
            def tuple_measurements(self) -> tuple[Measurement, ...] | None:
                type(self).tuple_measurement_reads += 1
                if self.amount == 2:
                    return (Measurement(1, "meter"),)
                if self.amount == 3:
                    return ()
                return None

            @graph_ql_property(cache="none")
            def set_measurements(self) -> set[Measurement] | None:
                type(self).set_measurement_reads += 1
                if self.amount == 2:
                    return {Measurement(1, "meter")}
                if self.amount == 3:
                    return set()
                return None

            @graph_ql_property(cache="none")
            def measurement_scalar(self) -> Measurement:
                type(self).measurement_scalar_reads += 1
                return Measurement(self.amount or 0, "meter")

            @graph_ql_property(cache="none")
            def measurement_list(self) -> list[Measurement]:
                type(self).measurement_list_reads += 1
                if self.amount == 2:
                    return [Measurement(1, "meter")]
                if self.amount == 3:
                    return [Measurement(2, "meter")]
                return []

            @graph_ql_property(cache="none")
            def primitive_list(self) -> list[str]:
                type(self).primitive_list_reads += 1
                if self.amount == 2:
                    return ["first"]
                if self.amount == 3:
                    return ["second"]
                return []

            @graph_ql_property(cache="none")
            def raw_payload(self) -> object:
                type(self).raw_payload_reads += 1
                return {"amount": self.amount}

        # ``get_type_hints`` resolves local test classes through the owning
        # manager namespace when GraphQLProperty metadata is generated.
        GroupingProject.GroupingCommercial = GroupingCommercial
        cls.general_manager_classes = [GroupingCommercial, GroupingProject]
        cls.commercial = GroupingCommercial
        cls.project = GroupingProject

    def setUp(self) -> None:
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="grouping-permissions-user",
            password=password,
        )
        self.client.login(username="grouping-permissions-user", password=password)
        self.project.Permission.denied_fields.clear()
        self.project.Permission.checks.clear()
        self.commercial.Permission.denied_fields.clear()
        self.commercial.Permission.checks.clear()
        self.project.property_reads = 0
        self.project.raw_payload_reads = 0
        self.project.tuple_measurement_reads = 0
        self.project.set_measurement_reads = 0
        self.project.measurement_scalar_reads = 0
        self.project.measurement_list_reads = 0
        self.project.tuple_reads = 0
        self.project.set_reads = 0
        self.project.primitive_list_reads = 0
        self.project.secret_list_reads = 0

    def _create_shared_projects(self) -> None:
        commercial = self.commercial.Factory.create(
            name="Commercial",
            group_key="shared",
            ignore_permission=True,
        )
        self.project.Factory.create(
            name="Shared",
            amount=2,
            count=7,
            commercial=commercial,
            ignore_permission=True,
        )
        self.project.Factory.create(
            name="Shared",
            amount=3,
            count=7,
            commercial=commercial,
            ignore_permission=True,
        )

    @contextmanager
    def _count_project_relation_id_reads(self) -> Iterator[list[int]]:
        descriptor = vars(self.project)["commercial_id"]
        reads = [0]

        class CountingDescriptor:
            def __get__(self, instance: object, owner: type | None = None) -> object:
                if instance is not None:
                    reads[0] += 1
                return descriptor.__get__(instance, owner)

        with patch.object(self.project, "commercial_id", CountingDescriptor()):
            yield reads

    def _assert_relation_id_grouping_denied_before_alias_read(
        self,
        query: str,
    ) -> None:
        self._create_shared_projects()
        self.project.Permission.denied_fields.add("commercial")

        with self._count_project_relation_id_reads() as reads:
            response = self.query(query)

        self.assertResponseHasErrors(response)
        self.assertIn(
            "Permission denied to read grouping key",
            response.json()["errors"][0]["message"],
        )
        self.assertIn("commercial_id", self.project.Permission.checks)
        self.assertIn("commercial", self.project.Permission.checks)
        self.assertEqual(reads[0], 0)

    def test_normal_relation_id_grouping_checks_canonical_permission_before_alias_read(
        self,
    ) -> None:
        self._assert_relation_id_grouping_denied_before_alias_read(
            """
            query {
              groupingProjectGroups(groupBy: ["commercialId"]) {
                items { amount }
                pageInfo { totalCount }
              }
            }
            """
        )

    def test_grouped_scalar_property_supports_aliases_and_fragments(self) -> None:
        self._create_shared_projects()

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items {
                  name
                  summedAmount: amount
                  ...ComputedProjectFields
                }
                pageInfo { totalCount }
              }
            }

            fragment ComputedProjectFields on GroupingProjectGroupType {
              computedAmount
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupingProjectGroups"],
            {
                "items": [
                    {
                        "name": "Shared",
                        "summedAmount": 5,
                        "computedAmount": 50,
                    }
                ],
                "pageInfo": {"totalCount": 1},
            },
        )

    def test_grouped_key_collision_uses_selected_count_value(self) -> None:
        self._create_shared_projects()

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["count"]) {
                items { count amount }
                pageInfo { totalCount }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupingProjectGroups"],
            {
                "items": [{"count": 7, "amount": 5}],
                "pageInfo": {"totalCount": 1},
            },
        )

    def test_denied_grouped_property_fails_before_property_read(self) -> None:
        self._create_shared_projects()
        self.project.Permission.denied_fields.add("computed_amount")

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items { computedAmount }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "Permission denied to read grouped field 'computed_amount'",
            response.json()["errors"][0]["message"],
        )
        self.assertEqual(self.project.property_reads, 0)
        self.assertIn("computed_amount", self.project.Permission.checks)

    def test_denied_grouped_property_page_fails_before_property_read(self) -> None:
        self._create_shared_projects()
        self.project.Permission.denied_fields.add("secret_list")

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items { secretList { items { name } } }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "Permission denied to read grouped field 'secret_list'",
            response.json()["errors"][0]["message"],
        )
        self.assertEqual(self.project.secret_list_reads, 0)

    def test_denied_ordinary_parent_relation_fails_before_group_companion_read(
        self,
    ) -> None:
        self._create_shared_projects()
        self.project.Permission.denied_fields.add("secret_list")

        response = self.query(
            """
            query {
              groupingProjectList {
                items {
                  secretGroups(groupBy: ["name"]) {
                    items { name }
                  }
                }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupingProjectList"]["items"],
            [{"secretGroups": None}, {"secretGroups": None}],
        )
        self.assertEqual(self.project.secret_list_reads, 0)

    def test_denied_grouped_key_fails_before_group_key_read(self) -> None:
        self._create_shared_projects()
        self.project.Permission.denied_fields.add("name")

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items { amount }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "Permission denied to read grouping key 'name'",
            response.json()["errors"][0]["message"],
        )

    def test_denied_unselected_aggregate_order_field_fails_before_read(self) -> None:
        self._create_shared_projects()
        self.project.Permission.denied_fields.add("amount")

        response = self.query(
            """
            query {
              groupingProjectGroups(
                groupBy: ["name"]
                orderBy: [{field: amount, direction: DESC}]
              ) {
                items { name }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn("amount", response.json()["errors"][0]["message"])
        self.assertIn("amount", self.project.Permission.checks)

    def test_denied_grouped_relation_fails_before_relation_read(self) -> None:
        self._create_shared_projects()
        self.project.Permission.denied_fields.add("commercial")

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items { commercialList { items { name } } }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "Permission denied to read grouped field 'commercial'",
            response.json()["errors"][0]["message"],
        )

    def test_nested_relation_field_permission_is_preserved_for_grouped_items(
        self,
    ) -> None:
        self._create_shared_projects()
        self.commercial.Permission.denied_fields.add("name")

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items { commercialList { items { name } } }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupingProjectGroups"]["items"],
            [{"commercialList": {"items": [{"name": None}]}}],
        )
        self.assertIn("name", self.commercial.Permission.checks)

    def test_bucket_property_without_list_suffix_has_explicit_grouped_pages(
        self,
    ) -> None:
        self._create_shared_projects()
        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items {
                  secretsList { items { name } pageInfo { totalCount } }
                  secretsGroups(groupBy: ["name"]) { items { name } }
                }
              }
              ordinary: __type(name: "GroupingProjectType") {
                fields { name type { name kind } }
              }
            }
            """
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]
        self.assertEqual(
            payload["groupingProjectGroups"]["items"],
            [
                {
                    "secretsList": {
                        "items": [{"name": "Commercial"}],
                        "pageInfo": {"totalCount": 1},
                    },
                    "secretsGroups": {"items": [{"name": "Commercial"}]},
                }
            ],
        )
        ordinary_fields = {
            field["name"]: field["type"] for field in payload["ordinary"]["fields"]
        }
        self.assertEqual(
            ordinary_fields["secrets"],
            {"name": "GroupingCommercialType", "kind": "OBJECT"},
        )

    def test_all_null_optional_property_page_does_not_fall_back_to_all_children(
        self,
    ) -> None:
        self._create_shared_projects()
        self.commercial.Factory.create(
            name="Unrelated",
            group_key="unrelated",
            ignore_permission=True,
        )

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items {
                  optionalSecretList {
                    items { name }
                    pageInfo { totalCount }
                  }
                }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupingProjectGroups"]["items"],
            [
                {
                    "optionalSecretList": {
                        "items": [],
                        "pageInfo": {"totalCount": 0},
                    }
                }
            ],
        )

    def test_property_relation_grouping_rejects_unknown_child_key(self) -> None:
        self._create_shared_projects()

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items {
                  secretGroups(groupBy: ["missingKey"]) {
                    items { name }
                  }
                }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "'missingKey' is not an eligible grouping key",
            response.json()["errors"][0]["message"],
        )

    def test_property_relation_grouping_normalizes_camel_case_child_key(self) -> None:
        self._create_shared_projects()
        self.commercial.Factory.create(
            name="Second",
            group_key="shared",
            ignore_permission=True,
        )

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items {
                  secretGroups(groupBy: ["groupKey"]) {
                    items { groupKey }
                    pageInfo { totalCount }
                  }
                }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupingProjectGroups"]["items"],
            [
                {
                    "secretGroups": {
                        "items": [{"groupKey": "shared"}],
                        "pageInfo": {"totalCount": 1},
                    }
                }
            ],
        )

    def test_property_relation_grouping_checks_child_key_permission(self) -> None:
        self._create_shared_projects()
        self.commercial.Permission.denied_fields.add("group_key")

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items {
                  secretGroups(groupBy: ["groupKey"]) {
                    items { name }
                  }
                }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "Permission denied to read grouping key 'group_key'",
            response.json()["errors"][0]["message"],
        )

    def test_unsupported_grouped_object_field_returns_field_error(self) -> None:
        self._create_shared_projects()

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items { rawPayload }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "raw_payload is not available for grouped results",
            response.json()["errors"][0]["message"],
        )
        self.assertEqual(self.project.raw_payload_reads, 0)

    def test_tuple_and_set_grouped_properties_fail_before_getters_run(self) -> None:
        self._create_shared_projects()
        self.project.Factory.create(
            name="Null",
            amount=None,
            count=7,
            commercial=self.commercial.all()[0],
            ignore_permission=True,
        )

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items { tupleValues setValues }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        messages = [error["message"] for error in response.json()["errors"]]
        self.assertTrue(
            any(
                "tuple_values is not available for grouped results" in message
                for message in messages
            )
        )
        self.assertTrue(
            any(
                "set_values is not available for grouped results" in message
                for message in messages
            )
        )
        self.assertEqual(self.project.tuple_reads, 0)
        self.assertEqual(self.project.set_reads, 0)

    def test_measurement_tuple_and_set_grouped_properties_fail_before_getters_run(
        self,
    ) -> None:
        self._create_shared_projects()
        self.project.Factory.create(
            name="Null",
            amount=None,
            count=7,
            commercial=self.commercial.all()[0],
            ignore_permission=True,
        )

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items {
                  tupleMeasurements { value unit }
                  setMeasurements { value unit }
                }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        messages = [error["message"] for error in response.json()["errors"]]
        self.assertTrue(
            any(
                "tuple_measurements is not available for grouped results" in message
                for message in messages
            )
        )
        self.assertTrue(
            any(
                "set_measurements is not available for grouped results" in message
                for message in messages
            )
        )
        self.assertEqual(self.project.tuple_measurement_reads, 0)
        self.assertEqual(self.project.set_measurement_reads, 0)

    def test_grouped_measurement_scalar_and_list_properties_preserve_output(
        self,
    ) -> None:
        self._create_shared_projects()

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items {
                  measurementScalar(targetUnit: "centimeter") { value unit }
                  measurementList(targetUnit: "centimeter") { value unit }
                }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupingProjectGroups"]["items"],
            [
                {
                    "measurementScalar": {"value": 500.0, "unit": "centimeter"},
                    "measurementList": [
                        {"value": 100.0, "unit": "centimeter"},
                        {"value": 200.0, "unit": "centimeter"},
                    ],
                }
            ],
        )
        self.assertEqual(self.project.measurement_scalar_reads, 2)
        self.assertEqual(self.project.measurement_list_reads, 2)

    def test_primitive_list_grouped_property_concatenates_member_values(self) -> None:
        self._create_shared_projects()

        response = self.query(
            """
            query {
              groupingProjectGroups(groupBy: ["name"]) {
                items { primitiveList }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["groupingProjectGroups"]["items"],
            [{"primitiveList": ["first", "second"]}],
        )
        self.assertEqual(self.project.primitive_list_reads, 2)
