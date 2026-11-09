# ruff: noqa: S106
"""List operand authorization keeps its strict budget unless explicitly proven safe."""

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import models
from graphql import GraphQLError

from general_manager.api import graphql_resolvers
from general_manager.api.graphql_query_permissions import authorize_query_fields
from general_manager.api.graphql_resolvers import create_list_resolver
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.permission import OverrideManagerPermission
from general_manager.permission.permission_checks import permission_functions
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLListPermissionBudget(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class BudgetRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                first_filter = models.CharField(max_length=30)
                second_filter = models.CharField(max_length=30)
                rank = models.IntegerField()

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        class AdditionalBudgetRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                secret = models.CharField(max_length=100)

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                secret: ClassVar[dict[str, list[str]]] = {"read": ["isAdmin"]}

        class CustomBudgetRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

                def check_permission(self, action: str, attribute: str) -> bool:
                    return super().check_permission(action, attribute)  # type: ignore[arg-type]

        class BudgetTarget(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        class BudgetLink(GeneralManager):
            class Interface(DatabaseInterface):
                target = models.ForeignKey(
                    "general_manager.BudgetTarget", on_delete=models.CASCADE
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        class DelegatedBudgetRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                target = models.ForeignKey(
                    "general_manager.BudgetTarget", on_delete=models.CASCADE
                )

            class Permission(OverrideManagerPermission):
                __based_on__: ClassVar[str] = "target"

        cls.Record = BudgetRecord
        cls.Additional = AdditionalBudgetRecord
        cls.Custom = CustomBudgetRecord
        cls.Target = BudgetTarget
        cls.Link = BudgetLink
        cls.Delegated = DelegatedBudgetRecord
        cls.general_manager_classes = [
            BudgetRecord,
            AdditionalBudgetRecord,
            CustomBudgetRecord,
            BudgetTarget,
            BudgetLink,
            DelegatedBudgetRecord,
        ]

    def setUp(self) -> None:
        super().setUp()
        self.user = get_user_model().objects.create_user(username="budget-user")
        self.info = SimpleNamespace(context=SimpleNamespace(user=self.user))
        self.resolver = create_list_resolver(lambda *_: self.Record.all(), self.Record)

    def resolver_for(self, manager: type[GeneralManager]):
        return create_list_resolver(lambda *_: manager.all(), manager)

    def test_proven_operands_allow_large_filtered_sorted_paginated_list(self) -> None:
        """Safe fields must not consume the visit bound before filtering or paging."""
        model = self.Record.Interface._model
        model.objects.bulk_create(
            [
                model(
                    name=f"record-{rank:04d}",
                    first_filter="first",
                    second_filter="second",
                    rank=rank,
                )
                for rank in range(1005)
            ]
        )

        ranks: list[int] = []
        for page in range(1, 12):
            result = self.resolver(
                None,
                self.info,
                filter={"first_filter": "first", "second_filter": "second"},
                order_by=[{"field": "rank", "direction": "DESC"}],
                page=page,
                page_size=100,
            )
            assert result["pageInfo"] == {
                "total_count": 1005,
                "page_size": 100,
                "current_page": page,
                "total_pages": 11,
            }
            ranks.extend(item.rank for item in result["items"])

        assert ranks == list(range(1004, -1, -1))

    def test_default_mode_still_bounds_proven_field_checks(self) -> None:
        """The opt-in must not weaken callers that retain strict accounting."""
        self.Record.Factory.create(
            name="first", first_filter="first", second_filter="second", rank=1
        )
        self.Record.Factory.create(
            name="second", first_filter="first", second_filter="second", rank=2
        )

        with (
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                1,
            ),
            self.assertRaisesMessage(GraphQLError, "candidate limit"),
        ):
            authorize_query_fields(self.Record.all(), self.info, ["first_filter"])

    def test_additional_field_rules_remain_budgeted_on_opted_in_list(self) -> None:
        """A declared field restriction still consumes the lowered visit budget."""
        admin = get_user_model().objects.create_superuser(username="budget-admin")
        info = SimpleNamespace(context=SimpleNamespace(user=admin))
        for name in ("first", "second"):
            self.Additional.Factory.create(name=name, secret="shared")

        with (
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                1,
            ),
            self.assertRaisesMessage(GraphQLError, "candidate limit"),
        ):
            self.resolver_for(self.Additional)(None, info, filter={"secret": "shared"})

    def test_custom_permission_remains_budgeted_on_opted_in_list(self) -> None:
        """An executable policy remains unknown even when it currently grants access."""
        for name in ("first", "second"):
            self.Custom.Factory.create(name=name)

        with (
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                1,
            ),
            self.assertRaisesMessage(GraphQLError, "candidate limit"),
        ):
            self.resolver_for(self.Custom)(None, self.info, filter={"name": "first"})

    def test_delegated_permission_remains_budgeted_on_opted_in_list(self) -> None:
        """Delegation can tighten a field at runtime and therefore stays bounded."""
        target = self.Target.Factory.create(name="target")
        for name in ("first", "second"):
            self.Delegated.Factory.create(name=name, target=target)

        with (
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                1,
            ),
            self.assertRaisesMessage(GraphQLError, "candidate limit"),
        ):
            self.resolver_for(self.Delegated)(None, self.info, filter={"name": "first"})

    def test_denied_nonmatching_row_is_checked_before_filtering_and_paging(
        self,
    ) -> None:
        """Proven fields stay checked even when the denied row cannot be returned."""
        self.Record.Factory.create(
            name="visible", first_filter="first", second_filter="second", rank=2
        )
        self.Record.Factory.create(
            name="hidden", first_filter="other", second_filter="other", rank=1
        )

        def check(instance: GeneralManager, _info: object, field: str) -> bool:
            return not (instance.name == "hidden" and field == "first_filter")

        with (
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                1,
            ),
            patch.object(graphql_resolvers, "check_read_permission", side_effect=check),
            self.assertRaisesMessage(GraphQLError, "query field 'first_filter'"),
        ):
            self.resolver(
                None,
                self.info,
                filter={"first_filter": "first"},
                order_by=[{"field": "rank", "direction": "DESC"}],
                page=1,
                page_size=1,
            )

    def test_repeated_candidate_permission_result_is_not_cached(self) -> None:
        """The second visit must observe a new denial for the same object."""
        record = self.Record.Factory.create(
            name="repeat", first_filter="first", second_filter="second", rank=1
        )

        with (
            patch.object(
                graphql_resolvers, "check_read_permission", side_effect=[True, False]
            ) as check,
            self.assertRaisesMessage(GraphQLError, "query field 'name'"),
        ):
            authorize_query_fields(
                [record, record],
                self.info,
                ["name"],
                budget_additional_permissions_only=True,
            )
        assert check.call_count == 2

    def test_repeated_scalar_operands_keep_runtime_checks_per_candidate_occurrence(
        self,
    ) -> None:
        record = self.Record.Factory.create(
            name="repeat", first_filter="first", second_filter="second", rank=1
        )
        native = graphql_resolvers.check_read_permission
        with patch.object(
            graphql_resolvers, "check_read_permission", wraps=native
        ) as check:
            authorize_query_fields(
                [record, record],
                self.info,
                ["rank", "rank__gte", "rank__lte", "name"],
                budget_additional_permissions_only=True,
            )
        assert [call.args[2] for call in check.call_args_list] == [
            "name",
            "rank",
            "rank",
            "rank",
            "name",
            "rank",
            "rank",
            "rank",
        ]

    def test_unknown_repeated_scalar_operands_keep_individual_checks(self) -> None:
        record = self.Custom.Factory.create(name="custom")
        with (
            patch.object(
                graphql_resolvers, "check_read_permission", side_effect=[True, False]
            ) as check,
            self.assertRaisesMessage(GraphQLError, "query field 'name'"),
        ):
            authorize_query_fields(
                [record],
                self.info,
                ["name", "name__exact"],
                budget_additional_permissions_only=True,
            )
        assert check.call_count == 2

    def test_declarative_callback_denial_or_error_is_not_collapsed(self) -> None:
        """Equal field/row declarations do not prove callback purity."""
        record = self.Record.Factory.create(
            name="callback", first_filter="first", second_filter="second", rank=1
        )
        for error in (None, RuntimeError("second operand failed")):
            with self.subTest(error=error):
                calls = 0

                def callback(_instance, _user, _config, *, error=error):
                    nonlocal calls
                    calls += 1
                    if calls == 2 and error is not None:
                        raise error
                    return calls == 1

                with (
                    patch.dict(
                        permission_functions,
                        {
                            "volatileOperand": {
                                "permission_method": callback,
                                "permission_filter": lambda *_: None,
                            }
                        },
                    ),
                    patch.object(
                        self.Record.Permission, "__read__", ["volatileOperand"]
                    ),
                    self.assertRaisesMessage(
                        RuntimeError if error else GraphQLError,
                        "second operand failed" if error else "query field 'rank'",
                    ),
                ):
                    authorize_query_fields(
                        [record],
                        self.info,
                        ["rank", "rank__gte"],
                        budget_additional_permissions_only=True,
                    )
                assert calls == 2

    def test_repeated_scalar_denial_still_covers_nonmatching_and_offpage_rows(
        self,
    ) -> None:
        for rank in (1, 2):
            self.Record.Factory.create(
                name=str(rank), first_filter="first", second_filter="second", rank=rank
            )

        def check(instance: GeneralManager, _info: object, field: str) -> bool:
            return not (instance.rank == 2 and field == "rank")

        with (
            patch.object(graphql_resolvers, "check_read_permission", side_effect=check),
            self.assertRaisesMessage(GraphQLError, "query field 'rank'"),
        ):
            self.resolver(
                None,
                self.info,
                filter={"rank__gte": 1, "rank__lte": 1},
                order_by=[{"field": "rank", "direction": "ASC"}],
                page=1,
                page_size=1,
            )

    def test_runtime_permission_override_remains_budgeted(self) -> None:
        """The concrete candidate policy, rather than its manager declaration, wins."""

        class RuntimePermission(OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]

            def check_permission(self, action: str, attribute: str) -> bool:
                return super().check_permission(action, attribute)  # type: ignore[arg-type]

        record = self.Record.Factory.create(
            name="runtime", first_filter="first", second_filter="second", rank=1
        )
        record.Permission = RuntimePermission

        with (
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                1,
            ),
            self.assertRaisesMessage(GraphQLError, "candidate limit"),
        ):
            authorize_query_fields(
                [record, record],
                self.info,
                ["name"],
                budget_additional_permissions_only=True,
            )

    def test_rule_change_between_visits_starts_consuming_budget(self) -> None:
        """A later restrictive rule must not reuse the first visit's safe proof."""
        admin = get_user_model().objects.create_superuser(username="budget-rule-admin")
        info = SimpleNamespace(context=SimpleNamespace(user=admin))
        record = self.Record.Factory.create(
            name="mutable", first_filter="first", second_filter="second", rank=1
        )
        native = graphql_resolvers.check_read_permission
        checks = 0

        def tighten_after_first_check(
            instance: GeneralManager, request_info: object, field: str
        ) -> bool:
            nonlocal checks
            result = native(instance, request_info, field)
            checks += 1
            if checks == 1:
                self.Record.Permission.name = {"read": ["isAdmin"]}
            return result

        try:
            with (
                patch(
                    "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                    1,
                ),
                patch.object(
                    graphql_resolvers,
                    "check_read_permission",
                    side_effect=tighten_after_first_check,
                ),
                self.assertRaisesMessage(GraphQLError, "candidate limit"),
            ):
                authorize_query_fields(
                    [record, record, record],
                    info,
                    ["name"],
                    budget_additional_permissions_only=True,
                )
        finally:
            del self.Record.Permission.name

        assert checks == 2

    def test_denied_related_target_still_rejects_in_opted_in_mode(self) -> None:
        """Skipping a proven field's charge never skips the target row gate."""
        target = self.Target.Factory.create(name="target")
        link = self.Link.Factory.create(target=target)

        with (
            patch.object(
                self.Target.Permission, "can_read_instance", return_value=False
            ),
            self.assertRaisesMessage(GraphQLError, "traverse query relation"),
        ):
            authorize_query_fields(
                [link],
                self.info,
                ["target__name"],
                budget_additional_permissions_only=True,
            )
