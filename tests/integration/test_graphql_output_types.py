from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar, Literal, cast
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase
from graphql import ExecutionResult

from general_manager.api.graphql import GraphQL, GraphQLMutationMap
from general_manager.api.registry import GraphQLRegistry
from general_manager.api.graphql_type import (
    GraphQLType,
    _restore_registered_graphql_types,
    get_registered_graphql_types,
)
from general_manager.api.property import (
    GraphQLProperty,
    _TYPE_HINT_UNRESOLVED,
    graph_ql_property,
)
from general_manager.bootstrap import handle_graph_ql
from general_manager.interface import CalculationInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.measurement.measurement import Measurement
from general_manager.permission.base_permission import (
    BasePermission,
    ReadPermissionPlan,
)

if TYPE_CHECKING:

    class IntegrationProjectHour(GraphQLType):
        task_id: int
        total_hours: Measurement
        users: list[str]

    class IntegrationProjectDetails(GraphQLType):
        hour: IntegrationProjectHour
        label: str

    class IntegrationPermissionDetails(GraphQLType):
        label: str
        related: PermissionedRelatedManager


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
    def details(self) -> "IntegrationPermissionDetails | None":
        details_type = cast(
            type[IntegrationPermissionDetails],
            globals()["IntegrationPermissionDetails"],
        )
        return details_type(
            label="permissioned",
            related=PermissionedRelatedManager(related_id=7),
        )


class ProjectSummary(GeneralManager):
    class Interface(CalculationInterface):
        project_id = Input(int)

    @graph_ql_property(cache="none")
    def hours(self) -> "list[IntegrationProjectHour]":
        project_hour_type = cast(
            type[IntegrationProjectHour],
            globals()["IntegrationProjectHour"],
        )
        return [
            project_hour_type(
                task_id=3,
                total_hours=Measurement(Decimal("8.5"), "h"),
                users=["Ng", "Smith"],
            )
        ]

    @graph_ql_property(cache="none")
    def optional_hour(self) -> "IntegrationProjectHour | None":
        return None

    @graph_ql_property(cache="none")
    def optional_hours(
        self,
    ) -> "list[IntegrationProjectHour | None]":
        return [None, self.hours[0]]

    @graph_ql_property(cache="none")
    def measurements(self) -> "list[Measurement]":
        return [Measurement(1, "hour"), Measurement(2, "hour")]

    @graph_ql_property(cache="none")
    def details(self) -> "IntegrationProjectDetails":
        details_type = cast(
            type[IntegrationProjectDetails],
            globals()["IntegrationProjectDetails"],
        )
        return details_type(
            hour=self.hours[0],
            label="summary",
        )


GraphQLPropertyHintSnapshot = tuple[tuple[GraphQLProperty, object], ...]


def _snapshot_graphql_property_hints(
    manager_classes: tuple[type[GeneralManager], ...],
) -> GraphQLPropertyHintSnapshot:
    return tuple(
        (property_value, property_value._graphql_type_hint)
        for manager_class in manager_classes
        for property_value in manager_class.Interface.get_graph_ql_properties().values()
    )


def _assert_graphql_property_hints(
    snapshot: GraphQLPropertyHintSnapshot,
) -> None:
    for property_value, expected_hint in snapshot:
        assert property_value._graphql_type_hint is expected_hint


def _restore_graphql_property_hints(
    snapshot: GraphQLPropertyHintSnapshot,
) -> None:
    for property_value, expected_hint in snapshot:
        property_value._graphql_type_hint = expected_hint


