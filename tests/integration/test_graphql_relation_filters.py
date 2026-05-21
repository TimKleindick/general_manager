# type: ignore
from __future__ import annotations


from django.contrib.auth import get_user_model
from django.db import models
from django.test import override_settings
from django.utils.crypto import get_random_string

from general_manager.bucket.base_bucket import Bucket
from general_manager.api.graphql import GraphQL
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class GraphQLRelationFilterIntegrationTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        settings_override = override_settings(
            GENERAL_MANAGER={"GRAPHQL_FILTER_RELATION_DEPTH": 2}
        )
        settings_override.enable()
        cls.addClassCleanup(settings_override.disable)

        class ChangeRequest(GeneralManager):
            title: str
            change_request_feasibility_list: Bucket[ChangeRequestFeasibility]

            class Interface(DatabaseInterface):
                title = models.CharField(max_length=100)

        class ChangeRequestFeasibility(GeneralManager):
            score: int
            change_request: ChangeRequest
            change_request_team_list: Bucket[ChangeRequestTeam]

            class Interface(DatabaseInterface):
                score = models.IntegerField(default=0)
                change_request = models.ForeignKey(
                    "general_manager.ChangeRequest",
                    on_delete=models.CASCADE,
                )

        class ChangeRequestTeam(GeneralManager):
            name: str
            size: int
            change_request_feasibility: ChangeRequestFeasibility

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                size = models.IntegerField(default=0)
                change_request_feasibility = models.ForeignKey(
                    "general_manager.ChangeRequestFeasibility",
                    on_delete=models.CASCADE,
                )

        cls.ChangeRequest = ChangeRequest
        cls.ChangeRequestFeasibility = ChangeRequestFeasibility
        cls.ChangeRequestTeam = ChangeRequestTeam
        cls.general_manager_classes = [
            ChangeRequest,
            ChangeRequestFeasibility,
            ChangeRequestTeam,
        ]

    def setUp(self):
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username=f"relation-filter-{get_random_string(8)}",
            password=password,
        )
        self.client.login(username=self.user.username, password=password)

        self.primary = self.ChangeRequest.create(
            creator_id=None,
            title="Primary",
            ignore_permission=True,
        )
        self.secondary = self.ChangeRequest.create(
            creator_id=None,
            title="Secondary",
            ignore_permission=True,
        )
        self.high_feasibility = self.ChangeRequestFeasibility.create(
            creator_id=None,
            score=9,
            change_request=self.primary,
            ignore_permission=True,
        )
        self.low_feasibility = self.ChangeRequestFeasibility.create(
            creator_id=None,
            score=2,
            change_request=self.secondary,
            ignore_permission=True,
        )
        self.ChangeRequestTeam.create(
            creator_id=None,
            name="Large Team",
            size=6,
            change_request_feasibility=self.high_feasibility,
            ignore_permission=True,
        )
        self.ChangeRequestTeam.create(
            creator_id=None,
            name="Small Team",
            size=2,
            change_request_feasibility=self.low_feasibility,
            ignore_permission=True,
        )

    def _titles_from_response(self, response) -> list[str]:
        self.assertResponseNoErrors(response)
        payload = response.json()
        return [item["title"] for item in payload["data"]["changerequestList"]["items"]]

    def test_filters_by_direct_foreign_key_relation(self):
        query = """
        query {
            changerequestfeasibilityList(filter: {
                changeRequest: { title: "Primary" }
            }) {
                items {
                    id
                    score
                    changeRequest { title }
                }
            }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["changerequestfeasibilityList"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["score"], 9)
        self.assertEqual(items[0]["changeRequest"]["title"], "Primary")

    def test_filters_by_reverse_relation_any(self):
        query = """
        query {
            changerequestList(filter: {
                changeRequestFeasibilityList: { any: { score_Gte: 7 } }
            }) {
                items { id title }
            }
        }
        """

        response = self.query(query)

        self.assertEqual(self._titles_from_response(response), ["Primary"])

    def test_filters_by_nested_reverse_relation_any(self):
        query = """
        query {
            changerequestList(filter: {
                changeRequestFeasibilityList: {
                    any: {
                        changeRequestTeamList: {
                            any: { size_Gte: 5 }
                        }
                    }
                }
            }) {
                items { id title }
            }
        }
        """

        response = self.query(query)

        self.assertEqual(self._titles_from_response(response), ["Primary"])

    def test_filters_by_reverse_relation_none(self):
        query = """
        query {
            changerequestList(filter: {
                changeRequestFeasibilityList: { none: { score_Gte: 7 } }
            }) {
                items { id title }
            }
        }
        """

        response = self.query(query)

        self.assertEqual(self._titles_from_response(response), ["Secondary"])

    def test_exclude_rejects_reverse_relation_none(self):
        query = """
        query {
            changerequestList(exclude: {
                changeRequestFeasibilityList: { none: { score_Gte: 7 } }
            }) {
                items { id title }
            }
        }
        """

        response = self.query(query)

        payload = response.json()
        self.assertIn("errors", payload)
        self.assertIn(
            "`none` relation filters are not supported inside `exclude` inputs.",
            payload["errors"][0]["message"],
        )

    def test_relation_filter_depth_two_exposes_second_level_relation(self):
        parent_filter = GraphQL.graphql_filter_type_registry[
            "ChangeRequestFilterTypeDepth2"
        ]
        relation_type = parent_filter._meta.fields[
            "change_request_feasibility_list"
        ].type
        child_filter = relation_type._meta.fields["any"].type

        self.assertIn("change_request_team_list", child_filter._meta.fields)


class GraphQLRelationFilterDepthOneTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        settings_override = override_settings(
            GENERAL_MANAGER={"GRAPHQL_FILTER_RELATION_DEPTH": 1}
        )
        settings_override.enable()
        cls.addClassCleanup(settings_override.disable)

        class DepthOneChangeRequest(GeneralManager):
            title: str
            depth_one_feasibility_list: Bucket[DepthOneFeasibility]

            class Interface(DatabaseInterface):
                title = models.CharField(max_length=100)

        class DepthOneFeasibility(GeneralManager):
            score: int
            change_request: DepthOneChangeRequest
            depth_one_team_list: Bucket[DepthOneTeam]

            class Interface(DatabaseInterface):
                score = models.IntegerField(default=0)
                change_request = models.ForeignKey(
                    "general_manager.DepthOneChangeRequest",
                    on_delete=models.CASCADE,
                )

        class DepthOneTeam(GeneralManager):
            size: int
            feasibility: DepthOneFeasibility

            class Interface(DatabaseInterface):
                size = models.IntegerField(default=0)
                feasibility = models.ForeignKey(
                    "general_manager.DepthOneFeasibility",
                    on_delete=models.CASCADE,
                )

        cls.general_manager_classes = [
            DepthOneChangeRequest,
            DepthOneFeasibility,
            DepthOneTeam,
        ]

    def test_relation_filter_depth_one_hides_second_level_relation(self):
        parent_filter = GraphQL.graphql_filter_type_registry[
            "DepthOneChangeRequestFilterTypeDepth1"
        ]
        relation_type = parent_filter._meta.fields["depth_one_feasibility_list"].type
        child_filter = relation_type._meta.fields["any"].type

        self.assertNotIn("depth_one_team_list", child_filter._meta.fields)
