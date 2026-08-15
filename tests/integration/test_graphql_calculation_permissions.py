from datetime import date
from typing import Literal, cast

from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.utils.crypto import get_random_string

from general_manager.api.property import graph_ql_property
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input
from general_manager.permission.base_permission import (
    BasePermission,
    PermissionConstraint,
    ReadPermissionPlan,
)
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLCalculationPermissions(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class AuthorizationSubject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        class ReportCalculation(GeneralManager):
            class Interface(CalculationInterface):
                subject = Input(
                    AuthorizationSubject,
                    possible_values=lambda: AuthorizationSubject.all(),
                )
                period = Input(
                    date,
                    possible_values=[date(2026, 1, 31), date(2026, 2, 28)],
                    depends_on=["subject"],
                )

            class Permission(BasePermission):
                def describe_operation_permissions(
                    self,
                    action: Literal["create", "read", "update", "delete"],
                ) -> tuple[str, ...]:
                    return ()

                def check_operation_permission(
                    self,
                    action: Literal["create", "read", "update", "delete"],
                ) -> bool:
                    return True

                def check_permission(
                    self,
                    action: Literal["create", "read", "update", "delete"],
                    attribute: str,
                ) -> bool:
                    return True

                def get_permission_filter(self) -> list[PermissionConstraint]:
                    return [{"filter": {}, "exclude": {}}]

                def get_read_permission_plan(self) -> ReadPermissionPlan:
                    return ReadPermissionPlan(
                        filters=[{"filter": {}, "exclude": {}}],
                        requires_instance_check=True,
                        instance_check_reasons=("unfilterable_read_rule",),
                    )

                def can_read_instance(self) -> bool:
                    calculation = cast(ReportCalculation, self.instance)
                    return calculation.period.month == 2

            @graph_ql_property
            def result(self) -> int:
                return 1

        cls.Subject = AuthorizationSubject
        cls.Calculation = ReportCalculation
        cls.general_manager_classes = [AuthorizationSubject, ReportCalculation]

    def setUp(self) -> None:
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="calculation-permission-user",
            password=password,
        )
        self.client.login(
            username="calculation-permission-user",
            password=password,
        )
        self.subject = self.Subject.Factory.create(name="Subject")

    def test_list_returns_only_instance_authorized_calculations(self) -> None:
        response = self.query(
            """
            query {
                reportCalculationList(page: 1, pageSize: 10) {
                    items { period result }
                    pageInfo { totalCount }
                }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["reportCalculationList"],
            {
                "items": [{"period": "2026-02-28", "result": 1}],
                "pageInfo": {"totalCount": 1},
            },
        )

    def test_filtered_list_returns_only_instance_authorized_calculations(self) -> None:
        response = self.query(
            """
            query($subjectId: ID!) {
                reportCalculationList(
                    page: 1,
                    pageSize: 10,
                    filter: {subject: {id: $subjectId}}
                ) {
                    items { period result }
                    pageInfo { totalCount }
                }
            }
            """,
            variables={"subjectId": self.subject.id},
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["reportCalculationList"],
            {
                "items": [{"period": "2026-02-28", "result": 1}],
                "pageInfo": {"totalCount": 1},
            },
        )
