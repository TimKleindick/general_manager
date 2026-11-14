"""List membership, ordering and aggregates use readable contributions."""

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

import graphene
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection, models
from django.test.utils import CaptureQueriesContext

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_resolvers import create_list_resolver
from general_manager.api.graphql_resolvers import (
    QueryParameterPlan,
    apply_grouped_projection_sorting,
    create_normal_resolver,
)
from general_manager.api.graphql_contributors import (
    apply_list_contributors,
    authorized_group,
)
from general_manager.bucket._materialized_bucket import MaterializedBucket
from general_manager.cache.run_context import CalculationRunContext
from general_manager.api.property import graph_ql_property
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input
from general_manager.permission import OverrideManagerPermission
from general_manager.permission.permission_checks import permission_functions
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestAuthorizedContributors(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class ContributorParent(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=40)
                category = models.CharField(max_length=20, default="A")
                secret = models.IntegerField(default=1)
                exact = models.IntegerField(default=7)
                contains = models.CharField(max_length=40, default="needle")
                hours = models.IntegerField(default=10)
                cost = models.IntegerField(default=100)
                maybe = models.IntegerField(null=True)
                allow_secret = models.BooleanField(default=True)
                allow_hours = models.BooleanField(default=True)
                allow_cost = models.BooleanField(default=True)
                creator = models.ForeignKey(
                    get_user_model(), null=True, on_delete=models.SET_NULL
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                secret: ClassVar[dict] = {"read": ["contributorFlag:allow_secret"]}
                exact: ClassVar[dict] = {"read": ["contributorFlag:allow_secret"]}
                contains: ClassVar[dict] = {"read": ["contributorFlag:allow_secret"]}
                hours: ClassVar[dict] = {"read": ["contributorFlag:allow_hours"]}
                cost: ClassVar[dict] = {"read": ["contributorFlag:allow_cost"]}
                danger: ClassVar[dict] = {"read": ["contributorFlag:allow_secret"]}
                annotated: ClassVar[dict] = {"read": ["contributorFlag:allow_secret"]}

            @graph_ql_property(filterable=True)
            def danger(self) -> int:
                if not self.allow_secret:
                    message = "denied property was evaluated"
                    raise AssertionError(message)
                return self.secret

            @graph_ql_property(
                filterable=True,
                query_annotation=lambda qs: qs.annotate(annotated=models.F("secret")),
            )
            def annotated(self) -> int:
                return self.secret

        class ContributorChild(GeneralManager):
            property_reads: ClassVar[list[int]] = []

            class Interface(DatabaseInterface):
                parent = models.ForeignKey(
                    "general_manager.ContributorParent", on_delete=models.CASCADE
                )
                a = models.IntegerField(default=1)
                b = models.IntegerField(default=2)
                allow_a = models.BooleanField(default=True)
                allow_b = models.BooleanField(default=True)
                visible = models.BooleanField(default=True)
                creator = models.ForeignKey(
                    get_user_model(), null=True, on_delete=models.SET_NULL
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["matches:visible:True"]
                a: ClassVar[dict] = {"read": ["contributorFlag:allow_a"]}
                b: ClassVar[dict] = {"read": ["contributorFlag:allow_b"]}

            @graph_ql_property(filterable=True)
            def once(self) -> int:
                type(self).property_reads.append(self.id)
                return self.a

        cls.Parent, cls.Child = ContributorParent, ContributorChild

        class ContributorKeyParent(GeneralManager):
            class Interface(DatabaseInterface):
                code = models.CharField(max_length=40, primary_key=True)
                value = models.IntegerField(default=1)
                visible = models.BooleanField(default=True)

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                value: ClassVar[dict] = {"read": ["contributorFlag:visible"]}

        class ContributorKeyChild(GeneralManager):
            class Interface(DatabaseInterface):
                code = models.CharField(max_length=40, primary_key=True)
                parent = models.ForeignKey(
                    "general_manager.ContributorKeyParent", on_delete=models.CASCADE
                )
                value = models.IntegerField(default=1)
                visible = models.BooleanField(default=True)

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                value: ClassVar[dict] = {"read": ["contributorFlag:visible"]}

        cls.KeyParent, cls.KeyChild = ContributorKeyParent, ContributorKeyChild

        class ContributorCodeTarget(GeneralManager):
            class Interface(DatabaseInterface):
                code = models.CharField(max_length=40, unique=True)
                visible = models.BooleanField(default=True)
                creator = models.ForeignKey(
                    get_user_model(), null=True, on_delete=models.SET_NULL
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                code: ClassVar[dict] = {"read": ["contributorFlag:visible"]}

        class ContributorCodeSource(GeneralManager):
            class Interface(DatabaseInterface):
                parent = models.ForeignKey(
                    "general_manager.ContributorCodeTarget",
                    to_field="code",
                    on_delete=models.CASCADE,
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        cls.CodeTarget, cls.CodeSource = ContributorCodeTarget, ContributorCodeSource
        cls.general_manager_classes = [
            ContributorParent,
            ContributorChild,
            ContributorKeyParent,
            ContributorKeyChild,
            ContributorCodeTarget,
            ContributorCodeSource,
        ]
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.checks = []
        self.Child.property_reads.clear()

        def readable(instance, _user, config):
            self.checks.append((type(instance), instance.identification, config[0]))
            return bool(getattr(instance, config[0]))

        self.registry = patch.dict(
            permission_functions,
            {
                "contributorFlag": {
                    "permission_method": readable,
                    "permission_filter": lambda *_: None,
                }
            },
        )
        self.registry.start()
        self.addCleanup(self.registry.stop)
        self.info = SimpleNamespace(context=SimpleNamespace(user=AnonymousUser()))
        self.resolver = create_list_resolver(lambda *_: self.Parent.all(), self.Parent)

    def parent(self, name, **values):
        model = self.Parent.Interface._model
        row = model.objects.create(name=name, **values)
        return self.Parent(id=row.pk)

    def child(self, parent, **values):
        return self.Child.Interface._model.objects.create(parent_id=parent.id, **values)

    def query(self, arguments, selection="items { name } pageInfo { totalCount }"):
        return graphene.Schema(query=GraphQL._query_class).execute(
            "{ contributorParentList(" + arguments + ") { " + selection + " } }",
            context_value=self.info.context,
        )

    def test_denied_scalar_matches_are_removed_before_count_and_filled_page(self):
        self.parent("hidden", allow_secret=False)
        self.parent("first")
        self.parent("second")
        result = self.resolver(
            None, self.info, filter={"secret": 1}, page=1, page_size=2
        )
        assert [row.name for row in result["items"]] == ["first", "second"]
        assert result["pageInfo"]["total_count"] == 2

    def test_denied_scalar_is_unavailable_under_exclusion(self):
        self.parent("hidden", secret=0, allow_secret=False)
        self.parent("visible", secret=1)
        result = self.resolver(None, self.info, exclude={"secret": 0})
        assert [row.name for row in result["items"]] == ["visible"]

    def test_denied_python_property_is_not_evaluated(self):
        self.parent("hidden", allow_secret=False)
        self.parent("visible")
        result = self.resolver(None, self.info, filter={"danger": 1})
        assert [row.name for row in result["items"]] == ["visible"]

    def test_boolean_denial_does_not_suppress_callback_error(self):
        self.parent("error")
        failure = RuntimeError("contributor callback failed")

        def fail(*_args):
            raise failure

        with patch.dict(
            permission_functions,
            {
                "contributorFlag": {
                    "permission_method": fail,
                    "permission_filter": lambda *_: None,
                }
            },
        ):
            with self.assertRaises(RuntimeError) as observed:
                self.resolver(None, self.info, filter={"secret": 1})
        assert observed.exception is failure

    def test_any_uses_the_same_authorized_child_witness(self):
        parent = self.parent("split")
        self.child(parent, a=1, b=0)
        self.child(parent, a=0, b=2)
        result = self.query("filter: {contributorChildList: {any: {a: 1, b: 2}}}")
        assert result.errors is None, result.errors
        assert result.data["contributorParentList"]["pageInfo"]["totalCount"] == 0

    def test_none_ignores_hidden_children(self):
        parent = self.parent("visible")
        self.child(parent, a=1, visible=False)
        result = self.query("filter: {contributorChildList: {none: {a: 1}}}")
        assert result.errors is None, result.errors
        assert result.data["contributorParentList"]["items"] == [{"name": "visible"}]

    def test_grouped_none_keeps_django_independent_witness_semantics(self):
        parent = self.parent("split")
        self.child(parent, a=1, b=0, allow_b=False)
        self.child(parent, a=0, b=2, allow_a=False)
        result = self.query("filter: {contributorChildList: {none: {a: 1, b: 2}}}")
        assert result.errors is None, result.errors
        assert result.data["contributorParentList"]["pageInfo"]["totalCount"] == 0

    def test_duplicate_joins_retain_existing_multiplicity_for_visible_witnesses(self):
        parent = self.parent("twice")
        self.child(parent, a=1)
        self.child(parent, a=1)
        self.child(parent, a=1, allow_a=False)
        result = self.query("filter: {contributorChildList: {any: {a: 1}}}")
        assert result.errors is None, result.errors
        assert result.data["contributorParentList"]["pageInfo"]["totalCount"] == 2

    def test_group_metrics_have_independent_contributor_domains(self):
        self.parent("hours", hours=10, cost=100, allow_cost=False)
        self.parent("cost", hours=20, cost=200, allow_hours=False)
        result = graphene.Schema(query=GraphQL._query_class).execute(
            '{ contributorParentGroups(groupBy: ["category"]) { items { hours cost } pageInfo { totalCount } } }',
            context_value=self.info.context,
        )
        assert result.errors is None, result.errors
        assert result.data["contributorParentGroups"]["items"] == [
            {"hours": 10, "cost": 200}
        ]

    def test_ordinary_page_keeps_lazy_sql_and_skips_unused_extra_rights(self):
        self.Parent.Interface._model.objects.bulk_create(
            [self.Parent.Interface._model(name=str(index)) for index in range(1000)]
        )
        with CaptureQueriesContext(connection) as queries:
            result = self.resolver(
                None,
                self.info,
                page=2,
                page_size=100,
                order_by=[{"field": "name", "direction": "ASC"}],
            )
        assert len(result["items"]) == 100
        assert result["pageInfo"]["total_count"] == 1000
        assert len(queries) == 2
        assert self.checks == []

    def test_superuser_field_plan_does_not_evaluate_unknown_field_rules(self):
        from general_manager.permission.manager_based_permission import (
            declared_field_read_plan,
        )

        actor = get_user_model()(is_superuser=True)
        with patch.object(
            self.Parent.Permission, "secret", {"read": ["unknownContributorRule"]}
        ):
            plan = declared_field_read_plan(self.Parent, "secret", actor)
        assert plan.decision == "allow_all"
        assert not plan.requires_instance_check

    def test_protected_order_fills_page_from_visible_domain(self):
        for value in range(1, 5):
            self.parent(str(value), secret=value, allow_secret=value > 2)
        with CaptureQueriesContext(connection) as queries:
            result = self.resolver(
                None,
                self.info,
                order_by=[{"field": "secret", "direction": "ASC"}],
                page=1,
                page_size=2,
            )
        assert [row.secret for row in result["items"]] == [3, 4]
        assert result["pageInfo"]["total_count"] == 2
        assert len(queries) <= 3

    def test_all_granted_child_policy_keeps_native_bag_equivalence(self):
        parent = self.parent("three")
        for _ in range(3):
            self.child(parent, a=1)
        argument = "filter: {contributorChildList: {any: {a: 1}}}"
        protected = self.query(argument)
        with patch.object(self.Child.Permission, "a", {"read": ["public"]}):
            ordinary = self.query(argument)
        assert protected.errors is None and ordinary.errors is None
        assert protected.data == ordinary.data
        assert protected.data["contributorParentList"]["pageInfo"]["totalCount"] == 3

    def test_preexisting_source_bag_is_not_squared_by_protected_filter(self):
        parent = self.parent("twice")
        for _ in range(2):
            self.child(parent, a=1)
        relation = next(
            info.get("filter_lookup", name)
            for name, info in self.Parent.Interface.get_attribute_types().items()
            if info.get("relation_kind") == "collection"
        )
        source = self.Parent.filter(**{relation + "__a": 1})
        resolver = create_list_resolver(lambda *_: source, self.Parent)
        result = resolver(None, self.info, filter={"secret": 1})
        assert [item.name for item in result["items"]] == ["twice", "twice"]
        assert result["pageInfo"]["total_count"] == 2

    def test_generic_none_uses_visible_child_domain(self):
        parent = self.parent("visible")
        self.child(parent, a=1, visible=False)
        relation = next(
            info.get("filter_lookup", name)
            for name, info in self.Parent.Interface.get_attribute_types().items()
            if info.get("relation_kind") == "collection"
        )
        source = MaterializedBucket(self.Parent, [parent])
        result = apply_list_contributors(
            source, self.info, QueryParameterPlan({}, {}, {relation + "__a": 1}), ()
        )
        assert list(result) == [parent]

    def test_group_sort_uses_metric_contributors_and_does_not_change_count(self):
        self.parent("first", category="A", hours=10)
        self.parent("hidden", category="A", hours=1000, allow_hours=False)
        self.parent("second", category="B", hours=20)
        groups = self.Parent.all().group_by("category")
        ordered = apply_grouped_projection_sorting(
            groups, [{"field": "hours", "direction": "ASC"}], info=self.info
        )
        assert [group.keys["category"] for group in ordered] == ["A", "B"]
        assert [group.count for group in ordered] == [2, 1]

    def test_group_projection_is_fresh_between_permission_changes(self):
        self.parent("first", hours=10)
        self.parent("second", hours=20, allow_hours=False)
        group = next(iter(self.Parent.all().group_by("category")))
        first = authorized_group(group, self.info, "hours")
        assert first.combine_value("hours") == 10
        with patch.object(self.Parent.Permission, "hours", {"read": ["public"]}):
            second = authorized_group(group, self.info, "hours")
        assert second.combine_value("hours") == 30

    def test_extra_authenticated_field_plan_retains_lazy_page(self):
        self.parent("one")
        self.parent("two")
        self.info.context.user = get_user_model()(is_staff=False)
        with patch.object(
            self.Parent.Permission, "secret", {"read": ["isAuthenticated"]}
        ):
            with CaptureQueriesContext(connection) as queries:
                result = self.resolver(
                    None, self.info, filter={"secret": 1}, page=1, page_size=1
                )
        assert result["pageInfo"]["total_count"] == 2
        assert len(queries) == 2

    def test_reused_group_rechecks_conditional_root_permissions_for_each_actor(self):
        alice = get_user_model().objects.create_user(username="contributor-alice")
        bob = get_user_model().objects.create_user(username="contributor-bob")
        self.parent("alice", hours=10, creator_id=alice.pk)
        self.parent("bob", hours=20, creator_id=bob.pk)
        group = next(iter(self.Parent.all().group_by("category")))
        with patch.object(self.Parent.Permission, "__read__", ["isSelf"]):
            self.info.context.user = alice
            assert authorized_group(group, self.info, "name").count == 1
            assert (
                authorized_group(group, self.info, "hours").combine_value("hours") == 10
            )
            self.info.context.user = bob
            assert authorized_group(group, self.info, "name").count == 1
            assert (
                authorized_group(group, self.info, "hours").combine_value("hours") == 20
            )

    def test_cached_group_key_needs_an_authorized_contributor_for_current_actor(self):
        alice = get_user_model().objects.create_user(username="contributor-key-alice")
        bob = get_user_model().objects.create_user(username="contributor-key-bob")
        self.parent("alice", creator_id=alice.pk, allow_hours=False)
        group = next(iter(self.Parent.all().group_by("category")))
        resolver = create_normal_resolver("category")
        with patch.object(
            self.Parent.Permission, "category", {"read": ["isSelf"]}, create=True
        ):
            self.info.context.user = alice
            assert resolver(group, self.info) == "A"
            assert authorized_group(group, self.info, "hours").keys["category"] == "A"
            self.info.context.user = bob
            assert resolver(group, self.info) is None

    def test_conditional_root_plan_with_ordinary_field_keeps_lazy_page(self):
        actor = get_user_model().objects.create_user(username="contributor-owner")
        self.parent("mine", creator_id=actor.pk)
        self.parent("other")
        self.info.context.user = actor
        with patch.object(self.Parent.Permission, "__read__", ["isSelf"]):
            with CaptureQueriesContext(connection) as queries:
                result = self.resolver(None, self.info, filter={"name": "mine"}, page=1)
        assert [item.name for item in result["items"]] == ["mine"]
        assert result["pageInfo"]["total_count"] == 1
        assert len(queries) == 2

    def test_replaced_field_authority_is_not_bypassed_by_public_plan(self):
        self.parent("denied")
        with patch(
            "general_manager.api.graphql_resolvers.check_read_permission",
            return_value=False,
        ) as check:
            result = self.resolver(None, self.info, filter={"name": "denied"})
        assert result["items"] == []
        assert check.call_count == 1

    def test_resolver_keeps_stricter_instance_permission_override(self):
        row = self.parent("denied")

        class InstancePolicy(OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]
            name: ClassVar[dict] = {"read": ["matches:name:unavailable"]}

        row.Permission = InstancePolicy
        source = self.Parent.all().with_instances([row])
        resolver = create_list_resolver(lambda *_: source, self.Parent)
        result = resolver(None, self.info, filter={"name": "denied"})
        assert result["items"] == []

    def test_hidden_only_changes_keep_complete_visible_domains_identical(self):
        for policy in ("relation", "leaf", "target-row", "source-row"):
            with self.subTest(policy=policy):
                visible = self.parent("visible")
                hidden = self.parent("visible")
                first = self.child(visible, a=1)
                second = self.child(hidden, a=1, allow_a=False)
                scope = self.Child.filter(id__in=[first.pk, second.pk])
                resolver = create_list_resolver(
                    lambda *_, scope=scope: scope, self.Child
                )
                if policy == "relation":
                    context = patch.object(
                        self.Child.Permission,
                        "parent",
                        {"read": ["contributorFlag:allow_a"]},
                        create=True,
                    )
                elif policy == "leaf":
                    hidden.Interface._model.objects.filter(pk=hidden.id).update(
                        allow_secret=False
                    )
                    context = patch.object(
                        self.Parent.Permission,
                        "name",
                        {"read": ["contributorFlag:allow_secret"]},
                        create=True,
                    )
                elif policy == "target-row":
                    hidden.Interface._model.objects.filter(pk=hidden.id).update(
                        allow_secret=False
                    )
                    context = patch.object(
                        self.Parent.Permission,
                        "__read__",
                        ["contributorFlag:allow_secret"],
                    )
                else:
                    self.Child.Interface._model.objects.filter(pk=second.pk).update(
                        visible=False
                    )
                    context = patch.object(
                        self.Child.Permission, "__read__", ["contributorFlag:visible"]
                    )

                def snapshot(resolver=resolver):
                    arguments = dict(
                        filter={"parent__name__in": ["visible", "mutated"]},
                        order_by=[{"field": "parent__name", "direction": "ASC"}],
                    )
                    rows = resolver(None, self.info, **arguments)
                    groups = resolver(
                        None, self.info, group_by=["b"], filter=arguments["filter"]
                    )
                    return (
                        [item.id for item in rows["items"]],
                        rows["pageInfo"],
                        [
                            (
                                group.keys["b"],
                                authorized_group(group, self.info, "a").combine_value(
                                    "a"
                                ),
                            )
                            for group in groups["items"]
                        ],
                        groups["pageInfo"],
                    )

                with context:
                    before = snapshot()
                    self.Parent.Interface._model.objects.filter(pk=hidden.id).update(
                        name="mutated"
                    )
                    self.Child.Interface._model.objects.filter(pk=second.pk).update(
                        a=999, b=999
                    )
                    after = snapshot()
                assert before == after
                assert before[0] == [first.pk]
                assert before[2] == [(2, 1)]

    def test_hidden_scalar_values_do_not_change_filter_order_or_aggregate(self):
        visible = self.parent("visible", secret=2, hours=10)
        hidden = self.parent(
            "hidden", secret=2, hours=100, allow_secret=False, allow_hours=False
        )

        def snapshot():
            rows = self.resolver(
                None,
                self.info,
                filter={"secret__gte": 0},
                order_by=[{"field": "secret", "direction": "ASC"}],
            )
            group = next(iter(self.Parent.all().group_by("category")))
            return (
                [item.id for item in rows["items"]],
                rows["pageInfo"],
                authorized_group(group, self.info, "hours").combine_value("hours"),
            )

        before = snapshot()
        self.Parent.Interface._model.objects.filter(pk=hidden.id).update(
            secret=999, hours=999
        )
        assert snapshot() == before
        assert before[0] == [visible.id]
        assert before[2] == 10

    def test_denied_annotation_provider_is_not_called_and_allowed_errors_propagate(
        self,
    ):
        self.parent("denied", allow_secret=False)
        failure = RuntimeError("annotation provider failed")
        prop = self.Parent.Interface.get_graph_ql_properties()["annotated"]
        with patch.object(prop, "query_annotation", side_effect=failure) as provider:
            result = self.resolver(None, self.info, filter={"annotated": 1})
            assert result["items"] == []
            provider.assert_not_called()
            self.parent("allowed")
            with self.assertRaises(RuntimeError) as observed:
                self.resolver(None, self.info, filter={"annotated": 1})
            assert observed.exception is failure

    def test_python_property_exclude_authorizes_before_getter(self):
        self.parent("denied", allow_secret=False)
        self.parent("visible", secret=2)
        result = self.resolver(None, self.info, exclude={"danger": 1})
        assert [row.name for row in result["items"]] == ["visible"]

    def test_unknown_operand_is_error_even_when_source_is_empty(self):
        with self.assertRaises(Exception) as error:
            self.resolver(None, self.info, filter={"does_not_exist": 1})
        assert "does_not_exist" in str(error.exception)

    def test_custom_named_text_primary_keys_work_for_scalar_and_plural_fields(self):
        self.KeyParent.Interface._model.objects.create(code="root", value=1)
        for code, visible in (
            ("allowed-1", True),
            ("allowed-2", True),
            ("hidden", False),
        ):
            self.KeyChild.Interface._model.objects.create(
                code=code, parent_id="root", visible=visible
            )
        info = self.info
        resolver = create_list_resolver(lambda *_: self.KeyParent.all(), self.KeyParent)
        scalar = resolver(None, info, filter={"value": 1})
        assert [item.code for item in scalar["items"]] == ["root"]
        relation = next(
            metadata.get("filter_lookup", name)
            for name, metadata in self.KeyParent.Interface.get_attribute_types().items()
            if metadata.get("relation_kind") == "collection"
        )
        plural = resolver(None, info, filter={relation + "__value": 1})
        assert [item.code for item in plural["items"]] == ["root", "root"]
        assert plural["pageInfo"]["total_count"] == 2

    def test_native_fk_conditional_target_rights_keep_sql_count_and_page(self):
        actor = get_user_model().objects.create_user(username="contributor-fk-owner")
        visible = self.parent("visible", creator_id=actor.pk)
        hidden = self.parent("hidden")
        self.child(visible)
        self.child(hidden)
        resolver = create_list_resolver(lambda *_: self.Child.all(), self.Child)
        self.info.context.user = actor
        with (
            patch.object(self.Parent.Permission, "__read__", ["isSelf"]),
            patch.object(self.Child.Permission, "__read__", ["public"]),
            CaptureQueriesContext(connection) as queries,
        ):
            result = resolver(
                None,
                self.info,
                filter={"parent__name__in": ["visible", "hidden"]},
                order_by=[{"field": "parent__name", "direction": "ASC"}],
                page=1,
                page_size=1,
            )
        assert [item.parent.name for item in result["items"]] == ["visible"]
        assert result["pageInfo"]["total_count"] == 1
        assert len(queries) == 2

    def test_native_permission_subsets_do_not_reuse_unrestricted_run_snapshot(self):
        alice = get_user_model().objects.create_user(username="contributor-run-alice")
        bob = get_user_model().objects.create_user(username="contributor-run-bob")
        first = self.parent("alice", creator_id=alice.pk)
        second = self.parent("bob", creator_id=bob.pk)
        self.child(first)
        self.child(second)
        resolver = create_list_resolver(lambda *_: self.Child.all(), self.Child)
        with (
            patch.object(self.Parent.Permission, "__read__", ["isSelf"]),
            CalculationRunContext(),
        ):
            assert len(list(self.Child.all())) == 2
            for actor, expected in ((alice, "alice"), (bob, "bob")):
                self.info.context.user = actor
                result = resolver(
                    None, self.info, filter={"parent__name__in": ["alice", "bob"]}
                )
                assert [row.parent.name for row in result["items"]] == [expected]

    def test_native_fk_alias_observes_target_row_and_identity_permissions(self):
        parent = self.parent("denied", allow_secret=False)
        self.child(parent)
        resolver = create_list_resolver(lambda *_: self.Child.all(), self.Child)
        policies = (
            patch.object(
                self.Parent.Permission,
                "id",
                {"read": ["contributorFlag:allow_secret"]},
                create=True,
            ),
            patch.object(
                self.Parent.Permission, "__read__", ["contributorFlag:allow_secret"]
            ),
        )
        for policy in policies:
            with policy, patch.object(self.Child.Permission, "__read__", ["public"]):
                for arguments in (
                    {"filter": {"parent_id": parent.id}},
                    {"exclude": {"parent_id": -1}},
                    {"order_by": [{"field": "parent_id", "direction": "ASC"}]},
                    {"group_by": ["parent_id"]},
                ):
                    with self.subTest(arguments=arguments):
                        result = resolver(None, self.info, **arguments)
                        assert list(result["items"]) == []
                        assert result["pageInfo"]["total_count"] == 0
                generated = graphene.Schema(query=GraphQL._query_class).execute(
                    '{ contributorChildGroups(groupBy: ["parentId"]) { items { parentId } pageInfo { totalCount } } }',
                    context_value=self.info.context,
                )
                assert generated.errors is None, generated.errors
                assert generated.data["contributorChildGroups"] == {
                    "items": [],
                    "pageInfo": {"totalCount": 0},
                }

    def test_fk_to_private_non_primary_field_protects_both_relation_spellings(self):
        target = self.CodeTarget.Interface._model.objects.create(
            code="hidden-code", visible=False
        )
        self.CodeSource.Interface._model.objects.create(parent_id=target.code)
        resolver = create_list_resolver(
            lambda *_: self.CodeSource.all(), self.CodeSource
        )
        for field in ("parent", "parent_id"):
            for arguments in (
                {"filter": {field: target.code}},
                {"exclude": {field: "absent"}},
                {
                    "order_by": [
                        {
                            "field": field + "_id" if field == "parent" else field,
                            "direction": "ASC",
                        }
                    ]
                },
                {"group_by": [field + "_id" if field == "parent" else field]},
            ):
                result = resolver(None, self.info, **arguments)
                assert list(result["items"]) == []
                assert result["pageInfo"]["total_count"] == 0
        assert (
            len(resolver(None, self.info, filter={"parent__id": target.pk})["items"])
            == 1
        )
        with patch.object(self.CodeTarget.Permission, "code", {"read": ["public"]}):
            assert (
                len(resolver(None, self.info, filter={"parent": target.code})["items"])
                == 1
            )
            assert (
                len(
                    resolver(None, self.info, filter={"parent_id": target.code})[
                        "items"
                    ]
                )
                == 1
            )
            actor = get_user_model().objects.create_user(username="code-target-owner")
            self.CodeTarget.Interface._model.objects.filter(pk=target.pk).update(
                creator_id=actor.pk
            )
            self.info.context.user = actor
            with patch.object(self.CodeTarget.Permission, "__read__", ["isSelf"]):
                for field in ("parent", "parent_id"):
                    with CaptureQueriesContext(connection) as queries:
                        result = resolver(
                            None,
                            self.info,
                            filter={field: target.code},
                            page=1,
                            page_size=1,
                        )
                    assert [item.parent_id for item in result["items"]] == [target.code]
                    assert result["pageInfo"]["total_count"] == 1
                    assert len(queries) == 2

    def test_native_plural_conditional_target_rights_keep_visible_bag_and_sql_page(
        self,
    ):
        actor = get_user_model().objects.create_user(username="contributor-child-owner")
        parent = self.parent("mixed")
        hidden_only = self.parent("hidden-only")
        self.child(parent, a=1, creator_id=actor.pk)
        self.child(parent, a=1, creator_id=actor.pk)
        self.child(parent, a=1)
        self.child(hidden_only, a=1)
        self.info.context.user = actor
        relation = next(
            metadata.get("filter_lookup", name)
            for name, metadata in self.Parent.Interface.get_attribute_types().items()
            if metadata.get("relation_kind") == "collection"
        )
        key = relation + "__a"
        with (
            patch.object(self.Child.Permission, "__read__", ["isSelf"]),
            patch.object(self.Child.Permission, "a", {"read": ["public"]}),
        ):
            with CaptureQueriesContext(connection) as queries:
                positive = self.resolver(
                    None, self.info, filter={key: 1}, page=1, page_size=2
                )
            assert [item.name for item in positive["items"]] == ["mixed", "mixed"]
            assert positive["pageInfo"]["total_count"] == 2
            assert len(queries) == 2
            with CaptureQueriesContext(connection) as queries:
                negative = apply_list_contributors(
                    self.Parent.all(),
                    self.info,
                    QueryParameterPlan({}, {}, {key: 1}),
                    (),
                )
                names = [item.name for item in negative]
            assert names == ["hidden-only"]
            assert len(queries) == 1

    def test_matches_string_rule_is_not_certified_as_typed_sql_equality(self):
        self.parent("one", secret=1)
        with patch.object(
            self.Parent.Permission, "secret", {"read": ["matches:secret:01"]}
        ):
            result = self.resolver(None, self.info, filter={"secret": 1})
        assert result["items"] == []

    def test_matches_target_row_uses_runtime_string_and_nullable_semantics(self):
        parent = self.parent("one", secret=1)
        self.child(parent)
        resolver = create_list_resolver(lambda *_: self.Child.all(), self.Child)
        with patch.object(self.Parent.Permission, "__read__", ["matches:secret:01"]):
            denied = resolver(None, self.info, filter={"parent__name": "one"})
        assert denied["items"] == []
        with patch.object(self.Parent.Permission, "__read__", ["matches:maybe:None"]):
            allowed = resolver(None, self.info, filter={"parent__name": "one"})
        assert len(allowed["items"]) == 1

    def test_root_matches_count_only_retains_string_and_nullable_semantics(self):
        self.parent("one", secret=1)
        with patch.object(self.Parent.Permission, "__read__", ["matches:secret:01"]):
            denied = self.resolver(None, self.info)
        assert denied["pageInfo"]["total_count"] == 0
        with patch.object(self.Parent.Permission, "__read__", ["matches:maybe:None"]):
            allowed = self.resolver(None, self.info)
        assert allowed["pageInfo"]["total_count"] == 1

    def test_calculation_nested_plural_predicates_ignore_hidden_children(self):
        parent = self.parent("one")
        self.child(parent, a=1, visible=False)
        parent_type = self.Parent
        parent_values = [parent]

        class ContributorCalculation(GeneralManager):
            class Interface(CalculationInterface):
                parent = Input(parent_type, possible_values=parent_values)

        resolver = create_list_resolver(
            lambda *_: ContributorCalculation.all(), ContributorCalculation
        )
        relation = next(
            metadata.get("filter_lookup", name)
            for name, metadata in self.Parent.Interface.get_attribute_types().items()
            if metadata.get("relation_kind") == "collection"
        )
        key = "parent__" + relation + "__a"
        result = resolver(None, self.info, filter={key: 1})
        assert result["items"] == []
        none = resolver(None, self.info, exclude={key: 1})
        assert len(none["items"]) == 1

    def test_calculation_collection_selection_keeps_whole_selection_with_plural_filter(
        self,
    ):
        parent = self.parent("one")
        child = self.child(parent)
        parent_type, child_type = self.Parent, self.Child
        parent_values = [parent]
        selections = [[child_type(id=child.pk)]]

        class SelectionCalculation(GeneralManager):
            class Interface(CalculationInterface):
                parent = Input(parent_type, possible_values=parent_values)
                selected = Input(list[child_type], possible_values=selections)

        resolver = create_list_resolver(
            lambda *_: SelectionCalculation.all(), SelectionCalculation
        )
        relation = next(
            metadata.get("filter_lookup", name)
            for name, metadata in self.Parent.Interface.get_attribute_types().items()
            if metadata.get("relation_kind") == "collection"
        )
        selection_only = resolver(None, self.info, filter={"selected": selections[0]})
        combined = resolver(
            None,
            self.info,
            filter={"selected": selections[0], "parent__" + relation + "__a": 1},
        )
        assert len(selection_only["items"]) == len(combined["items"]) == 1
        assert (
            selection_only["items"][0].identification
            == combined["items"][0].identification
        )

    def test_inherited_builtin_permission_replacement_keeps_runtime_authority(self):
        self.parent("one")
        with patch.object(
            OverrideManagerPermission, "check_permission", return_value=False
        ) as denied:
            result = self.resolver(None, self.info, filter={"name": "one"})
        assert result["items"] == []
        assert denied.call_count == 1

    def test_replaced_builtin_registry_method_cannot_inherit_old_filter_grant(self):
        self.parent("one")
        public = permission_functions["public"]
        with patch.dict(
            permission_functions,
            {"public": {**public, "permission_method": lambda *_: False}},
        ):
            assert self.resolver(None, self.info)["items"] == []
            assert self.resolver(None, self.info, filter={"name": "one"})["items"] == []

    def test_composite_manager_collection_keeps_native_selection_identity(self):
        class CompositeSelection(GeneralManager):
            class Interface(CalculationInterface):
                code = Input(str)
                revision = Input(int)

        selection = CompositeSelection(code="A", revision=1)

        class CompositeSelectionOwner(GeneralManager):
            class Interface(CalculationInterface):
                selected = Input(
                    list[CompositeSelection], possible_values=[[selection]]
                )

        resolver = create_list_resolver(
            lambda *_: CompositeSelectionOwner.all(), CompositeSelectionOwner
        )
        result = resolver(None, self.info, filter={"selected": [selection]})
        assert len(result["items"]) == 1
        assert result["items"][0].selected[0].identification == selection.identification

    def test_callback_without_filter_provider_remains_runtime(self):
        self.parent("one")
        callback = permission_functions["contributorFlag"]["permission_method"]
        with patch.dict(
            permission_functions, {"contributorFlag": {"permission_method": callback}}
        ):
            result = self.resolver(None, self.info, filter={"secret": 1})
        assert len(result["items"]) == len(self.checks) == 1

    def test_calculation_relation_property_uses_its_hint_and_current_permissions(self):
        parent = self.parent("one")
        parent_type = self.Parent

        class PropertyCalculation(GeneralManager):
            class Interface(CalculationInterface):
                axis = Input(int, possible_values=[1])

            @graph_ql_property(filterable=True)
            def related(self) -> parent_type:
                return parent_type(id=parent.id)

        resolver = create_list_resolver(
            lambda *_: PropertyCalculation.all(), PropertyCalculation
        )
        result = resolver(None, self.info, filter={"related__name": "one"})
        assert len(result["items"]) == 1
        with patch.object(
            self.Parent.Permission,
            "name",
            {"read": ["contributorFlag:allow_secret"]},
            create=True,
        ):
            self.Parent.Interface._model.objects.filter(pk=parent.id).update(
                allow_secret=False
            )
            assert (
                resolver(None, self.info, filter={"related__name": "one"})["items"]
                == []
            )

    def test_lookup_named_target_fields_keep_permissions_in_native_and_generated_lists(
        self,
    ):
        parent = self.parent("visible", allow_secret=False)
        self.child(parent)
        resolver = create_list_resolver(lambda *_: self.Child.all(), self.Child)
        with patch.object(self.Child.Permission, "__read__", ["public"]):
            for field, expected, literal in (
                ("exact", 7, "7"),
                ("contains", "needle", '"needle"'),
            ):
                with self.subTest(field=field):
                    result = resolver(
                        None, self.info, filter={"parent__" + field: expected}
                    )
                    assert result["items"] == []
                    generated = graphene.Schema(query=GraphQL._query_class).execute(
                        "{ contributorChildList(filter: {parent: {"
                        + field
                        + ": "
                        + literal
                        + "}}) { items { id } pageInfo { totalCount } } }",
                        context_value=self.info.context,
                    )
                    assert generated.errors is None, generated.errors
                    assert generated.data["contributorChildList"] == {
                        "items": [],
                        "pageInfo": {"totalCount": 0},
                    }
            control = resolver(
                None, self.info, filter={"parent__name__exact": "visible"}
            )
            assert len(control["items"]) == 1
            self.Parent.Interface._model.objects.filter(pk=parent.id).update(
                allow_secret=True
            )
            assert (
                len(resolver(None, self.info, filter={"parent__exact": 7})["items"])
                == 1
            )

    def test_optional_collection_keeps_none_empty_and_in_semantics_with_plural_operand(
        self,
    ):
        parent = self.parent("one")
        self.child(parent)
        parent_type = self.Parent
        parent_values = [parent]

        class OptionalSelection(GeneralManager):
            class Interface(CalculationInterface):
                parent = Input(parent_type, possible_values=parent_values)
                selected = Input(list[int], required=False, possible_values=[None, []])

        resolver = create_list_resolver(
            lambda *_: OptionalSelection.all(), OptionalSelection
        )
        relation = next(
            metadata.get("filter_lookup", name)
            for name, metadata in self.Parent.Interface.get_attribute_types().items()
            if metadata.get("relation_kind") == "collection"
        )
        plural = {"parent__" + relation + "__a": 1}
        for filter_key, expected, selections in (
            ("selected", None, [None]),
            ("selected", [], [[]]),
            ("selected__in", [None, []], [None, []]),
        ):
            with self.subTest(filter_key=filter_key, expected=expected):
                result = resolver(
                    None, self.info, filter={filter_key: expected, **plural}
                )
                assert [item.selected for item in result["items"]] == selections
        for excluded, selections in ((None, [[]]), ([], [None])):
            with self.subTest(excluded=excluded):
                result = resolver(
                    None, self.info, exclude={"selected": excluded, **plural}
                )
                assert [item.selected for item in result["items"]] == selections

    def test_nested_calculation_property_uses_the_owner_getter_once(self):
        parent = self.parent("one")
        child = self.child(parent)
        parent_type = self.Parent
        parent_values = [parent]

        class GetterCalculation(GeneralManager):
            class Interface(CalculationInterface):
                parent = Input(parent_type, possible_values=parent_values)

        resolver = create_list_resolver(
            lambda *_: GetterCalculation.all(), GetterCalculation
        )
        relation = next(
            metadata.get("filter_lookup", name)
            for name, metadata in self.Parent.Interface.get_attribute_types().items()
            if metadata.get("relation_kind") == "collection"
        )
        path = "parent__" + relation + "__once"
        result = resolver(None, self.info, filter={path: 1})
        assert len(result["items"]) == 1
        assert self.Child.property_reads == [child.pk]
        self.Child.property_reads.clear()
        self.Child.Interface._model.objects.filter(pk=child.pk).update(allow_a=False)
        with patch.object(
            self.Child.Permission,
            "once",
            {"read": ["contributorFlag:allow_a"]},
            create=True,
        ):
            assert resolver(None, self.info, filter={path: 1})["items"] == []
        assert self.Child.property_reads == []
        failure = RuntimeError("matching property permission failed")

        def fail(*_args):
            raise failure

        with (
            patch.object(
                self.Child.Permission,
                "once",
                {"read": ["contributorFlag:allow_a"]},
                create=True,
            ),
            patch.dict(
                permission_functions,
                {
                    "contributorFlag": {
                        "permission_method": fail,
                        "permission_filter": lambda *_: None,
                    }
                },
            ),
            self.assertRaises(RuntimeError) as observed,
        ):
            resolver(None, self.info, filter={path: 1})
        assert observed.exception is failure
        assert self.Child.property_reads == []
