from datetime import date
from typing import ClassVar

from django.contrib.auth import get_user_model
from django.db.models import CharField
from graphql import GraphQLResolveInfo, is_input_object_type, is_object_type

from general_manager import GraphQLType
from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_type import (
    _restore_registered_graphql_types,
    get_registered_graphql_types,
)
from general_manager.api.mutation import graph_ql_mutation
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement import Measurement
from general_manager.permission.mutation_permission import MutationPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class GraphQLTypeInputTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(
            _restore_registered_graphql_types, get_registered_graphql_types()
        )

        class PlanningUser(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=50)

        class PlanningProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=50)

        class EmployeeProjectPlan(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=50)

        class PlanningAllocationChange(GraphQLType):
            user: PlanningUser
            project: PlanningProject
            planned_weekly_hours: Measurement

        class SaveEmployeePlanningPermission(MutationPermission):
            __mutate__: ClassVar[list[str]] = ["isAuthenticated"]

            @classmethod
            def check(cls, data: dict[str, object], request_user: object) -> None:
                super().check(data, request_user)
                permission_inputs.append(data)

        cls.general_manager_classes = [
            PlanningUser,
            PlanningProject,
            EmployeeProjectPlan,
        ]
        cls.PlanningUser = PlanningUser
        cls.PlanningProject = PlanningProject
        cls.EmployeeProjectPlan = EmployeeProjectPlan
        cls.PlanningAllocationChange = PlanningAllocationChange
        cls.permission_inputs = permission_inputs = []
        cls.resolver_inputs = resolver_inputs = []

        @graph_ql_mutation(SaveEmployeePlanningPermission)
        def save_employee_planning(
            info: GraphQLResolveInfo,
            month: date,
            changes: list[PlanningAllocationChange],
            deletions: list[EmployeeProjectPlan] | None = None,
        ) -> bool:
            resolver_inputs.append((info.context.user.pk, month, changes, deletions))
            return True

    def setUp(self):
        super().setUp()
        self.permission_inputs.clear()
        self.resolver_inputs.clear()
        self.actor = get_user_model().objects.create_user(username="planning-actor")
        self.planning_user = self.PlanningUser.create(
            name="Planning user", creator_id=self.actor.pk
        )
        self.project = self.PlanningProject.create(
            name="Project", creator_id=self.actor.pk
        )
        self.plan = self.EmployeeProjectPlan.create(
            name="Plan", creator_id=self.actor.pk
        )
        self.variables = {
            "month": "2026-11-01",
            "changes": [
                {
                    "user": str(self.planning_user.id),
                    "project": str(self.project.id),
                    "plannedWeeklyHours": "12.5 h",
                }
            ],
        }
        self.mutation = """
            mutation SaveEmployeePlanning(
                $month: Date!,
                $changes: [PlanningAllocationChangeInput!]!,
                $deletions: [ID!]
            ) {
                saveEmployeePlanning(month: $month, changes: $changes, deletions: $deletions) {
                    success
                    bool
                }
            }
        """

    def test_structured_changes_reach_permission_and_resolver_as_native_values(self):
        self.client.force_login(self.actor)
        self.variables["deletions"] = [str(self.plan.id)]
        response = self.query(self.mutation, variables=self.variables)

        self.assertResponseNoErrors(response)
        assert response.json()["data"]["saveEmployeePlanning"] == {
            "success": True,
            "bool": True,
        }
        [(actor_id, month, changes, deletions)] = self.resolver_inputs
        assert actor_id == self.actor.pk
        assert month == date(2026, 11, 1)
        [change] = changes
        assert type(change) is self.PlanningAllocationChange
        assert type(change.user) is self.PlanningUser
        assert change.user.id == self.planning_user.id
        assert type(change.project) is self.PlanningProject
        assert change.project.id == self.project.id
        assert change.planned_weekly_hours == Measurement.from_string("12.5 h")
        [deleted] = deletions
        assert type(deleted) is self.EmployeeProjectPlan
        assert deleted.id == self.plan.id
        assert self.permission_inputs == [
            {"month": month, "changes": changes, "deletions": deletions}
        ]

        schema = GraphQL.get_schema().graphql_schema
        input_type = schema.get_type("PlanningAllocationChangeInput")
        assert is_input_object_type(input_type)
        assert {name: str(field.type) for name, field in input_type.fields.items()} == {
            "user": "ID!",
            "project": "ID!",
            "plannedWeeklyHours": "MeasurementScalar!",
        }
        output_type = schema.get_type("PlanningAllocationChangeType")
        assert is_object_type(output_type)
        assert str(output_type.fields["user"].type) == "PlanningUserType!"

    def test_omitted_deletions_and_permission_denial(self):
        denied = self.query(self.mutation, variables=self.variables)

        self.assertResponseHasErrors(denied)
        assert denied.json()["errors"][0]["extensions"]["code"] == "PERMISSION_DENIED"
        assert self.resolver_inputs == []

        self.client.force_login(self.actor)
        allowed = self.query(self.mutation, variables=self.variables)

        self.assertResponseNoErrors(allowed)
        [(_, _, _, deletions)] = self.resolver_inputs
        assert deletions is None