def _restore_graphql_registry(snapshot: GraphQLRegistry) -> None:
    GraphQL._query_class = snapshot.query_class
    GraphQL._mutation_class = snapshot.mutation_class
    GraphQL._subscription_class = snapshot.subscription_class
    GraphQL._schema = snapshot.schema
    GraphQL._mutations = cast(GraphQLMutationMap, snapshot.mutations)
    GraphQL._query_fields = snapshot.query_fields
    GraphQL._subscription_fields = snapshot.subscription_fields
    GraphQL._page_type_registry = snapshot.page_type_registry
    GraphQL._group_page_type_registry = snapshot.group_page_type_registry
    GraphQL._subscription_payload_registry = snapshot.subscription_payload_registry
    GraphQL.graphql_type_registry = snapshot.graphql_type_registry
    GraphQL.graphql_output_type_registry = snapshot.graphql_output_type_registry
    GraphQL.graphql_filter_type_registry = snapshot.graphql_filter_type_registry
    GraphQL.graphql_capability_type_registry = snapshot.graphql_capability_type_registry
    GraphQL.manager_registry = snapshot.manager_registry
    GraphQL._search_union = snapshot.search_union
    GraphQL._search_result_type = snapshot.search_result_type


def _restore_and_assert_registries(
    graphql_snapshot: GraphQLRegistry,
    declaration_snapshot: tuple[type[GraphQLType], ...],
) -> None:
    _restore_graphql_registry(graphql_snapshot)
    _restore_registered_graphql_types(declaration_snapshot)
    assert GraphQL.get_registry_snapshot() == graphql_snapshot
    assert get_registered_graphql_types() == declaration_snapshot


def _restore_module_bindings(bindings: dict[str, object]) -> None:
    for name, previous in bindings.items():
        if previous is _MISSING:
            globals().pop(name, None)
        else:
            globals()[name] = previous


_MISSING = object()


def _activate_declarations(
    declaration_snapshot: tuple[type[GraphQLType], ...],
    *required: type[GraphQLType],
) -> None:
    _restore_registered_graphql_types(
        declaration_snapshot
        + tuple(
            declaration
            for declaration in required
            if declaration not in declaration_snapshot
        )
    )


