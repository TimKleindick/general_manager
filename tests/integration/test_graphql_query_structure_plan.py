"""A native operand structure plan reuses metadata, never permission decisions."""

import sys
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.db import models
from graphql import GraphQLError

from general_manager.api import graphql_resolvers
from general_manager.api.graphql_query_permissions import authorize_query_fields
from general_manager.api.property import graph_ql_property
from general_manager.interface import DatabaseInterface
from general_manager.interface.capabilities.orm.support import (
    OrmPersistenceSupportCapability,
    OrmReadCapability,
)
from general_manager.manager import GeneralManager
from general_manager.permission import OverrideManagerPermission
from general_manager.permission.manager_based_permission import (
    additional_local_read_permission,
)
from general_manager.permission.permission_checks import permission_functions
from general_manager.utils.testing import GeneralManagerTransactionTestCase


@contextmanager
def metadata_calls():
    """Count actual code entries without replacing canonical methods."""
    codes = {
        OrmReadCapability.get_attribute_types.__code__: "metadata",
        OrmReadCapability.get_attributes.__code__: "accessors",
        additional_local_read_permission.__code__: "additional_permissions",
    }
    counts = Counter()
    previous = sys.getprofile()

    def observe(frame, event, arg):
        if event == "call" and frame.f_code in codes:
            counts[codes[frame.f_code]] += 1
        if previous is not None:
            previous(frame, event, arg)

    sys.setprofile(observe)
    try:
        yield counts
    finally:
        sys.setprofile(previous)


