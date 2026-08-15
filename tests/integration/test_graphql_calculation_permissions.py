from datetime import date
from typing import ClassVar, Literal, cast

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
            authorization_checks: ClassVar[list[tuple[int, date]]] = []
            result_evaluations: ClassVar[list[tuple[int, date]]] = []

            class Interface(CalculationInterface):
                subject = Input(
                    AuthorizationSubject,
                    possible_values=lambda: AuthorizationSubject.all(),
                )
                period = Input(
                    date,
                    possible_values=[
                        date(2026, 1, 31),
                        date(2026, 2, 28),
                        date(2026, 3, 31),
                        date(2026, 4, 30),
                    ],
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
                    ReportCalculation.authorization_checks.append(
                        (calculation.subject.id, calculation.period)
                    )
                    return calculation.period.month > 1

            @graph_ql_property(filterable=True)
            def result(self) -> int:
                type(self).result_evaluations.append((self.subject.id, self.period))
                return self.period.month

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
        self.other_subject = self.Subject.Factory.create(name="Other subject")
        self.Calculation.authorization_checks.clear()
        self.Calculation.result_evaluations.clear()

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
                "items": [
                    {"period": "2026-02-28", "result": 2},
                    {"period": "2026-03-31", "result": 3},
                    {"period": "2026-04-30", "result": 4},
                    {"period": "2026-02-28", "result": 2},
                    {"period": "2026-03-31", "result": 3},
                    {"period": "2026-04-30", "result": 4},
                ],
                "pageInfo": {"totalCount": 6},
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
                "items": [
                    {"period": "2026-02-28", "result": 2},
                    {"period": "2026-03-31", "result": 3},
                    {"period": "2026-04-30", "result": 4},
                ],
                "pageInfo": {"totalCount": 3},
            },
        )

    def test_input_filter_is_applied_before_authorization(self) -> None:
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
        assert response.json()["data"]["reportCalculationList"]["pageInfo"] == {
            "totalCount": 3
        }
        assert self.Calculation.authorization_checks == [
            (self.subject.id, date(2026, 1, 31)),
            (self.subject.id, date(2026, 2, 28)),
            (self.subject.id, date(2026, 3, 31)),
            (self.subject.id, date(2026, 4, 30)),
        ]

    def test_computed_property_filter_is_applied_after_authorization(self) -> None:
        response = self.query(
            """
            query($subjectId: ID!) {
                reportCalculationList(
                    page: 1,
                    pageSize: 1,
                    filter: {subject: {id: $subjectId}}
                    exclude: {result: 2}
                ) {
                    items { period }
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
                "items": [{"period": "2026-03-31"}],
                "pageInfo": {"totalCount": 2},
            },
        )
        self.assertEqual(
            self.Calculation.result_evaluations,
            [
                (self.subject.id, date(2026, 2, 28)),
                (self.subject.id, date(2026, 3, 31)),
                (self.subject.id, date(2026, 4, 30)),
            ],
        )
        self.assertEqual(
            self.Calculation.authorization_checks,
            [
                (self.subject.id, date(2026, 1, 31)),
                (self.subject.id, date(2026, 2, 28)),
                (self.subject.id, date(2026, 3, 31)),
                (self.subject.id, date(2026, 4, 30)),
            ],
        )

    def test_input_exclude_is_applied_before_authorization(self) -> None:
        response = self.query(
            """
            query {
                reportCalculationList(
                    page: 1,
                    pageSize: 10,
                    exclude: {period: "2026-01-31"}
                ) {
                    items { period }
                    pageInfo { totalCount }
                }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["reportCalculationList"],
            {
                "items": [
                    {"period": "2026-02-28"},
                    {"period": "2026-03-31"},
                    {"period": "2026-04-30"},
                    {"period": "2026-02-28"},
                    {"period": "2026-03-31"},
                    {"period": "2026-04-30"},
                ],
                "pageInfo": {"totalCount": 6},
            },
        )
        self.assertEqual(
            self.Calculation.authorization_checks,
            [
                (self.subject.id, date(2026, 2, 28)),
                (self.subject.id, date(2026, 3, 31)),
                (self.subject.id, date(2026, 4, 30)),
                (self.other_subject.id, date(2026, 2, 28)),
                (self.other_subject.id, date(2026, 3, 31)),
                (self.other_subject.id, date(2026, 4, 30)),
            ],
        )
