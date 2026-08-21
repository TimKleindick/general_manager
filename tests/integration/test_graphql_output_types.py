from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import ClassVar, Literal
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
from general_manager.permission.base_permission import (
    BasePermission,
    ReadPermissionPlan,
)


_PRE_MODULE_DECLARATIONS = get_registered_graphql_types()


class IntegrationProjectHour(GraphQLType):
    task_id: int
    total_hours: Measurement
    users: list[str]


class IntegrationProjectDetails(GraphQLType):
    hour: IntegrationProjectHour
    label: str


class PermissionedRelatedManager(GeneralManager):
    class Permission(BasePermission):
        checks: ClassVar[list[str]] = []

        def check_permission(
            self,
            action: Literal["create", "read", "update", "delete"],
            attribute: str,
        ) -> bool:
            del action
            type(self).checks.append(attribute)
            return attribute == "allowed"

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

    class Interface(CalculationInterface):
        related_id = Input(int)

    @graph_ql_property(cache="none")
    def allowed(self) -> str:
        return "visible"

    @graph_ql_property(cache="none")
    def denied(self) -> str:
        return "hidden"


class IntegrationPermissionDetails(GraphQLType):
    label: str
    related: PermissionedRelatedManager


class PermissionedProjectSummary(GeneralManager):
    class Permission(BasePermission):
        checks: ClassVar[list[str]] = []
        allow_details: ClassVar[bool] = True

        def check_permission(
            self,
            action: Literal["create", "read", "update", "delete"],
            attribute: str,
        ) -> bool:
            del action
            type(self).checks.append(attribute)
            return attribute != "details" or type(self).allow_details

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

    class Interface(CalculationInterface):
        project_id = Input(int)

    @graph_ql_property(cache="none")
    def details(self) -> IntegrationPermissionDetails | None:
        return IntegrationPermissionDetails(
            label="permissioned",
            related=PermissionedRelatedManager(related_id=7),
        )


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


_restore_registered_graphql_types(_PRE_MODULE_DECLARATIONS)


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


def _restore_and_assert_registries(
    graphql_snapshot: object,
    declaration_snapshot: tuple[type[GraphQLType], ...],
) -> None:
    _restore_graphql_registry(graphql_snapshot)
    _restore_registered_graphql_types(declaration_snapshot)
    assert GraphQL.get_registry_snapshot() == graphql_snapshot
    assert get_registered_graphql_types() == declaration_snapshot


def _activate_declarations(
    *required: type[GraphQLType],
) -> None:
    _restore_registered_graphql_types((*_PRE_MODULE_DECLARATIONS, *required))


class GraphQLOutputPropertyIntegrationTests(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        graphql_registry = GraphQL.get_registry_snapshot()
        self.addCleanup(
            _restore_and_assert_registries,
            graphql_registry,
            _PRE_MODULE_DECLARATIONS,
        )
        GraphQL.reset_registry()
        _restore_registered_graphql_types(_PRE_MODULE_DECLARATIONS)
        self.assertEqual(get_registered_graphql_types(), _PRE_MODULE_DECLARATIONS)
        _activate_declarations(IntegrationProjectHour, IntegrationProjectDetails)

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
        self.assertNotIn("integrationProjectHour", query_fields)
        self.assertNotIn("integrationProjectHourList", query_fields)
        self.assertNotIn("integrationProjectDetails", query_fields)
        self.assertNotIn("integrationProjectDetailsList", query_fields)
        self.assertIsNotNone(
            self.schema.graphql_schema.get_type("IntegrationProjectHourType")
        )


class PermissionedGraphQLOutputIntegrationTests(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        graphql_registry = GraphQL.get_registry_snapshot()
        self.addCleanup(
            _restore_and_assert_registries,
            graphql_registry,
            _PRE_MODULE_DECLARATIONS,
        )
        GraphQL.reset_registry()
        _restore_registered_graphql_types(_PRE_MODULE_DECLARATIONS)
        self.assertEqual(get_registered_graphql_types(), _PRE_MODULE_DECLARATIONS)
        _activate_declarations(IntegrationPermissionDetails)
        PermissionedRelatedManager.Permission.checks.clear()
        PermissionedProjectSummary.Permission.checks.clear()
        PermissionedProjectSummary.Permission.allow_details = True

        with (
            patch.object(GraphQL, "register_file_upload_mutation"),
            patch.object(GraphQL, "register_search_query"),
            patch("general_manager.uploads.urls.add_file_upload_urls"),
            patch("general_manager.bootstrap.add_graphql_url"),
        ):
            handle_graph_ql([PermissionedRelatedManager, PermissionedProjectSummary])

        schema = GraphQL.get_schema()
        self.assertIsNotNone(schema)
        self.schema = schema

    def _execute(self, query: str):
        return self.schema.execute(
            query,
            context_value=SimpleNamespace(user=AnonymousUser()),
        )

    def test_nested_manager_permissions_and_owner_property_permissions_are_preserved(
        self,
    ) -> None:
        ordinary = self._execute(
            """
            query {
                permissionedProjectSummary(projectId: 3) {
                    details { label }
                }
            }
            """
        )

        self.assertIsNone(ordinary.errors)
        self.assertEqual(
            ordinary.data,
            {"permissionedProjectSummary": {"details": {"label": "permissioned"}}},
        )
        self.assertEqual(PermissionedProjectSummary.Permission.checks, ["details"])
        self.assertEqual(PermissionedRelatedManager.Permission.checks, [])

        nested = self._execute(
            """
            query {
                permissionedProjectSummary(projectId: 3) {
                    details { label related { allowed denied } }
                }
            }
            """
        )

        self.assertIsNone(nested.errors)
        self.assertEqual(
            nested.data,
            {
                "permissionedProjectSummary": {
                    "details": {
                        "label": "permissioned",
                        "related": {"allowed": "visible", "denied": None},
                    }
                }
            },
        )
        self.assertEqual(
            PermissionedProjectSummary.Permission.checks,
            ["details", "details"],
        )
        self.assertEqual(
            PermissionedRelatedManager.Permission.checks,
            ["allowed", "denied"],
        )

        query_fields = self.schema.graphql_schema.query_type.fields
        self.assertNotIn("integrationPermissionDetails", query_fields)
        self.assertNotIn("integrationPermissionDetailsList", query_fields)

        PermissionedProjectSummary.Permission.allow_details = False
        denied = self._execute(
            """
            query {
                permissionedProjectSummary(projectId: 3) {
                    details { label related { allowed } }
                }
            }
            """
        )

        self.assertIsNone(denied.errors)
        self.assertEqual(
            denied.data,
            {"permissionedProjectSummary": {"details": None}},
        )
        self.assertEqual(
            PermissionedProjectSummary.Permission.checks,
            ["details", "details", "details"],
        )
        self.assertEqual(
            PermissionedRelatedManager.Permission.checks,
            ["allowed", "denied"],
        )
