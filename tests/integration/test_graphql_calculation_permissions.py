from collections import Counter
from datetime import date
import json
from typing import ClassVar, Literal, cast
from types import SimpleNamespace
from unittest.mock import patch

from graphql import GraphQLError

from django.contrib.auth import get_user_model
from django.db.models import CharField, DateField
from django.utils.crypto import get_random_string

from general_manager.api.property import graph_ql_property
from general_manager.api.graphql_resolvers import create_list_resolver
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input
from general_manager.bucket._materialized_bucket import MaterializedBucket
from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.permission import OverrideManagerPermission
from general_manager.permission.base_permission import (
    BasePermission,
    PermissionConstraint,
    ReadPermissionPlan,
)
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLCalculationPermissions(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """Declare real related managers and calculation permission boundaries."""

        class AuthorizationSubject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                secret = CharField(max_length=100, default="classified")
                exact = CharField(max_length=100, default="private-exact")
                born_on = DateField(default=date(2026, 1, 1))

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                secret: ClassVar[dict] = {"read": ["isAdmin"]}
                exact: ClassVar[dict] = {"read": ["isAdmin"]}

        class ReportCalculation(GeneralManager):
            chat_exposed = True
            authorization_checks: ClassVar[list[tuple[int, date]]] = []
            result_evaluations: ClassVar[list[tuple[int, date]]] = []
            denied_query_fields: ClassVar[set[str]] = set()
            permission_constraints: ClassVar[list[PermissionConstraint]] = [
                {"filter": {}, "exclude": {}}
            ]

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
                    """Keep these tests driven by explicit row and field permission gates."""
                    return ()

                def check_operation_permission(
                    self,
                    action: Literal["create", "read", "update", "delete"],
                ) -> bool:
                    """Allow the operation while retaining independent row and field gates."""
                    return True

                def check_permission(
                    self,
                    action: Literal["create", "read", "update", "delete"],
                    attribute: str,
                ) -> bool:
                    """Let tests deny an operand independently from calculation membership."""
                    return attribute not in ReportCalculation.denied_query_fields

                def get_permission_filter(self) -> list[PermissionConstraint]:
                    """Expose the current native permission constraint alternatives."""
                    return ReportCalculation.permission_constraints

                def get_read_permission_plan(self) -> ReadPermissionPlan:
                    """Exercise native prefilters followed by the final row gate."""
                    return ReadPermissionPlan(
                        filters=ReportCalculation.permission_constraints,
                        requires_instance_check=True,
                        instance_check_reasons=("unfilterable_read_rule",),
                    )

                def can_read_instance(self) -> bool:
                    """Record candidates and reject calculations from January."""
                    calculation = cast(ReportCalculation, self.instance)
                    ReportCalculation.authorization_checks.append(
                        (calculation.subject.id, calculation.period)
                    )
                    return calculation.period.month > 1

            @graph_ql_property(filterable=True)
            def result(self) -> int:
                """Record computed reads to detect evaluation before row authorization."""
                type(self).result_evaluations.append((self.subject.id, self.period))
                return self.period.month

        class NormalizedConstruction(GeneralManager):
            authorization_checks: ClassVar[list[str]] = []
            denied_codes: ClassVar[set[str]] = set()
            permission_constraints: ClassVar[list[PermissionConstraint]] = [
                {"filter": {}, "exclude": {}}
            ]

            class Interface(CalculationInterface):
                code = Input(str, possible_values=["A", "B"], normalizer=str.upper)

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

                def get_read_permission_plan(self) -> ReadPermissionPlan:
                    """Retain native prefilters and an observable final instance gate."""
                    return ReadPermissionPlan(
                        filters=NormalizedConstruction.permission_constraints,
                        requires_instance_check=True,
                        instance_check_reasons=("unfilterable_read_rule",),
                    )

                def can_read_instance(self) -> bool:
                    """Record the scoped codes checked before releasing results."""
                    code = self.instance.code
                    NormalizedConstruction.authorization_checks.append(code)
                    return code not in NormalizedConstruction.denied_codes

            @graph_ql_property(filterable=True)
            def result(self) -> int:
                """Expose distinct computed values for atomic mixed exclusion tests."""
                return 1 if self.code == "A" else 2

        cls.Normalized = NormalizedConstruction
        cls.Subject = AuthorizationSubject
        cls.Calculation = ReportCalculation
        cls.general_manager_classes = [
            AuthorizationSubject,
            ReportCalculation,
            NormalizedConstruction,
        ]

    def setUp(self) -> None:
        """Reset input rows and authorization observations for each query."""
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
        self.subject = self.Subject.Factory.create(
            name="Subject", exact="private-exact", born_on=date(2026, 1, 1)
        )
        self.other_subject = self.Subject.Factory.create(
            name="Other subject", exact="other-exact", born_on=date(2026, 2, 1)
        )
        self.Calculation.authorization_checks.clear()
        self.Calculation.result_evaluations.clear()
        self.Calculation.denied_query_fields.clear()
        self.Calculation.permission_constraints = [{"filter": {}, "exclude": {}}]
        self.Normalized.authorization_checks.clear()
        self.Normalized.denied_codes.clear()
        self.Normalized.permission_constraints = [{"filter": {}, "exclude": {}}]

    def test_list_returns_only_instance_authorized_calculations(self) -> None:
        """List membership and counts contain only row-authorized calculations."""
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

    def test_chat_uses_the_same_instance_authorization_before_getters(self) -> None:
        """Chat inherits GraphQL row authorization before computed getters run."""
        from general_manager.chat.tools import ScopeChatContext, query

        context = ScopeChatContext.from_scope({"user": self.user})
        result = query(
            manager="ReportCalculation",
            fields=["period", "result"],
            filters={},
            limit=10,
            context=context,
        )
        self.assertEqual(result["total_count"], 6)
        self.assertEqual([row["result"] for row in result["data"]], [2, 3, 4, 2, 3, 4])
        self.assertTrue(self.Calculation.authorization_checks)
        self.assertTrue(self.Calculation.result_evaluations)
        self.assertTrue(
            all(period.month > 1 for _, period in self.Calculation.result_evaluations)
        )

    def test_filtered_list_returns_only_instance_authorized_calculations(self) -> None:
        """An identity filter preserves the final calculation row gate."""
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

    def test_input_filter_bounds_candidate_authorization(self) -> None:
        """Constructor identity scope bounds rows checked before pagination."""
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
        self.assertEqual(
            Counter(self.Calculation.authorization_checks),
            Counter(
                (subject.id, date(2026, month, day))
                for subject in [self.subject]
                for month, day in [(1, 31), (2, 28), (3, 31), (4, 30)]
            ),
        )

    def test_computed_property_filter_is_applied_after_authorization(self) -> None:
        """Computed predicates never read values on row-denied calculations."""
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
            Counter(self.Calculation.authorization_checks),
            Counter(
                (subject.id, date(2026, month, day))
                for subject in [self.subject]
                for month, day in [(1, 31), (2, 28), (3, 31), (4, 30)]
            ),
        )

    def test_input_exclude_bounds_candidate_authorization(self) -> None:
        """Safe constructor exclusions remove combinations before row checks."""
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
            Counter(self.Calculation.authorization_checks),
            Counter(
                (subject.id, date(2026, month, day))
                for subject in [self.subject, self.other_subject]
                for month, day in [(2, 28), (3, 31), (4, 30)]
            ),
        )

    def test_permission_filter_cannot_replace_same_key_user_filter(self) -> None:
        """Conflicting permission and user filters intersect the frozen scope."""
        self.Calculation.permission_constraints = [
            {
                "filter": {"period": date(2026, 2, 28)},
                "exclude": {},
            }
        ]

        response = self.query(
            """
            query {
                reportCalculationList(
                    filter: {period: "2026-01-31"}
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
            {"items": [], "pageInfo": {"totalCount": 0}},
        )
        self.assertEqual(self.Calculation.authorization_checks, [])

    def test_permission_exclude_cannot_replace_same_key_user_exclude(self) -> None:
        """Permission exclusions accumulate without widening constructor scope."""
        self.Calculation.permission_constraints = [
            {
                "filter": {},
                "exclude": {"period": date(2026, 2, 28)},
            }
        ]

        response = self.query(
            """
            query {
                reportCalculationList(
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
                    {"period": "2026-03-31"},
                    {"period": "2026-04-30"},
                    {"period": "2026-03-31"},
                    {"period": "2026-04-30"},
                ],
                "pageInfo": {"totalCount": 4},
            },
        )
        self.assertEqual(
            Counter(self.Calculation.authorization_checks),
            Counter(
                (subject.id, date(2026, month, day))
                for subject in [self.subject, self.other_subject]
                for month, day in [(3, 31), (4, 30)]
            ),
        )

    def test_manager_id_aliases_bound_authorization_candidates(self) -> None:
        """Manager identity aliases restrict the same constructor combinations."""
        resolver = create_list_resolver(
            lambda _parent, _include_inactive: self.Calculation.all(),
            self.Calculation,
        )
        info = type(
            "Info",
            (),
            {"context": type("Context", (), {"user": self.user})()},
        )()

        for lookup, value in (
            ("subject_id", self.subject.id),
            ("subject_id__in", [self.subject.id]),
        ):
            with self.subTest(lookup=lookup):
                self.Calculation.authorization_checks.clear()

                result = resolver(object(), info, filter={lookup: value})

                self.assertEqual(result["pageInfo"]["total_count"], 3)
                self.assertEqual(
                    Counter(self.Calculation.authorization_checks),
                    Counter(
                        (subject.id, date(2026, month, day))
                        for subject in [self.subject]
                        for month, day in [(1, 31), (2, 28), (3, 31), (4, 30)]
                    ),
                )

    def test_constructor_identity_and_date_range_bound_the_permission_budget(self):
        """Identity and date bounds limit field decisions within the same budget."""
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        info = SimpleNamespace(context=SimpleNamespace(user=self.user))
        with patch(
            "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
            8,
        ):
            result = resolver(
                None,
                info,
                filter={
                    "subject__id": self.subject.id,
                    "period__gte": date(2026, 3, 1),
                    "period__lte": date(2026, 4, 30),
                },
            )
        assert result["pageInfo"]["total_count"] == 2
        assert [item.period for item in result["items"]] == [
            date(2026, 3, 31),
            date(2026, 4, 30),
        ]
        assert self.Calculation.authorization_checks == [
            (self.subject.id, date(2026, 3, 31)),
            (self.subject.id, date(2026, 4, 30)),
        ]

    def test_exact_identity_filter_reaches_input_domain_before_enumeration(self):
        """A required identity predicate reaches the native domain before reads."""
        subject_class = self.Subject

        class ExactSubjectDomain(MaterializedBucket):
            def __iter__(self):
                """Reject domain enumeration before its exact identity scope is applied."""
                message = "subject domain requires an exact id filter"
                raise AssertionError(message)

            def filter(self, **kwargs):
                """Forward only requests containing an exact native identity predicate."""
                assert "id" in kwargs or "id__exact" in kwargs
                return subject_class.filter(**kwargs)

        input_field = self.Calculation.Interface.input_fields["subject"]
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        info = SimpleNamespace(context=SimpleNamespace(user=self.user))
        with patch.object(
            input_field,
            "possible_values",
            lambda: ExactSubjectDomain(subject_class, []),
        ):
            result = resolver(None, info, filter={"subject__id": self.subject.id})
        assert result["pageInfo"]["total_count"] == 3

    def test_related_protected_field_is_checked_before_matching_or_nonmatching_filter(
        self,
    ):
        """Denied related operands reject both matching and nonmatching probes."""
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        info = SimpleNamespace(context=SimpleNamespace(user=self.user))
        for value in ("classified", "absent"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(GraphQLError, "Permission denied"):
                    resolver(None, info, filter={"subject__secret": value})
        assert self.Calculation.result_evaluations == []

    def test_scoped_input_still_requires_its_field_permission(self):
        """Constructor restriction does not bypass the owning input field gate."""
        self.Calculation.denied_query_fields.add("subject")
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        with self.assertRaisesRegex(GraphQLError, "Permission denied"):
            resolver(
                None,
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                filter={"subject__id": self.subject.id},
            )

    def test_related_filter_checks_the_conditionally_denied_nonmatch(self):
        """Related field permissions cover authorized nonmatching candidates too."""
        original = self.Subject.Permission.check_permission

        def check(permission, action, attribute):
            """Deny the nonmatching related field to detect authorization of hits only."""
            if attribute == "name" and permission.instance.id == self.other_subject.id:
                return False
            return original(permission, action, attribute)

        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        with (
            patch.object(self.Subject.Permission, "check_permission", check),
            self.assertRaisesRegex(GraphQLError, "Permission denied"),
        ):
            resolver(
                None,
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                filter={"subject__name": "Subject"},
            )

    def test_related_field_named_exact_cannot_narrow_before_authorization(self):
        """A protected related field named exact remains a deferred operand."""
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        for value in ("private-exact", "absent"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(GraphQLError, "Permission denied"):
                    resolver(
                        None,
                        SimpleNamespace(context=SimpleNamespace(user=self.user)),
                        filter={"subject__exact": value},
                    )

    def test_authorized_related_field_named_exact_keeps_native_filter_semantics(self):
        """A real related `exact` field matches only after operand authorization."""
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        info = SimpleNamespace(context=SimpleNamespace(user=self.user))
        cases = (
            ("filter", "private-exact", ["Subject"] * 3),
            ("filter", "absent", []),
            ("exclude", "private-exact", ["Other subject"] * 3),
            ("exclude", "absent", ["Subject"] * 3 + ["Other subject"] * 3),
        )
        for operation, value, expected in cases:
            with self.subTest(operation=operation, value=value):
                result = resolver(None, info, **{operation: {"subject__exact": value}})
                assert [item.subject.name for item in result["items"]] == expected
                assert result["pageInfo"]["total_count"] == len(expected)

    def test_deferred_identity_predicate_uses_replaced_related_getter(self):
        """Deferred identity filters observe the exposed value, not stored keys."""
        source_id = self.subject.identification["id"]
        exposed_id = source_id + 1000
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        info = SimpleNamespace(context=SimpleNamespace(user=self.user))
        cases = (
            ("filter", exposed_id, ["Subject"] * 3),
            ("filter", source_id, []),
            ("exclude", exposed_id, ["Other subject"] * 3),
            ("exclude", source_id, ["Subject"] * 3 + ["Other subject"] * 3),
        )
        with patch.object(
            self.Subject,
            "id",
            property(lambda subject: subject.identification["id"] + 1000),
        ):
            for operation, value, expected in cases:
                with self.subTest(operation=operation, value=value):
                    result = resolver(None, info, **{operation: {"subject__id": value}})
                    assert [item.subject.name for item in result["items"]] == expected
                    assert result["pageInfo"]["total_count"] == len(expected)

    def test_related_date_predicates_cast_json_values_in_frozen_rows(self):
        """Raw JSON date operands retain native scalar and comparison semantics."""
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        info = SimpleNamespace(context=SimpleNamespace(user=self.user))
        cases = (
            ({"subject__born_on": "2026-01-01"}, ["Subject"] * 3),
            ({"subject__born_on__exact": "2026-01-01"}, ["Subject"] * 3),
            ({"subject__born_on__exact": "2026-03-01"}, []),
            ({"subject__born_on__in": ["2026-02-01"]}, ["Other subject"] * 3),
            (
                {
                    "subject__born_on__gte": "2026-01-15",
                    "subject__born_on__lte": "2026-02-15",
                },
                ["Other subject"] * 3,
            ),
        )
        for filters, expected in cases:
            with self.subTest(filters=filters):
                result = resolver(None, info, filter=json.dumps(filters))
                assert [item.subject.name for item in result["items"]] == expected
                assert result["pageInfo"]["total_count"] == len(expected)

    def test_snapshot_identity_matching_keeps_the_native_query_free_shortcut(self):
        """Proven identity predicates keep exact instances without ORM reads."""
        calculation = self.Calculation(
            subject=self.subject.id, period=date(2026, 2, 28)
        )
        assert calculation.subject.identification == self.subject.identification
        predicate = CalculationBucket(self.Calculation).filter(
            subject__id=self.subject.id
        )

        with self.assertNumQueries(0):
            result = list(predicate.filter_instances([calculation, calculation]))

        assert len(result) == 2
        assert result[0] is calculation and result[1] is calculation

    def test_snapshot_identity_membership_retains_native_container_grammar(self):
        """Membership containers remain containers during related scalar casting."""
        calculation = self.Calculation(
            subject=self.subject.id, period=date(2026, 2, 28)
        )
        identifier = self.subject.id
        for values in ([identifier], {identifier}, range(identifier, identifier + 1)):
            with self.subTest(container=type(values).__name__):
                predicate = CalculationBucket(self.Calculation).filter(
                    subject__id__in=values
                )
                native = (
                    self.Calculation.filter(
                        subject__id__in=values, period__gte=date(2026, 2, 1)
                    )
                    .filter(subject__id__in=[identifier])
                    .exclude(period=date(2026, 4, 30))
                )
                assert [item.period for item in native] == [
                    date(2026, 2, 28),
                    date(2026, 3, 31),
                ]
                selected = list(predicate.filter_instances([calculation, calculation]))
                assert len(selected) == 2
                assert selected[0] is calculation and selected[1] is calculation

        for value in (identifier, str(identifier)):
            with self.subTest(noncontainer=value):
                predicate = CalculationBucket(self.Calculation).filter(
                    subject__id__in=value
                )
                assert list(predicate.filter_instances([calculation])) == []

    def test_protected_result_filter_is_rejected_before_getters(self):
        """Denied computed operands fail before their getters affect membership."""
        self.Calculation.denied_query_fields.add("result")
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        for value in (2, 9):
            with self.subTest(value=value):
                with self.assertRaisesRegex(GraphQLError, "Permission denied"):
                    resolver(
                        None,
                        SimpleNamespace(context=SimpleNamespace(user=self.user)),
                        filter={"subject__id": self.subject.id, "result": value},
                    )
        assert self.Calculation.result_evaluations == []

    def test_mixed_input_and_result_exclude_keeps_its_conjunction(self):
        """A mixed exclusion removes only combinations matching every predicate."""
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        result = resolver(
            None,
            SimpleNamespace(context=SimpleNamespace(user=self.user)),
            exclude={"period": date(2026, 2, 28), "result": 3},
        )
        # No row is simultaneously in February and has March's result.
        assert result["pageInfo"]["total_count"] == 6
        assert [item.period.month for item in result["items"]] == [2, 3, 4, 2, 3, 4]

    def test_deny_all_never_enumerates_constructor_domain(self):
        """Static denial returns an empty result without evaluating input domains."""
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        input_field = self.Calculation.Interface.input_fields["subject"]

        def unexpected_domain():
            """Fail if filtering exact instances tries to enumerate an input domain."""
            message = "deny-all must not evaluate a domain"
            raise AssertionError(message)

        with (
            patch.object(input_field, "possible_values", unexpected_domain),
            patch.object(
                self.Calculation.Permission,
                "get_read_permission_plan",
                return_value=ReadPermissionPlan(filters=[], decision="deny_all"),
            ),
        ):
            result = resolver(
                None,
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                filter={"subject__id": self.subject.id},
            )
        assert result["pageInfo"]["total_count"] == 0

    def test_filter_instances_preserves_normalizers_groups_order_and_identity(self):
        """Snapshot filtering preserves normalization, conjunctions and exact objects."""
        first = self.Normalized(code="A")
        second = self.Normalized(code="B")
        assert (first.code, second.code) == ("A", "B")
        planned = CalculationBucket(self.Normalized).exclude(code="a", result=1)

        def unexpected_domain():
            """Fail if filtering exact instances tries to enumerate an input domain."""
            message = "filtering a snapshot must not enumerate domains"
            raise AssertionError(message)

        with patch.object(
            self.Normalized.Interface.input_fields["code"],
            "possible_values",
            unexpected_domain,
        ):
            result = list(planned.filter_instances([second, first, second]))
        assert len(result) == 2
        assert result[0] is second and result[1] is second

    def test_normalized_constructor_scope_is_not_reapplied_as_raw_value(self):
        """A normalized constructor predicate is consumed exactly once."""
        resolver = create_list_resolver(
            lambda *_: self.Normalized.all(), self.Normalized
        )
        result = resolver(
            None,
            SimpleNamespace(context=SimpleNamespace(user=self.user)),
            filter={"code": "a"},
        )
        assert [item.code for item in result["items"]] == ["A"]

    def test_normalized_constructor_scope_on_an_existing_snapshot(self):
        """Existing snapshots use native Input normalization for early scope."""
        snapshot = self.Normalized.all().with_instances(
            [self.Normalized(code="A"), self.Normalized(code="B")]
        )
        resolver = create_list_resolver(lambda *_: snapshot, self.Normalized)
        result = resolver(
            None,
            SimpleNamespace(context=SimpleNamespace(user=self.user)),
            filter={"code": "a"},
        )
        assert [item.code for item in result["items"]] == ["A"]

    def test_normalized_input_in_mixed_exclude_retains_native_semantics(self):
        """A normalized mixed exclusion preserves nonmatching conjunctions."""
        resolver = create_list_resolver(
            lambda *_: self.Normalized.all(), self.Normalized
        )
        result = resolver(
            None,
            SimpleNamespace(context=SimpleNamespace(user=self.user)),
            exclude={"code": "a", "result": 2},
        )
        assert [item.code for item in result["items"]] == ["A", "B"]

    def test_normalized_permission_filter_intersects_constructor_scope(self):
        """Permission prefilters normalize values on the frozen requested scope."""
        self.Normalized.permission_constraints = [
            {"filter": {"code": "a"}, "exclude": {}}
        ]
        response = self.query(
            """{
                normalizedConstructionList(filter: {code: "a"}) {
                    items { code }
                    pageInfo { totalCount }
                }
            }"""
        )

        self.assertResponseNoErrors(response)
        assert response.json()["data"]["normalizedConstructionList"] == {
            "items": [{"code": "A"}],
            "pageInfo": {"totalCount": 1},
        }
        assert self.Normalized.authorization_checks == ["A"]

    def test_normalized_permission_alternatives_retain_final_row_gate(self):
        """Normalized OR prefilters retain the final per-instance read decision."""
        self.Normalized.permission_constraints = [
            {"filter": {"code": "a"}, "exclude": {}},
            {"filter": {"code": "b"}, "exclude": {}},
        ]
        self.Normalized.denied_codes.add("B")
        resolver = create_list_resolver(
            lambda *_: self.Normalized.all(), self.Normalized
        )
        result = resolver(
            None,
            SimpleNamespace(context=SimpleNamespace(user=self.user)),
            filter={"code__in": ["a", "b"]},
        )

        assert [item.code for item in result["items"]] == ["A"]
        assert self.Normalized.authorization_checks == ["A", "B"]

    def test_normalized_permission_exclude_intersects_constructor_exclude(self):
        """Normalized permission exclusions cannot widen a constructor snapshot."""
        self.Normalized.permission_constraints = [
            {"filter": {}, "exclude": {"code": "a"}}
        ]
        resolver = create_list_resolver(
            lambda *_: self.Normalized.all(), self.Normalized
        )
        result = resolver(
            None,
            SimpleNamespace(context=SimpleNamespace(user=self.user)),
            exclude={"code": "b"},
        )

        assert result["pageInfo"]["total_count"] == 0
        assert self.Normalized.authorization_checks == []

    def test_normalized_matching_mixed_exclude_removes_only_its_conjunction(self):
        """Mixed exclusions remove the row matching both normalized predicates."""
        resolver = create_list_resolver(
            lambda *_: self.Normalized.all(), self.Normalized
        )
        result = resolver(
            None,
            SimpleNamespace(context=SimpleNamespace(user=self.user)),
            exclude={"code": "a", "result": 1},
        )

        assert [item.code for item in result["items"]] == ["B"]
