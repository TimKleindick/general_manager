from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_type import (
    GraphQLType,
    _restore_registered_graphql_types,
    get_registered_graphql_types,
)
from general_manager.api.property import graph_ql_property
from general_manager.bootstrap import handle_graph_ql
from general_manager.interface import CalculationInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.measurement.measurement import Measurement


class IntegrationProjectHour(GraphQLType):
    task_id: int
    total_hours: Measurement
    users: list[str]


class IntegrationProjectDetails(GraphQLType):
    hour: IntegrationProjectHour
    label: str


class ProjectSummary(GeneralManager):
    class Interface(CalculationInterface):
        project_id = Input(int)

    @graph_ql_property(cache="none")
    def hours(self) -> list[IntegrationProjectHour]:
        return [
            IntegrationProjectHour(
                task_id=3,
                total_hours=Measurement(Decimal("8.5"), "h"),
                users=["Ng", "Smith"],
            )
        ]

    @graph_ql_property(cache="none")
    def optional_hour(self) -> IntegrationProjectHour | None:
        return None

    @graph_ql_property(cache="none")
    def optional_hours(self) -> list[IntegrationProjectHour | None]:
        return [None, self.hours[0]]

    @graph_ql_property(cache="none")
    def details(self) -> IntegrationProjectDetails:
        return IntegrationProjectDetails(
            hour=self.hours[0],
            label="summary",
        )


def _restore_graphql_registry(snapshot: object) -> None:
    GraphQL._query_class = snapshot.query_class  # type: ignore[attr-defined]
    GraphQL._mutation_class = snapshot.mutation_class  # type: ignore[attr-defined]
    GraphQL._subscription_class = snapshot.subscription_class  # type: ignore[attr-defined]
    GraphQL._schema = snapshot.schema  # type: ignore[attr-defined]
    GraphQL._mutations = snapshot.mutations  # type: ignore[attr-defined]
    GraphQL._query_fields = snapshot.query_fields  # type: ignore[attr-defined]
    GraphQL._subscription_fields = snapshot.subscription_fields  # type: ignore[attr-defined]
    GraphQL._page_type_registry = snapshot.page_type_registry  # type: ignore[attr-defined]
    GraphQL._subscription_payload_registry = (  # type: ignore[attr-defined]
        snapshot.subscription_payload_registry  # type: ignore[attr-defined]
    )
    GraphQL.graphql_type_registry = snapshot.graphql_type_registry  # type: ignore[attr-defined]
    GraphQL.graphql_output_type_registry = (  # type: ignore[attr-defined]
        snapshot.graphql_output_type_registry  # type: ignore[attr-defined]
    )
    GraphQL.graphql_filter_type_registry = (  # type: ignore[attr-defined]
        snapshot.graphql_filter_type_registry  # type: ignore[attr-defined]
    )
    GraphQL.graphql_capability_type_registry = (  # type: ignore[attr-defined]
        snapshot.graphql_capability_type_registry  # type: ignore[attr-defined]
    )
    GraphQL.manager_registry = snapshot.manager_registry  # type: ignore[attr-defined]
    GraphQL._search_union = snapshot.search_union  # type: ignore[attr-defined]
    GraphQL._search_result_type = snapshot.search_result_type  # type: ignore[attr-defined]


class GraphQLOutputPropertyIntegrationTests(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        graphql_registry = GraphQL.get_registry_snapshot()
        declaration_registry = get_registered_graphql_types()
        self.addCleanup(_restore_graphql_registry, graphql_registry)
        self.addCleanup(_restore_registered_graphql_types, declaration_registry)
        GraphQL.reset_registry()

        with (
            patch.object(GraphQL, "register_file_upload_mutation"),
            patch.object(GraphQL, "register_search_query"),
            patch("general_manager.uploads.urls.add_file_upload_urls"),
            patch("general_manager.bootstrap.add_graphql_url"),
        ):
            handle_graph_ql([ProjectSummary])

        schema = GraphQL.get_schema()
        self.assertIsNotNone(schema)
        self.schema = schema

    def test_graphql_properties_expose_output_objects_without_root_operations(
        self,
    ) -> None:
        response = self.schema.execute(
            """
            query {
                projectSummary(projectId: 3) {
                    hours { taskId totalHours { value unit } users }
                    optionalHour { taskId }
                    optionalHours { taskId }
                    details { label hour { taskId } }
                }
            }
            """,
            context_value=SimpleNamespace(user=AnonymousUser()),
        )

        self.assertIsNone(response.errors)
        self.assertEqual(
            response.data,
            {
                "projectSummary": {
                    "hours": [
                        {
                            "taskId": 3,
                            "totalHours": {"value": 8.5, "unit": "hour"},
                            "users": ["Ng", "Smith"],
                        }
                    ],
                    "optionalHour": None,
                    "optionalHours": [None, {"taskId": 3}],
                    "details": {
                        "label": "summary",
                        "hour": {"taskId": 3},
                    },
                }
            },
        )

        query_fields = self.schema.graphql_schema.query_type.fields
        self.assertIn("projectSummary", query_fields)
        self.assertNotIn("projectHour", query_fields)
        self.assertNotIn("projectHourList", query_fields)
        self.assertIsNotNone(
            self.schema.graphql_schema.get_type("IntegrationProjectHourType")
        )
