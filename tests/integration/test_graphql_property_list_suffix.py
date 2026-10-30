"""Property annotations retain their output shape when names end in ``_list``."""

from typing import ClassVar

from django.db import models

from general_manager.api.graphql import GraphQL
from general_manager.api.property import graph_ql_property
from general_manager.bucket.base_bucket import Bucket
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input
from general_manager.permission import ManagerBasedPermission, OverrideManagerPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class GraphQLPropertyListSuffixTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class SuffixMember(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=30)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]

                def can_read_instance(self):
                    return self.instance.name != "private"

        class SuffixOwner(GeneralManager):
            class Interface(DatabaseInterface):
                mode = models.CharField(max_length=20, default="full")
                member = models.ForeignKey(
                    "general_manager.SuffixMember", on_delete=models.CASCADE
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                denied_list: ClassVar[dict[str, list[str]]] = {"read": ["isAdmin"]}

            denied_reads: ClassVar[int] = 0

            @graph_ql_property(cache="none")
            def members(self) -> list[SuffixMember]:
                return [self.member, self.member]

            @graph_ql_property(cache="none")
            def members_list(self) -> list[SuffixMember]:
                return [self.member, self.member]

            @graph_ql_property(cache="none")
            def optional_list(self) -> list[SuffixMember] | None:
                if self.mode == "null":
                    return None
                if self.mode == "empty":
                    return []
                return [self.member, self.member]

            @graph_ql_property(cache="none")
            def tuple_list(self) -> tuple[SuffixMember, ...]:
                return (self.member, self.member)

            @graph_ql_property(cache="none")
            def set_list(self) -> set[SuffixMember]:
                return set()

            @graph_ql_property(cache="none")
            def mixed(self) -> list[SuffixMember]:
                return [SuffixMember(13), self.member, SuffixMember(12), self.member]

            @graph_ql_property(cache="none")
            def mixed_list(self) -> list[SuffixMember]:
                return [SuffixMember(13), self.member, SuffixMember(12), self.member]

            @graph_ql_property(cache="none")
            def selected_list(self) -> SuffixMember:
                return self.member

            @graph_ql_property(cache="none")
            def optional_selected_list(self) -> SuffixMember | None:
                return None if self.mode == "null" else self.member

            @graph_ql_property(cache="none")
            def names_list(self) -> list[str]:
                return ["first", "second"]

            @graph_ql_property(cache="none")
            def denied_list(self) -> list[SuffixMember]:
                type(self).denied_reads += 1
                return [self.member]

            @graph_ql_property(cache="none")
            def related_list(self) -> Bucket[SuffixMember]:
                return SuffixMember.filter(id=self.member.id)

        class SuffixCalculation(GeneralManager):
            class Interface(CalculationInterface):
                owner = Input(
                    SuffixOwner, possible_values=lambda: list(SuffixOwner.all())
                )

        cls.Member = SuffixMember
        cls.Owner = SuffixOwner
        cls.general_manager_classes = [SuffixMember, SuffixOwner, SuffixCalculation]

    def setUp(self):
        super().setUp()
        self.Member.Interface._model.objects.create(pk=11, name="selected")
        self.Member.Interface._model.objects.create(pk=12, name="private")
        self.Member.Interface._model.objects.create(pk=13, name="second")
        self.Owner.Interface._model.objects.create(pk=21, member_id=11)
        self.Owner.denied_reads = 0

    def test_plain_manager_list_property_matches_property_without_suffix(self):
        response = self.query("""{
          suffixOwner(id: 21) { members { id } membersList { id } namesList }
        }""")
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["suffixOwner"],
            {
                "members": [{"id": "11"}, {"id": "11"}],
                "membersList": [{"id": "11"}, {"id": "11"}],
                "namesList": ["first", "second"],
            },
        )

    def test_singular_manager_property_is_an_object_despite_suffix(self):
        response = self.query("{ suffixOwner(id: 21) { selectedList { id } } }")
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["suffixOwner"], {"selectedList": {"id": "11"}}
        )

    def test_optional_and_collection_annotations_keep_native_list_semantics(self):
        for mode, expected in (
            ("null", None),
            ("empty", []),
            ("full", [{"id": "11"}, {"id": "11"}]),
        ):
            with self.subTest(mode=mode):
                self.Owner.Interface._model.objects.filter(pk=21).update(mode=mode)
                response = self.query("""{
                  suffixOwner(id: 21) {
                    optionalList { id } tupleList { id } setList { id }
                    optionalSelectedList { id }
                  }
                }""")
                self.assertResponseNoErrors(response)
                self.assertEqual(
                    response.json()["data"]["suffixOwner"],
                    {
                        "optionalList": expected,
                        "tupleList": [{"id": "11"}, {"id": "11"}],
                        "setList": [],
                        "optionalSelectedList": None
                        if mode == "null"
                        else {"id": "11"},
                    },
                )

    def test_manager_collection_keeps_member_read_permissions(self):
        response = self.query(
            "{ suffixOwner(id: 21) { mixed { id name } mixedList { id name } } }"
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["suffixOwner"],
            {
                "mixed": [
                    {"id": "13", "name": "second"},
                    {"id": "11", "name": "selected"},
                    {"id": "11", "name": "selected"},
                ],
                "mixedList": [
                    {"id": "13", "name": "second"},
                    {"id": "11", "name": "selected"},
                    {"id": "11", "name": "selected"},
                ],
            },
        )

    def test_property_field_permission_prevents_getter_execution(self):
        response = self.query("{ suffixOwner(id: 21) { deniedList { id } } }")
        self.assertResponseNoErrors(response)
        self.assertEqual(response.json()["data"]["suffixOwner"], {"deniedList": None})
        self.assertEqual(self.Owner.denied_reads, 0)

    def test_bucket_and_generated_reverse_relations_remain_paginated(self):
        response = self.query("""{
          suffixOwner(id: 21) {
            relatedList { items { id } pageInfo { totalCount } }
            suffixCalculationList(pageSize: 1) {
              items { owner { id } } pageInfo { totalCount }
            }
          }
          suffixMember(id: 11) {
            suffixOwnerList { items { id } pageInfo { totalCount } }
          }
        }""")
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"],
            {
                "suffixOwner": {
                    "relatedList": {
                        "items": [{"id": "11"}],
                        "pageInfo": {"totalCount": 1},
                    },
                    "suffixCalculationList": {
                        "items": [{"owner": {"id": "21"}}],
                        "pageInfo": {"totalCount": 1},
                    },
                },
                "suffixMember": {
                    "suffixOwnerList": {
                        "items": [{"id": "21"}],
                        "pageInfo": {"totalCount": 1},
                    }
                },
            },
        )

    def test_schema_distinguishes_properties_from_relation_pages(self):
        fields = GraphQL.get_schema().graphql_schema.get_type("SuffixOwnerType").fields
        for name, expected in (
            ("membersList", "[SuffixMemberType]"),
            ("selectedList", "SuffixMemberType"),
            ("namesList", "[String]"),
            ("relatedList", "SuffixMemberTypePage"),
        ):
            with self.subTest(field=name):
                self.assertEqual(str(fields[name].type), expected)
        self.assertEqual(fields["membersList"].args, {})
        self.assertEqual(fields["selectedList"].args, {})
        self.assertIn("page", fields["relatedList"].args)