class GraphQLOutputPropertyIntegrationTests(SimpleTestCase):
    _expected_property_hints: GraphQLPropertyHintSnapshot | None = None

    def setUp(self) -> None:
        super().setUp()
        graphql_registry = GraphQL.get_registry_snapshot()
        declaration_registry = get_registered_graphql_types()
        self.addCleanup(
            _restore_and_assert_registries,
            graphql_registry,
            declaration_registry,
        )
        binding_names = ("IntegrationProjectHour", "IntegrationProjectDetails")
        previous_bindings = {
            name: globals().get(name, _MISSING) for name in binding_names
        }
        self.addCleanup(_restore_module_bindings, previous_bindings)
        GraphQL.reset_registry()

        property_hints = _snapshot_graphql_property_hints(
            (ProjectSummary, PermissionedProjectSummary)
        )
        if self.__class__._expected_property_hints is not None:
            _assert_graphql_property_hints(self.__class__._expected_property_hints)
        self.__class__._expected_property_hints = property_hints
        self.addCleanup(_restore_graphql_property_hints, property_hints)

        # Keep declarations test-local to avoid module-import registry leaks;
        # temporary module globals let postponed string hints resolve.
        project_hour = type(
            "IntegrationProjectHour",
            (GraphQLType,),
            {
                "__module__": __name__,
                "__annotations__": {
                    "task_id": int,
                    "total_hours": Measurement,
                    "users": list[str],
                },
            },
        )
        project_details = type(
            "IntegrationProjectDetails",
            (GraphQLType,),
            {
                "__module__": __name__,
                "__annotations__": {
                    "hour": project_hour,
                    "label": str,
                },
            },
        )
        globals().update(
            {
                "IntegrationProjectHour": project_hour,
                "IntegrationProjectDetails": project_details,
            }
        )
        self.project_hour = project_hour
        self.project_details = project_details
        self.assertEqual(
            get_registered_graphql_types()[-2:],
            (project_hour, project_details),
        )
        for property_value, _ in property_hints:
            property_value._graphql_type_hint = _TYPE_HINT_UNRESOLVED
        _activate_declarations(
            declaration_registry,
            project_hour,
            project_details,
        )

        with (
            patch.object(GraphQL, "register_file_upload_mutation"),
            patch.object(GraphQL, "register_search_query"),
            patch("general_manager.uploads.urls.add_file_upload_urls"),
            patch("general_manager.bootstrap.add_graphql_url"),
        ):
            handle_graph_ql([ProjectSummary])

        schema = GraphQL.get_schema()
        self.assertIsNotNone(schema)
        assert schema is not None
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

    def test_legacy_measurement_collections_convert_every_value(self) -> None:
        response = self.schema.execute(
            """
            query {
                projectSummary(projectId: 3) {
                    measurements(targetUnit: \"minute\") { value unit }
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
                    "measurements": [
                        {"value": 60.0, "unit": "minute"},
                        {"value": 120.0, "unit": "minute"},
                    ]
                }
            },
        )

    def test_late_declaration_survives_setup_and_cleanup(self) -> None:
        sentinel = type(
            "PostBaselineSentinel",
            (GraphQLType,),
            {
                "__module__": __name__,
                "__annotations__": {"marker": str},
            },
        )
        declarations = get_registered_graphql_types()
        self.assertIn(sentinel, declarations)
        self.assertIn(self.project_hour, declarations)
        self.assertIn(self.project_details, declarations)


class PermissionedGraphQLOutputIntegrationTests(SimpleTestCase):
    _expected_property_hints: GraphQLPropertyHintSnapshot | None = None
    _expected_allow_details: bool | None = None

    def setUp(self) -> None:
        super().setUp()
        graphql_registry = GraphQL.get_registry_snapshot()
        declaration_registry = get_registered_graphql_types()
        self.addCleanup(
            _restore_and_assert_registries,
            graphql_registry,
            declaration_registry,
        )
        previous_binding = globals().get("IntegrationPermissionDetails", _MISSING)
        self.addCleanup(
            _restore_module_bindings,
            {"IntegrationPermissionDetails": previous_binding},
        )
        GraphQL.reset_registry()

        property_hints = _snapshot_graphql_property_hints((PermissionedProjectSummary,))
        if self.__class__._expected_property_hints is not None:
            _assert_graphql_property_hints(self.__class__._expected_property_hints)
        self.__class__._expected_property_hints = property_hints
        self.addCleanup(_restore_graphql_property_hints, property_hints)
        previous_allow_details = PermissionedProjectSummary.Permission.allow_details
        if self.__class__._expected_allow_details is not None:
            self.assertEqual(
                PermissionedProjectSummary.Permission.allow_details,
                self.__class__._expected_allow_details,
            )
        self.__class__._expected_allow_details = previous_allow_details
        self.addCleanup(
            setattr,
            PermissionedProjectSummary.Permission,
            "allow_details",
            previous_allow_details,
        )

        # Keep declarations test-local to avoid module-import registry leaks;
        # temporary module globals let postponed string hints resolve.
        permission_details = type(
            "IntegrationPermissionDetails",
            (GraphQLType,),
            {
                "__module__": __name__,
                "__annotations__": {
                    "label": str,
                    "related": PermissionedRelatedManager,
                },
            },
        )
        globals()["IntegrationPermissionDetails"] = permission_details
        self.permission_details = permission_details
        self.assertIs(get_registered_graphql_types()[-1], permission_details)
        for property_value, _ in property_hints:
            property_value._graphql_type_hint = _TYPE_HINT_UNRESOLVED
        _activate_declarations(declaration_registry, permission_details)
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
        assert schema is not None
        self.schema = schema

    def _execute(self, query: str) -> ExecutionResult:
        return cast(
            ExecutionResult,
            self.schema.execute(
                query,
                context_value=SimpleNamespace(user=AnonymousUser()),
            ),
        )

    def test_setup_restores_shared_state_for_the_next_schema_build(self) -> None:
        self.assertTrue(PermissionedProjectSummary.Permission.allow_details)

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

        PermissionedProjectSummary.Permission.allow_details = False

        query_fields = self.schema.graphql_schema.query_type.fields
        self.assertNotIn("integrationPermissionDetails", query_fields)
        self.assertNotIn("integrationPermissionDetailsList", query_fields)

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
