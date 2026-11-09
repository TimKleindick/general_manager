from collections import Counter
from datetime import date
from types import SimpleNamespace
from typing import ClassVar, Literal, cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.utils.crypto import get_random_string

from general_manager.as_of import as_of
from general_manager.api.property import graph_ql_property
from general_manager.api.graphql_resolvers import create_list_resolver
from general_manager.bucket._materialized_bucket import MaterializedBucket
from general_manager.bucket.calculation_bucket import (
    CalculationBucket,
    FrozenCalculationLookupError,
)
from general_manager.bucket.database_bucket import QuerysetFilteringError
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

        class SoftDeleteSubject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

                class Meta:
                    use_soft_delete = True

        class ReportCalculation(GeneralManager):
            chat_exposed = True
            authorization_checks: ClassVar[list[tuple[int, date]]] = []
            result_evaluations: ClassVar[list[tuple[int, date]]] = []
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
                    return ReportCalculation.permission_constraints

                def get_read_permission_plan(self) -> ReadPermissionPlan:
                    return ReadPermissionPlan(
                        filters=ReportCalculation.permission_constraints,
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
        cls.SoftDeleteSubject = SoftDeleteSubject
        cls.Calculation = ReportCalculation
        cls.general_manager_classes = [
            AuthorizationSubject,
            SoftDeleteSubject,
            ReportCalculation,
        ]

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
        self.Calculation.permission_constraints = [{"filter": {}, "exclude": {}}]

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

    def test_chat_uses_the_same_instance_authorization_before_getters(self) -> None:
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
        self.assertEqual(
            Counter(self.Calculation.authorization_checks),
            Counter(
                (subject.id, date(2026, month, day))
                for subject in [self.subject, self.other_subject]
                for month, day in []
            ),
        )

    def test_permission_exclude_cannot_replace_same_key_user_exclude(self) -> None:
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

    def test_exact_subject_identity_reaches_input_domain_before_enumeration(
        self,
    ) -> None:
        """A project-style identity scope is applied before its domain is read."""
        subject_class = self.Subject

        class ExactSubjectDomain(MaterializedBucket):
            def __iter__(self):
                raise AssertionError

            def filter(self, **kwargs):
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

    def test_mixed_input_and_result_exclude_keeps_its_conjunction(self) -> None:
        """A computed member prevents its exclusion group becoming constructor scope."""
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )

        result = resolver(
            None,
            SimpleNamespace(context=SimpleNamespace(user=self.user)),
            exclude={"period": date(2026, 2, 28), "result": 3},
        )

        assert result["pageInfo"]["total_count"] == 6
        assert [item.period.month for item in result["items"]] == [2, 3, 4, 2, 3, 4]

    def test_deny_all_never_enumerates_constructor_domain(self) -> None:
        """Static denial returns an empty result without evaluating input domains."""
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(), self.Calculation
        )
        input_field = self.Calculation.Interface.input_fields["subject"]

        def unexpected_domain():
            raise AssertionError

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

    def test_constructor_scope_intersects_existing_calculation_constraints(
        self,
    ) -> None:
        """Client inputs cannot replace an existing calculation bucket constraint."""
        resolver = create_list_resolver(
            lambda *_: self.Calculation.filter(period=date(2026, 2, 28)),
            self.Calculation,
        )

        result = resolver(
            None,
            SimpleNamespace(context=SimpleNamespace(user=self.user)),
            filter={"period": date(2026, 1, 31)},
        )

        assert result["pageInfo"]["total_count"] == 0
        assert self.Calculation.authorization_checks == []

    def test_constructor_scope_only_narrows_an_exact_materialized_source(self) -> None:
        """A client constructor input cannot introduce rows absent from a snapshot."""
        subject_row = self.Calculation(
            subject=self.subject.id,
            period=date(2026, 2, 28),
        )
        other_row = self.Calculation(
            subject=self.other_subject.id,
            period=date(2026, 2, 28),
        )
        snapshot = self.Calculation.all().with_instances([subject_row, other_row])
        resolver = create_list_resolver(lambda *_: snapshot, self.Calculation)

        result = resolver(
            None,
            SimpleNamespace(context=SimpleNamespace(user=self.user)),
            filter={"subject__id": self.subject.id},
        )

        assert result["pageInfo"]["total_count"] == 1
        assert result["items"] == [subject_row]
        assert self.Calculation.authorization_checks == [
            (self.subject.id, date(2026, 2, 28))
        ]

    def test_nested_manager_constraints_traverse_native_values(self) -> None:
        """Nested manager predicates retain values rather than raw identity mappings."""

        class Leaf(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(int)
                name = Input(str)

        class Middle(GeneralManager):
            class Interface(CalculationInterface):
                leaf = Input(Leaf)

        class Top(GeneralManager):
            class Interface(CalculationInterface):
                middle = Input(Middle)
                axis = Input(int)

        row = Top(middle={"leaf": {"id": 1, "name": "secret"}}, axis=1)
        matcher = CalculationBucket(Top).filter(middle__leaf__name="secret")
        exclusion = CalculationBucket(Top).exclude(middle__leaf__name="secret")

        assert row.middle.leaf.name == "secret"
        assert list(matcher.filter_instances([row])) == [row]
        assert list(exclusion.filter_instances([row])) == []

        resolver = create_list_resolver(
            lambda *_: Top.all().with_instances([row]),
            Top,
        )
        permission_plan = ReadPermissionPlan(
            filters=[{"filter": {}, "exclude": {"middle__leaf__name": "secret"}}],
            requires_instance_check=False,
        )
        with patch(
            "general_manager.api.graphql_resolvers.get_read_permission_filter",
            return_value=permission_plan,
        ):
            result = resolver(
                None,
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                filter={"axis": 1},
            )

        assert result["pageInfo"]["total_count"] == 0

    def test_frozen_permission_exclude_preserves_database_lookup_semantics(
        self,
    ) -> None:
        """A frozen snapshot delegates related Django lookups to the source manager."""
        row = self.Calculation(subject=self.subject.id, period=date(2026, 2, 28))
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all().with_instances([row]),
            self.Calculation,
        )
        info = SimpleNamespace(context=SimpleNamespace(user=self.user))
        for lookup, expected in (
            ("subject__name__iexact", "subject"),
            ("subject__name__isnull", False),
        ):
            with self.subTest(lookup=lookup):
                permission_plan = ReadPermissionPlan(
                    filters=[{"filter": {}, "exclude": {lookup: expected}}],
                    requires_instance_check=False,
                )
                with patch(
                    "general_manager.api.graphql_resolvers.get_read_permission_filter",
                    return_value=permission_plan,
                ):
                    unscoped = resolver(None, info)
                    scoped = resolver(
                        None,
                        info,
                        filter={"period": date(2026, 2, 28)},
                    )

                assert unscoped["pageInfo"]["total_count"] == 0
                assert scoped["pageInfo"]["total_count"] == 0

    def test_scoped_permission_excludes_retain_orm_text_lookup_semantics(self) -> None:
        """Constructor snapshots preserve the active database's lookup semantics."""
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(),
            self.Calculation,
        )
        info = SimpleNamespace(context=SimpleNamespace(user=self.user))
        native_source = self.Subject.Interface._model.objects.filter(
            pk__in=[self.subject.id, self.other_subject.id]
        )
        for lookup in (
            "subject__name__contains",
            "subject__name__startswith",
            "subject__name__endswith",
        ):
            with self.subTest(lookup=lookup):
                expected_ids = set(
                    native_source.exclude(
                        **{lookup.removeprefix("subject__"): "subject"}
                    ).values_list("pk", flat=True)
                )
                permission_plan = ReadPermissionPlan(
                    filters=[{"filter": {}, "exclude": {lookup: "subject"}}],
                    requires_instance_check=False,
                )
                with patch(
                    "general_manager.api.graphql_resolvers.get_read_permission_filter",
                    return_value=permission_plan,
                ):
                    result = resolver(
                        None,
                        info,
                        filter={"period": date(2026, 2, 28)},
                    )

                assert result["pageInfo"]["total_count"] == len(expected_ids)
                assert {row.subject.id for row in result["items"]} == expected_ids

    def test_scoped_permission_exclude_rejects_unknown_orm_lookup(self) -> None:
        """An invalid native permission predicate fails rather than retaining rows."""
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all(),
            self.Calculation,
        )
        permission_plan = ReadPermissionPlan(
            filters=[{"filter": {}, "exclude": {"subject__name__unknown": "x"}}],
            requires_instance_check=False,
        )
        with (
            patch(
                "general_manager.api.graphql_resolvers.get_read_permission_filter",
                return_value=permission_plan,
            ),
            self.assertRaises(QuerysetFilteringError),
        ):
            resolver(
                None,
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                filter={"period": date(2026, 2, 28)},
            )

    def test_custom_related_getter_uses_frozen_value_not_database_source(self) -> None:
        """A replaced ORM getter remains a deferred predicate on exact rows."""
        row = self.Calculation(subject=self.subject.id, period=date(2026, 2, 28))
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all().with_instances([row]),
            self.Calculation,
        )
        info = SimpleNamespace(context=SimpleNamespace(user=self.user))
        with patch.object(self.Subject, "name", property(lambda _subject: "Masked")):
            filtered = resolver(None, info, filter={"subject__name": "Masked"})
            excluded = resolver(None, info, exclude={"subject__name": "Masked"})

        assert filtered["items"] == [row]
        assert excluded["pageInfo"]["total_count"] == 0

    def test_frozen_exclude_does_not_reopen_related_global_filter(self) -> None:
        """Exact related predicates use the admitted row, not its global manager."""
        row = self.Calculation(subject=self.subject.id, period=date(2026, 2, 28))
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all().with_instances([row]),
            self.Calculation,
        )
        permission_plan = ReadPermissionPlan(
            filters=[{"filter": {}, "exclude": {"subject__name__contains": "Subject"}}],
            requires_instance_check=False,
        )
        with (
            patch(
                "general_manager.api.graphql_resolvers.get_read_permission_filter",
                return_value=permission_plan,
            ),
            patch.object(
                self.Subject,
                "filter",
                side_effect=AssertionError("global owner filter called"),
            ),
        ):
            result = resolver(
                None,
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                filter={"period": date(2026, 2, 28)},
            )

        assert result["pageInfo"]["total_count"] == 0

    def test_frozen_exclude_uses_historical_related_scalar(self) -> None:
        """An admitted historical row is never matched against its live value."""
        model = self.Subject.Interface._model
        persisted = model.objects.get(id=self.subject.id)
        snapshot = persisted.history.latest().history_date
        persisted.name = "Changed live value"
        persisted.save()

        with as_of(snapshot):
            row = self.Calculation(
                subject=self.subject.identification["id"],
                period=date(2026, 2, 28),
            )
            assert row.subject.name == "Subject"
            resolver = create_list_resolver(
                lambda *_: self.Calculation.all().with_instances([row]),
                self.Calculation,
            )
            permission_plan = ReadPermissionPlan(
                filters=[{"filter": {}, "exclude": {"subject__name": "Subject"}}],
                requires_instance_check=False,
            )
            with patch(
                "general_manager.api.graphql_resolvers.get_read_permission_filter",
                return_value=permission_plan,
            ):
                result = resolver(
                    None,
                    SimpleNamespace(context=SimpleNamespace(user=self.user)),
                    filter={"period": date(2026, 2, 28)},
                )

        assert model.objects.get(id=self.subject.id).name == "Changed live value"
        assert result["pageInfo"]["total_count"] == 0

    def test_frozen_exclude_fails_closed_when_related_row_is_deleted(self) -> None:
        """A predicate cannot silently retain an admitted row after its source vanishes."""
        row = self.Calculation(subject=self.subject.id, period=date(2026, 2, 28))
        assert row.subject.name == "Subject"
        resolver = create_list_resolver(
            lambda *_: self.Calculation.all().with_instances([row]),
            self.Calculation,
        )
        self.Subject.Interface._model.objects.filter(id=self.subject.id).delete()
        permission_plan = ReadPermissionPlan(
            filters=[{"filter": {}, "exclude": {"subject__name": "Subject"}}],
            requires_instance_check=False,
        )

        with (
            patch(
                "general_manager.api.graphql_resolvers.get_read_permission_filter",
                return_value=permission_plan,
            ),
            self.assertRaises(FrozenCalculationLookupError),
        ):
            resolver(
                None,
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                filter={"period": date(2026, 2, 28)},
            )

    def test_frozen_exclude_matches_an_inactive_admitted_related_row(self) -> None:
        """Exact filtering keeps an admitted inactive relation in its local domain."""

        class InactiveCalculation(GeneralManager):
            class Interface(CalculationInterface):
                subject = Input(self.SoftDeleteSubject)
                axis = Input(int)

        related = self.SoftDeleteSubject.create(
            creator_id=None,
            name="Inactive Family",
            ignore_permission=True,
        )
        related.delete(ignore_permission=True)
        inactive = self.SoftDeleteSubject.filter(
            include_inactive=True, name="Inactive Family"
        ).first()
        assert inactive is not None
        row = InactiveCalculation(subject=inactive.id, axis=1)
        assert row.subject.name == "Inactive Family"
        resolver = create_list_resolver(
            lambda *_: InactiveCalculation.all().with_instances([row]),
            InactiveCalculation,
        )
        permission_plan = ReadPermissionPlan(
            filters=[{"filter": {}, "exclude": {"subject__name": "Inactive Family"}}],
            requires_instance_check=False,
        )
        with patch(
            "general_manager.api.graphql_resolvers.get_read_permission_filter",
            return_value=permission_plan,
        ):
            result = resolver(
                None,
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                filter={"axis": 1},
            )

        assert result["pageInfo"]["total_count"] == 0

    def test_null_manager_paths_validate_suffixes_and_preserve_scalar_none(
        self,
    ) -> None:
        """Frozen paths fail closed while nullable scalar equality remains exact."""

        class Leaf(GeneralManager):
            class Interface(CalculationInterface):
                name = Input(str, required=False)

        class Middle(GeneralManager):
            class Interface(CalculationInterface):
                leaf = Input(Leaf, required=False)

        class Top(GeneralManager):
            class Interface(CalculationInterface):
                middle = Input(Middle)

        null_relation = Top(middle={"leaf": None})
        null_scalar = Top(middle={"leaf": {"name": None}})

        assert (
            list(
                CalculationBucket(Top)
                .filter(middle__leaf__name="secret")
                .filter_instances([null_relation])
            )
            == []
        )
        assert list(
            CalculationBucket(Top)
            .exclude(middle__leaf__name="secret")
            .filter_instances([null_relation])
        ) == [null_relation]
        assert list(
            CalculationBucket(Top)
            .filter(middle__leaf__name__exact=None)
            .filter_instances([null_scalar])
        ) == [null_scalar]
        assert (
            list(
                CalculationBucket(Top)
                .exclude(middle__leaf__name__exact=None)
                .filter_instances([null_scalar])
            )
            == []
        )

        resolver = create_list_resolver(
            lambda *_: Top.all().with_instances([null_scalar]), Top
        )
        permission_plan = ReadPermissionPlan(
            filters=[
                {
                    "filter": {},
                    "exclude": {"middle__leaf__name__exact": None},
                }
            ],
            requires_instance_check=False,
        )
        with patch(
            "general_manager.api.graphql_resolvers.get_read_permission_filter",
            return_value=permission_plan,
        ):
            result = resolver(
                None,
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
            )

        assert result["pageInfo"]["total_count"] == 0

        class NativeMiddle(GeneralManager):
            class Interface(CalculationInterface):
                leaf = Input(self.Subject, required=False)

        class NativeTop(GeneralManager):
            class Interface(CalculationInterface):
                middle = Input(NativeMiddle)

        null_native_relation = NativeTop(middle={"leaf": None})
        assert (
            list(
                CalculationBucket(NativeTop)
                .filter(middle__leaf__name__iexact="subject")
                .filter_instances([null_native_relation])
            )
            == []
        )
        with self.assertRaises(FrozenCalculationLookupError):
            CalculationBucket(NativeTop).exclude(
                middle__leaf__name__unsupported="subject"
            ).filter_instances([null_native_relation])

        class RootNullable(GeneralManager):
            class Interface(CalculationInterface):
                subject = Input(self.Subject, required=False)

        null_root_relation = RootNullable(subject=None)
        assert (
            list(
                CalculationBucket(RootNullable)
                .filter(subject__name__iexact="subject")
                .filter_instances([null_root_relation])
            )
            == []
        )
        with self.assertRaises(FrozenCalculationLookupError):
            CalculationBucket(RootNullable).exclude(
                subject__name__unsupported="subject"
            ).filter_instances([null_root_relation])

        for lookup in (
            "middle__leaf__unknown",
            "middle__leaf__name__unsupported",
        ):
            with self.subTest(lookup=lookup):
                with self.assertRaises(FrozenCalculationLookupError):
                    CalculationBucket(Top).exclude(
                        **{lookup: "secret"}
                    ).filter_instances([null_relation])