class TestGraphQLQueryStructurePlan(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class StructurePlanTarget(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=30)
                contains = models.CharField(max_length=30, default="contained")

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        class StructurePlanRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=30)
                rank = models.IntegerField()
                target = models.ForeignKey(
                    "general_manager.StructurePlanTarget", on_delete=models.CASCADE
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        cls.Record = StructurePlanRecord
        cls.Target = StructurePlanTarget
        cls.general_manager_classes = [StructurePlanTarget, StructurePlanRecord]
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        self.info = SimpleNamespace(context=SimpleNamespace(user=AnonymousUser()))
        self.target = self.Target.Factory.create(name="target")
        self.record = self.Record.Factory.create(
            name="match", rank=1, target=self.target
        )

    def authorize(self, candidates, paths, *, prepared=True, **kwargs):
        authorize_query_fields(
            candidates,
            self.info,
            paths,
            budget_additional_permissions_only=True,
            prepare_native_structure=prepared,
            **kwargs,
        )

    def test_standard_structure_work_does_not_grow_with_candidate_visits(self):
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Record.all(), self.Record
        )
        arguments = {
            "filter": {
                "name": "match",
                "rank__gte": 0,
                "rank__lte": 30,
                "target_id": self.target.id,
            },
            "order_by": [{"field": "rank", "direction": "ASC"}],
            "page": 1,
            "page_size": 1,
        }
        # Setup/lazy initialization is outside the measured calls.
        self.Record.Interface.get_attribute_types()
        self.Record.Interface.get_attributes()
        with metadata_calls() as single:
            first = resolver(None, self.info, **arguments)
        model = self.Record.Interface._model
        model.objects.bulk_create(
            [
                model(name="match", rank=rank, target_id=self.target.id)
                for rank in range(2, 21)
            ]
        )
        with metadata_calls() as repeated:
            second = resolver(None, self.info, **arguments)

        assert first["pageInfo"]["total_count"] == 1
        assert second["pageInfo"]["total_count"] == 20
        assert [item.rank for item in first["items"]] == [1]
        assert [item.rank for item in second["items"]] == [1]
        for kind in ("metadata", "accessors", "additional_permissions"):
            assert repeated[kind] <= single[kind], (kind, single, repeated)

    def test_runtime_field_order_matches_unprepared_execution(self):
        paths = ["rank__lte", "target_id", "rank", "name", "rank__gte"]
        runs = []
        original = graphql_resolvers.check_read_permission
        for prepared in (False, True):
            with patch.object(
                graphql_resolvers, "check_read_permission", wraps=original
            ) as check:
                self.authorize([self.record, self.record], paths, prepared=prepared)
            runs.append([(call.args[0], call.args[2]) for call in check.call_args_list])
        assert runs[0] == runs[1]
        assert len(runs[1]) == 12

    def test_repeated_declarative_callback_denial_and_error_remain_live(self):
        for error in (None, RuntimeError("second callback failed")):
            with self.subTest(error=error):
                calls = []

                def callback(instance, _user, _config, *, error=error, calls=calls):
                    calls.append(instance)
                    if len(calls) == 2 and error is not None:
                        raise error
                    return len(calls) == 1

                with (
                    patch.dict(
                        permission_functions,
                        {
                            "structureCallback": {
                                "permission_method": callback,
                                "permission_filter": lambda *_: None,
                            }
                        },
                    ),
                    patch.object(
                        self.Record.Permission, "__read__", ["structureCallback"]
                    ),
                    self.assertRaisesMessage(
                        RuntimeError if error else GraphQLError,
                        "second callback failed" if error else "query field 'rank'",
                    ),
                ):
                    self.authorize([self.record], ["rank", "rank__gte"])
                assert calls == [self.record, self.record]

    def test_repeated_candidate_denial_is_not_cached(self):
        with (
            patch.object(
                graphql_resolvers, "check_read_permission", side_effect=[True, False]
            ) as check,
            self.assertRaisesMessage(GraphQLError, "query field 'name'"),
        ):
            self.authorize([self.record, self.record], ["name"])
        assert check.call_count == 2

    def test_resolver_checks_denied_nonmatching_and_offpage_candidates(self):
        self.Record.Factory.create(name="match", rank=2, target=self.target)
        self.Record.Factory.create(name="outside", rank=3, target=self.target)
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Record.all(), self.Record
        )
        for denied_rank in (2, 3):
            with self.subTest(denied_rank=denied_rank):
                seen = []

                def check(
                    instance, _info, field, *, denied_rank=denied_rank, seen=seen
                ):
                    seen.append((instance.rank, field))
                    return instance.rank != denied_rank

                with (
                    patch.object(
                        graphql_resolvers, "check_read_permission", side_effect=check
                    ),
                    self.assertRaisesMessage(GraphQLError, "query field 'name'"),
                ):
                    resolver(
                        None,
                        self.info,
                        filter={"name": "match"},
                        order_by=[{"field": "rank", "direction": "ASC"}],
                        page=1,
                        page_size=1,
                    )
                assert (denied_rank, "name") in seen

    def test_budget_failure_precedes_runtime_denial(self):
        with (
            patch.object(
                self.Record.Permission, "name", {"read": ["isAdmin"]}, create=True
            ),
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                0,
            ),
            patch.object(
                graphql_resolvers, "check_read_permission", return_value=False
            ) as check,
            self.assertRaisesMessage(GraphQLError, "candidate limit"),
        ):
            self.authorize([self.record], ["name"])
        check.assert_not_called()

    def test_later_unknown_operand_does_not_preempt_earlier_denial(self):
        with (
            patch.object(
                graphql_resolvers, "check_read_permission", return_value=False
            ) as check,
            self.assertRaisesMessage(GraphQLError, "query field 'name'"),
        ):
            self.authorize([self.record], ["zz_missing", "name"])
        assert [call.args[2] for call in check.call_args_list] == ["name"]

    def test_empty_candidates_do_not_read_metadata(self):
        with metadata_calls() as counts:
            self.authorize(iter(()), ["name", "zz_missing"])
        assert not counts

    def test_rule_changes_between_calls_are_read_again(self):
        self.authorize([self.record], ["name"])
        with (
            patch.object(
                self.Record.Permission, "name", {"read": ["isAdmin"]}, create=True
            ),
            self.assertRaisesMessage(GraphQLError, "query field 'name'"),
        ):
            self.authorize([self.record], ["name"])
        self.authorize([self.record], ["name"])

    def test_instance_permission_override_uses_runtime_policy_and_budget(self):
        class RuntimePermission(OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]

            def check_permission(self, action, attribute):
                return super().check_permission(action, attribute)

        overridden = self.Record(**self.record.identification)
        overridden.Permission = RuntimePermission
        with (
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                1,
            ),
            self.assertRaisesMessage(GraphQLError, "candidate limit"),
        ):
            self.authorize([self.record, overridden, overridden], ["name"])

    def test_actual_subtype_permission_is_not_replaced_by_base_plan(self):
        class RuntimeSubtype(self.Record):
            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

                def check_permission(self, action, attribute):
                    return False

        subtype = RuntimeSubtype(**self.record.identification)
        with self.assertRaisesMessage(GraphQLError, "query field 'name'"):
            self.authorize([self.record, subtype], ["name"])

    def test_delegation_still_uses_strict_additional_budget(self):
        class DelegatedPermission(OverrideManagerPermission):
            __based_on__: ClassVar[str] = "target"

        self.record.Permission = DelegatedPermission
        with (
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                1,
            ),
            self.assertRaisesMessage(GraphQLError, "candidate limit"),
        ):
            self.authorize([self.record, self.record], ["name"])

    def test_property_operand_keeps_runtime_checks(self):
        @graph_ql_property
        def computed_name(instance) -> str:
            return instance.name

        with (
            patch.object(self.Record, "computed_name", computed_name, create=True),
            patch.object(
                graphql_resolvers, "check_read_permission", side_effect=[True, False]
            ) as check,
            self.assertRaisesMessage(GraphQLError, "query field 'computed_name'"),
        ):
            self.authorize([self.record, self.record], ["computed_name"])
        assert check.call_count == 2

    def test_metadata_changes_between_calls_are_read_again(self):
        descriptors = self.Record.Interface.require_capability(
            "orm_support"
        ).get_field_descriptors(self.Record.Interface)
        with patch.object(
            graphql_resolvers, "check_read_permission", return_value=True
        ) as check:
            with patch.dict(descriptors["name"].metadata, {"filter_lookup": "operand"}):
                self.authorize([self.record], ["operand"])
            with patch.dict(descriptors["rank"].metadata, {"filter_lookup": "operand"}):
                self.authorize([self.record], ["operand"])
        assert [call.args[2] for call in check.call_args_list] == ["name", "rank"]

    def test_instance_interface_override_keeps_dynamic_alias_metadata_live(self):
        original = self.Record.Interface.get_attribute_types
        visits = 0

        def metadata():
            nonlocal visits
            values = original()
            name = "name" if visits == 0 else "rank"
            visits += 1
            values[name]["filter_lookup"] = "operand"
            return values

        self.record.Interface = SimpleNamespace(
            get_attribute_types=metadata,
            get_attributes=self.Record.Interface.get_attributes,
            get_graph_ql_properties=self.Record.Interface.get_graph_ql_properties,
            input_fields=self.Record.Interface.input_fields,
        )
        with patch.object(
            graphql_resolvers, "check_read_permission", return_value=True
        ) as check:
            self.authorize([self.record, self.record], ["operand"])
        assert [call.args[2] for call in check.call_args_list] == ["name", "rank"]

    def test_custom_read_capability_keeps_dynamic_alias_metadata_live(self):
        class DynamicRead(OrmReadCapability):
            visits = 0

            def get_attribute_types(self, interface_cls):
                values = super().get_attribute_types(interface_cls)
                name = "name" if self.visits == 0 else "rank"
                self.visits += 1
                values[name]["filter_lookup"] = "operand"
                return values

        with (
            patch.dict(
                self.Record.Interface._capability_handlers, {"read": DynamicRead()}
            ),
            patch.object(
                graphql_resolvers, "check_read_permission", return_value=True
            ) as check,
        ):
            self.authorize([self.record, self.record], ["operand"])
        assert [call.args[2] for call in check.call_args_list] == ["name", "rank"]

    def test_custom_support_capability_keeps_dynamic_alias_metadata_live(self):
        class DynamicSupport(OrmPersistenceSupportCapability):
            selected = "name"

            def get_field_descriptors(self, interface_cls):
                values = dict(super().get_field_descriptors(interface_cls))
                descriptor = values[self.selected]
                values[self.selected] = replace(
                    descriptor,
                    metadata={**descriptor.metadata, "filter_lookup": "operand"},
                )
                return values

        support = DynamicSupport()
        seen = []

        def check(_instance, _info, field):
            seen.append(field)
            support.selected = "rank"
            return True

        with (
            patch.dict(
                self.Record.Interface._capability_handlers, {"orm_support": support}
            ),
            patch.object(graphql_resolvers, "check_read_permission", side_effect=check),
        ):
            self.authorize([self.record, self.record], ["operand"])
        assert seen == ["name", "rank"]

    def test_related_row_denial_remains_live_for_nested_paths(self):
        with (
            patch.object(
                self.Target.Permission, "can_read_instance", return_value=False
            ),
            self.assertRaisesMessage(GraphQLError, "traverse query relation"),
        ):
            self.authorize([self.record], ["target__name"])

    def test_annotation_manager_wrapper_keeps_lookup_named_relation_gate(self):
        """A model-style annotation remains a relation without relation_kind."""

        class TargetAnnotation:
            _general_manager_class = self.Target

        descriptors = self.Record.Interface.require_capability(
            "orm_support"
        ).get_field_descriptors(self.Record.Interface)
        metadata = {
            **descriptors["target"].metadata,
            "type": TargetAnnotation,
        }
        metadata.pop("relation_kind", None)
        for prepared in (False, True):
            with self.subTest(prepared=prepared):
                with (
                    patch.dict(descriptors["target"].metadata, metadata, clear=True),
                    patch.object(
                        self.Target.Permission, "can_read_instance", return_value=False
                    ) as row_check,
                    self.assertRaisesMessage(GraphQLError, "traverse query relation"),
                ):
                    self.authorize(
                        [self.record], ["target__contains"], prepared=prepared
                    )
                row_check.assert_called_once()

    def test_descriptor_backed_metadata_remains_live_with_standard_capabilities(self):
        """Canonical capability methods can still read executable metadata."""
        original = self.Record.Interface.require_capability(
            "orm_support"
        ).get_field_descriptors(self.Record.Interface)

        class DynamicDescriptors:
            visits = 0

            def __get__(self, _instance, _owner):
                name = "name" if self.visits == 0 else "rank"
                self.visits += 1
                descriptors = dict(original)
                descriptor = descriptors[name]
                descriptors[name] = replace(
                    descriptor,
                    metadata={**descriptor.metadata, "filter_lookup": "operand"},
                )
                return descriptors

        for prepared in (False, True):
            with (
                self.subTest(prepared=prepared),
                patch.object(
                    self.Record.Interface, "_field_descriptors", DynamicDescriptors()
                ),
                patch.object(
                    graphql_resolvers, "check_read_permission", return_value=True
                ) as check,
            ):
                self.authorize(
                    [self.record, self.record], ["operand"], prepared=prepared
                )
                assert [call.args[2] for call in check.call_args_list] == [
                    "name",
                    "rank",
                ]

    def test_executable_identification_preserves_second_access_error(self):
        """A stable property definition does not imply a stable runtime value."""
        identification = self.record.identification
        for prepared in (False, True):
            accesses = []

            def identify(instance, *, accesses=accesses):
                accesses.append(instance)
                if len(accesses) == 2:
                    message = "second identification access"
                    raise RuntimeError(message)
                return identification

            with self.subTest(prepared=prepared):
                with (
                    patch.object(self.Record, "identification", property(identify)),
                    patch.object(
                        graphql_resolvers, "check_read_permission", return_value=True
                    ) as check,
                    self.assertRaisesMessage(
                        RuntimeError, "second identification access"
                    ),
                ):
                    self.authorize(
                        [self.record, self.record], ["name"], prepared=prepared
                    )
                assert len(accesses) == 2
                assert check.call_count == 1

    def test_permission_descriptor_is_not_evaluated_by_structure_probe(self):
        """A fallback probe must not consume the policy used by runtime checks."""

        class AllowPermission(OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]

        class DenyPermission(OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["isAuthenticated"]

        class AlternatingPermission:
            def __init__(self):
                self.returned = []

            def __get__(self, _instance, _owner):
                permission = (
                    AllowPermission if len(self.returned) % 2 == 0 else DenyPermission
                )
                self.returned.append(permission)
                return permission

        for prepared in (False, True):
            descriptor = AlternatingPermission()
            with self.subTest(prepared=prepared):
                with (
                    patch.object(self.Record, "Permission", descriptor),
                    self.assertRaisesMessage(GraphQLError, "query field 'name'"),
                ):
                    self.authorize([self.record], ["name"], prepared=prepared)
                assert descriptor.returned == [AllowPermission, DenyPermission]

    def test_executable_rule_container_keeps_structure_and_runtime_access_order(self):
        """A dict subclass can execute code even when its definition stays fixed."""

        class AlternatingRules(dict):
            def __init__(self):
                super().__init__({"read": ["isAuthenticated"]})
                self.read_results = []

            def __contains__(self, key):
                if key != "read":
                    return super().__contains__(key)
                present = len(self.read_results) % 2 == 0
                self.read_results.append(present)
                return present

        original = graphql_resolvers.check_read_permission
        for prepared in (False, True):
            rules = AlternatingRules()
            with (
                self.subTest(prepared=prepared),
                patch.object(self.Record.Permission, "name", rules, create=True),
                patch.object(
                    graphql_resolvers, "check_read_permission", wraps=original
                ) as check,
            ):
                self.authorize(
                    [self.record], ["name", "name__exact"], prepared=prepared
                )
                assert rules.read_results == [True, False, True, False]
                assert [call.args[2] for call in check.call_args_list] == [
                    "name",
                    "name",
                ]

    def test_executable_rule_list_keeps_structure_and_runtime_iteration_order(self):
        """A list subclass must retain the general path's executable accesses."""

        class AlternatingList(list):
            def __init__(self):
                super().__init__(["public"])
                self.iterations = 0

            def __iter__(self):
                self.iterations += 1
                if self.iterations == 4:
                    return iter(["isAuthenticated"])
                return iter(["public"])

        original = graphql_resolvers.check_read_permission
        for prepared in (False, True):
            rules = AlternatingList()
            with (
                self.subTest(prepared=prepared),
                patch.object(
                    self.Record.Permission, "name", {"read": rules}, create=True
                ),
                patch.object(
                    graphql_resolvers, "check_read_permission", wraps=original
                ) as check,
            ):
                self.authorize(
                    [self.record], ["name", "name__exact"], prepared=prepared
                )
                assert rules.iterations == 6
                assert [call.args[2] for call in check.call_args_list] == [
                    "name",
                    "name",
                ]
