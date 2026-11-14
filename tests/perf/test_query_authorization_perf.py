"""Operation-count gates for native query authorization, not permission caches."""

from __future__ import annotations

import sys
from collections import Counter
from contextlib import contextmanager, nullcontext
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection, models
from django.db.models.query import ModelIterable
from django.db.models.sql.query import Query
from django.test.utils import CaptureQueriesContext
from graphql import GraphQLError

from general_manager.api import graphql_resolvers
from general_manager.api import graphql_contributors
from general_manager.api import graphql_query_permissions as permissions
from general_manager.api.graphql_contributors import ContributorAuthorization
from general_manager.api.graphql_relations import resolve_general_manager_type
from general_manager.as_of import HistoricalContextConflictError, as_of
from general_manager.bucket._materialized_bucket import MaterializedBucket
from general_manager.bucket.calculation_bucket import (
    CalculationBucket,
    IncompatibleBucketTypeError,
)
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.run_context import CalculationRunContext
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.interface.capabilities.orm.support import OrmReadCapability
from general_manager.interface.capabilities.calculation.lifecycle import (
    CalculationReadCapability,
)
from general_manager.manager import GeneralManager, Input
from general_manager.manager import general_manager as manager_implementation
from general_manager.permission import OverrideManagerPermission
from general_manager.permission import manager_based_permission as policy_definitions
from general_manager.permission import utils as permission_utils
from general_manager.permission.permission_checks import permission_functions
from general_manager.utils.testing import GeneralManagerTransactionTestCase


@contextmanager
def structural_calls():
    """Observe real code entries without replacing the attested metadata methods."""
    codes = {
        OrmReadCapability.get_attribute_types.__code__: "types",
        OrmReadCapability.get_attributes.__code__: "attributes",
        permissions._NativePathNode.__init__.__code__: "path_nodes",
        CalculationBucket.with_instances.__code__: "calculation_snapshots",
        MaterializedBucket.with_instances.__code__: "snapshot_copies",
        permissions._prepare_native_query_structure.__code__: "native_preparations",
        permissions._prepare_pushdown_structure.__code__: "pushdown_preparations",
        ContributorAuthorization.canonical_authority.__code__: "authority_proofs",
        policy_definitions._canonical_read_executables.__code__: "policy_attestations",
        graphql_resolvers.relation_id_alias_field.__code__: "relation_aliases",
        resolve_general_manager_type.__code__: "manager_type_resolutions",
        manager_implementation._COMMON_MANAGER_IDENTIFICATION[0].fget.__code__: (
            "identification_reads"
        ),
        policy_definitions._AttributePermissionDefinitions.__getitem__.__code__: (
            "raising_rule_lookups"
        ),
        CalculationReadCapability.get_attribute_types.__code__: "calculation_types",
        CalculationReadCapability.get_attributes.__code__: "calculation_accessors",
        permissions._operand_field_names.__code__: "operand_shapes",
        graphql_resolvers.check_read_permission.__code__: "runtime_field_checks",
        policy_definitions.additional_local_read_permission.__code__: (
            "local_read_classifications"
        ),
    }
    counts = Counter()
    previous = sys.getprofile()

    def observe(frame, event, arg):
        if event == "call" and frame.f_code in codes:
            counts[codes[frame.f_code]] += 1
        if (
            event == "call"
            and frame.f_code.co_filename
            == permission_utils.validate_permission_string.__code__.co_filename
        ):
            counts["expression_frames"] += 1
        if event == "call" and frame.f_code.co_qualname.startswith(
            policy_definitions._AttributePermissionDefinitions.__init__.__qualname__
        ):
            counts["declaration_snapshot_frames"] += 1
        if previous is not None:
            previous(frame, event, arg)

    sys.setprofile(observe)
    try:
        yield counts
    finally:
        sys.setprofile(previous)


@contextmanager
def contributor_static_reads():
    """Observe the owning inspection without changing authority executables."""
    original = graphql_contributors.getattr_static
    reads = []

    def observed(owner, name, *default):
        value = original(owner, name, *default)
        reads.append((owner, name, value))
        return value

    with patch.object(graphql_contributors, "getattr_static", observed):
        yield reads


@pytest.mark.perf
def test_permission_expression_work_keeps_callbacks_and_fresh_configs():
    """Evaluate every fragment with one utility frame and fresh callback input."""
    seen = []
    actor = AnonymousUser()
    subject = object()

    def allowed(instance, user, config):
        seen.append((instance, user, config[:]))
        config.append("changed by callback")
        return True

    with (
        patch.dict(
            permission_functions, {"perfAllowed": {"permission_method": allowed}}
        ),
        structural_calls() as counts,
    ):
        for _ in range(1000):
            assert permission_utils.validate_permission_string(
                "perfAllowed:first&perfAllowed:second&perfAllowed", subject, actor
            )

    assert seen == [
        (subject, actor, config)
        for _ in range(1000)
        for config in (["first"], ["second"], [])
    ]
    assert counts["expression_frames"] == 1000


@pytest.mark.perf
class TestQueryAuthorizationWork(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class PerfQueryTarget(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=30)
                region = models.CharField(max_length=30)

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        class PerfQuerySource(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=30)
                hours = models.IntegerField()
                target = models.ForeignKey(
                    "general_manager.PerfQueryTarget", on_delete=models.CASCADE
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        class PerfQueryCalendar(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(int, possible_values=range(10))
                day = Input.date_range(start=date(2024, 1, 1), end=date(2028, 12, 31))

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        class PerfRelatedCalendar(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(
                    PerfQueryTarget, possible_values=lambda: PerfQueryTarget.all()
                )
                day = Input.date_range(start=date(2024, 1, 1), end=date(2028, 12, 31))

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        cls.Source, cls.Target = PerfQuerySource, PerfQueryTarget
        cls.Calendar = PerfQueryCalendar
        cls.RelatedCalendar = PerfRelatedCalendar
        cls.general_manager_classes = [
            PerfQueryTarget,
            PerfQuerySource,
            PerfQueryCalendar,
            PerfRelatedCalendar,
        ]
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.info = SimpleNamespace(context=SimpleNamespace(user=AnonymousUser()))
        self.target = self.Target.Factory.create(name="account", region="EU")
        model = self.Source.Interface._model
        model.objects.bulk_create(
            [model(name=str(i), hours=1, target_id=self.target.id) for i in range(1000)]
        )
        self.rows = list(self.Source.all())

    def test_native_projection_metadata_is_bounded_for_1000_rows(self):
        """Only schema work is shared; all 2,000 field decisions still execute."""
        checks = []
        original = graphql_resolvers.check_read_permission

        def checked(instance, info, field, **kwargs):
            checks.append((instance.id, field))
            return original(instance, info, field, **kwargs)

        counts = []
        with patch.object(graphql_resolvers, "check_read_permission", checked):
            for rows in (self.rows[:2], self.rows):
                with structural_calls() as observed:
                    permissions.authorize_query_fields(
                        rows, self.info, ("name", "hours"), native_paths=True
                    )
                counts.append(observed)

        assert len(self.rows) == 1000
        assert checks[4:] == [
            (row.id, field) for row in self.rows for field in ("hours", "name")
        ]
        for kind in ("types", "attributes"):
            assert counts[1][kind] <= counts[0][kind], (kind, counts)

    def test_field_rules_keep_callbacks_with_bounded_constructor_work(self):
        """Every field retains its fresh snapshot and callback with bounded work."""
        visits = []
        public = permission_functions["public"]

        def checked(instance, user, config):
            visits.append(instance.id)
            return public["permission_method"](instance, user, config)

        with (
            patch.dict(
                permission_functions,
                {"public": {**public, "permission_method": checked}},
            ),
            structural_calls() as counts,
        ):
            for row in self.rows:
                assert self.Source.Permission(
                    row, self.info.context.user
                ).check_permission("read", "name")

        assert visits == [row.id for row in self.rows]
        assert counts["raising_rule_lookups"] == 0
        assert counts["declaration_snapshot_frames"] == 1000
        declarations = policy_definitions._attribute_permission_definitions(
            self.Source.Permission
        )
        missing = object()
        assert declarations.get("missing", missing) is missing
        with pytest.raises(KeyError, match="missing"):
            declarations["missing"]

    def test_1000_row_hydration_builds_cache_keys_only_with_a_context(self):
        """Context-free iteration retains every wrapper without cache-key work."""
        with patch.object(
            manager_implementation, "id", wraps=id, create=True
        ) as identity:
            uncached = list(self.Source.all())
        assert len(uncached) == 1000
        assert [row.id for row in uncached] == [row.id for row in self.rows]
        assert identity.call_count == 0

        with (
            CalculationRunContext(),
            patch.object(
                manager_implementation, "id", wraps=id, create=True
            ) as identity,
        ):
            bucket = self.Source.all()
            first, second = list(bucket), list(bucket)
        assert len(first) == len(second) == 1000
        assert [row.id for row in first] == [row.id for row in uncached]
        assert all(left is right for left, right in zip(first, second, strict=True))
        assert identity.call_count == 1000

    def test_ten_sorted_pages_keep_the_full_1000_row_permission_scope(self):
        """Fresh policies retain all 10,000 off-page sort checks and all results."""
        checked = []
        original = graphql_resolvers.check_read_permission

        def authorize(instance, info, field, **kwargs):
            checked.append((instance.id, field))
            return original(instance, info, field, **kwargs)

        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Source.all(), self.Source
        )
        with (
            patch.object(graphql_resolvers, "check_read_permission", authorize),
            structural_calls() as counts,
        ):
            pages = [
                resolver(
                    None,
                    self.info,
                    order_by=[{"field": "id", "direction": "ASC"}],
                    page=page,
                    page_size=100,
                )
                for page in range(1, 11)
            ]

        expected = sorted(row.id for row in self.rows)
        assert [row.id for page in pages for row in page["items"]] == expected
        assert all(page["pageInfo"]["total_count"] == 1000 for page in pages)
        expected_checks = Counter((row.id, "id") for row in self.rows)
        assert len(checked) == 10000
        for page in range(10):
            assert Counter(checked[page * 1000 : (page + 1) * 1000]) == expected_checks
        # Each page also creates one class-level planning policy.
        assert counts["declaration_snapshot_frames"] == 10010

    def test_ten_native_public_pages_skip_unused_rights_and_operand_scans(self):
        """Unchanged 1,000-row/page workload keeps lazy SQL and every result."""
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Source.all(), self.Source
        )
        with (
            patch.object(
                self.Source.Permission,
                "unused",
                {"read": ["unknownUnusedRule"]},
                create=True,
            ),
            CaptureQueriesContext(connection) as queries,
            structural_calls() as counts,
        ):
            pages = [
                resolver(
                    None,
                    self.info,
                    order_by=[{"field": "id", "direction": "ASC"}],
                    page=page,
                    page_size=100,
                )
                for page in range(1, 11)
            ]
        assert [row.id for page in pages for row in page["items"]] == sorted(
            row.id for row in self.rows
        )
        assert all(page["pageInfo"]["total_count"] == 1000 for page in pages)
        assert len(queries) == 20
        assert counts["runtime_field_checks"] == 0
        assert counts["declaration_snapshot_frames"] <= 50
        assert counts["local_read_classifications"] <= 50

    def test_unknown_field_policy_keeps_callbacks_without_n_plus_one_or_metadata_scan(
        self,
    ):
        """Every matching row retains its custom decision before a filled page."""
        seen = []

        def allowed(instance, _user, _config):
            seen.append(instance.id)
            return True

        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Source.all(), self.Source
        )
        with (
            patch.dict(
                permission_functions,
                {
                    "contributorPerf": {
                        "permission_method": allowed,
                        "permission_filter": lambda *_: None,
                    }
                },
            ),
            patch.object(
                self.Source.Permission,
                "hours",
                {"read": ["contributorPerf"]},
                create=True,
            ),
            CaptureQueriesContext(connection) as queries,
            structural_calls() as counts,
        ):
            result = resolver(
                None, self.info, filter={"hours__gte": 1}, page=1, page_size=100
            )
        assert seen == [row.id for row in self.rows]
        assert len(result["items"]) == 100
        assert result["pageInfo"]["total_count"] == 1000
        assert len(queries) == 3
        assert counts["types"] <= 16
        assert counts["local_read_classifications"] <= 16
        assert counts["declaration_snapshot_frames"] <= 1005
        assert counts["authority_proofs"] <= 2

    def test_definition_preparation_attests_executables_once(self):
        with structural_calls() as counts:
            assert policy_definitions._can_prepare_local_read_definitions(
                self.Source.Permission
            )
        assert counts["policy_attestations"] == 1
        assert counts["declaration_snapshot_frames"] == 0

    def test_runtime_scalar_shape_is_bounded_with_every_callback_and_identity_read(
        self,
    ):
        """Share passive scalar resolution, keeping each object's runtime gate."""
        seen = []

        def allowed(instance, _user, _config):
            seen.append(instance)
            return True

        with (
            patch.object(
                self.Source.Permission, "hours", {"read": ["scalarShape"]}, create=True
            ),
            patch.dict(
                permission_functions, {"scalarShape": {"permission_method": allowed}}
            ),
        ):
            for path in ("hours", "hours__gte"):
                counts = []
                for rows in (self.rows[:64], self.rows):
                    seen.clear()
                    authorizer = ContributorAuthorization(self.info)
                    with structural_calls() as observed:
                        for row in rows:
                            assert authorizer.path(row, path) == (True, {})
                    counts.append(observed)
                    assert len(seen) == len(rows)
                    assert all(
                        actual is expected
                        for actual, expected in zip(seen, rows, strict=True)
                    )
                    assert authorizer.budget.decisions == len(rows)
                    assert observed["identification_reads"] == len(rows)
                for name in (
                    "relation_aliases",
                    "manager_type_resolutions",
                    "operand_shapes",
                ):
                    assert counts[1][name] == counts[0][name], (path, name, counts)

    def test_runtime_scalar_shape_keeps_identity_budget_and_error_order(self):
        seen = []

        def allowed(instance, _user, _config):
            seen.append(instance)
            return True

        with (
            patch.object(
                self.Source.Permission, "hours", {"read": ["scalarShape"]}, create=True
            ),
            patch.dict(
                permission_functions, {"scalarShape": {"permission_method": allowed}}
            ),
        ):
            authorizer = ContributorAuthorization(self.info)
            authorizer.budget.limit = 1
            assert authorizer.path(self.rows[0], "hours") == (True, {})
            with pytest.raises(GraphQLError, match="candidate limit"):
                authorizer.path(self.rows[1], "hours")
            assert len(seen) == 1
            seen.clear()
            with (
                patch.object(self.rows[0], "_GeneralManager__id", None),
                pytest.raises(TypeError),
            ):
                ContributorAuthorization(self.info).path(self.rows[0], "hours")
            assert not seen
            with pytest.raises(permissions.QueryFieldPermissionError):
                ContributorAuthorization(self.info).path(self.rows[0], "hours__invalid")
            assert len(seen) == 1

    def test_cached_unknown_permission_reuses_only_attested_structure(self):
        seen = []

        def allowed(instance, _user, _config):
            seen.append(instance)
            return True

        with (
            patch.object(
                self.Source.Permission,
                "hours",
                {"read": ["opaqueStructure"]},
                create=True,
            ),
            patch.dict(
                permission_functions,
                {"opaqueStructure": {"permission_method": allowed}},
            ),
            contributor_static_reads() as reads,
        ):
            authorizer = ContributorAuthorization(self.info)
            for row in self.rows:
                assert authorizer.field_allowed(row, "hours")
            policy_reads = [
                (owner, value)
                for owner, name, value in reads
                if type(owner) is self.Source and name == "Permission"
            ]
            assert len(policy_reads) == 1
            assert policy_reads[0][0] is self.rows[0]
            assert policy_reads[0][1] is self.Source.Permission
            assert (
                sum(
                    owner is self.Source.Permission and name == "__based_on__"
                    for owner, name, _ in reads
                )
                == 1000
            )
        assert len(seen) == authorizer.budget.decisions == 1000
        assert all(
            actual is expected for actual, expected in zip(seen, self.rows, strict=True)
        )

    def test_cached_unknown_permission_keeps_replaced_instance_policy_fallback(self):
        class Restricted(OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]
            hours: ClassVar[dict] = {"read": ["replacementPolicy"]}

        failure = RuntimeError("replacement instance policy failed")
        seen = []

        def replacement(instance, _user, _config):
            seen.append(instance)
            if instance is self.rows[2]:
                raise failure
            return False

        with (
            patch.object(
                self.Source.Permission,
                "hours",
                {"read": ["opaqueStructure"]},
                create=True,
            ),
            patch.dict(
                permission_functions,
                {
                    "opaqueStructure": {"permission_method": lambda *_: True},
                    "replacementPolicy": {"permission_method": replacement},
                },
            ),
            patch.object(self.rows[1], "Permission", Restricted),
            patch.object(self.rows[2], "Permission", Restricted),
            contributor_static_reads() as reads,
        ):
            authorizer = ContributorAuthorization(self.info)
            assert authorizer.field_allowed(self.rows[0], "hours")
            assert not authorizer.field_allowed(self.rows[1], "hours")
            with pytest.raises(RuntimeError) as observed:
                authorizer.field_allowed(self.rows[2], "hours")
            assert observed.value is failure
            policy_reads = [
                (owner, value)
                for owner, name, value in reads
                if type(owner) is self.Source and name == "Permission"
            ]
            assert len(policy_reads) == 3
            assert all(value is Restricted for _, value in policy_reads[1:])
        assert len(seen) == 2
        assert authorizer.budget.decisions == 3

    def test_fresh_unknown_plan_keeps_provider_policy_mutation_inspection(self):
        class Restricted(OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]
            hours: ClassVar[dict] = {"read": ["replacementPolicy"]}

        row = self.rows[0]
        providers = []

        def change_policy(*_args):
            providers.append(True)
            row.Permission = Restricted
            return None

        with (
            patch.object(
                self.Source.Permission, "hours", {"read": ["mutatingPlan"]}, create=True
            ),
            patch.dict(
                permission_functions,
                {
                    "mutatingPlan": {
                        "permission_method": lambda *_: True,
                        "permission_filter": change_policy,
                    },
                    "replacementPolicy": {"permission_method": lambda *_: False},
                },
            ),
            patch.object(row, "Permission", self.Source.Permission),
            contributor_static_reads() as reads,
        ):
            authorizer = ContributorAuthorization(self.info)
            assert not authorizer.field_allowed(row, "hours")
            policy_reads = [
                value
                for owner, name, value in reads
                if owner is row and name == "Permission"
            ]
            assert policy_reads == [Restricted]
        assert providers == [True]
        assert authorizer.budget.decisions == 1

    def test_cached_unknown_permission_keeps_fresh_delegation_error_before_charge(self):
        with (
            patch.object(
                self.Source.Permission,
                "hours",
                {"read": ["opaqueStructure"]},
                create=True,
            ),
            patch.dict(
                permission_functions,
                {"opaqueStructure": {"permission_method": lambda *_: True}},
            ),
        ):
            authorizer = ContributorAuthorization(self.info)
            assert authorizer.field_allowed(self.rows[0], "hours")
            with patch.object(
                self.Source.Permission, "__based_on__", "missing_relation"
            ):
                with pytest.raises(policy_definitions.InvalidBasedOnConfigurationError):
                    authorizer.field_allowed(self.rows[1], "hours")
            assert authorizer.budget.decisions == 1

    def test_native_unpaged_filter_materializes_once_for_omitted_and_null_controls(
        self,
    ):
        selected = [row.id for row in self.rows[:10]]
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Source.all(), self.Source
        )
        for controls in ({}, {"page": None}, {"page": None, "page_size": None}):
            with (
                self.subTest(controls=controls),
                CaptureQueriesContext(connection) as queries,
            ):
                result = resolver(
                    None,
                    self.info,
                    filter={"id__in": selected, "target_id": self.target.id},
                    **controls,
                )
            assert [row.id for row in result["items"]] == selected
            assert result["pageInfo"]["total_count"] == 10
            assert len(queries) == 1

    def test_native_unpaged_empty_iteration_keeps_dependencies(self):
        self.Source.Interface._model.objects.all().delete()
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Source.all(), self.Source
        )
        for filters in ({}, {"hours__lt": 0}):
            with (
                self.subTest(filters=filters),
                DependencyTracker() as dependencies,
                CaptureQueriesContext(connection) as queries,
            ):
                result = resolver(None, self.info, filter=filters)
            assert result["items"] == []
            assert result["pageInfo"]["total_count"] == 0
            assert len(queries) == 1
            if filters:
                assert any(
                    name == self.Source.__name__
                    and operation == "filter"
                    and "hours__lt" in payload
                    for name, operation, payload in dependencies
                )
            else:
                assert (self.Source.__name__, "all", "") in dependencies

    def test_native_unpaged_respects_terminal_hooks_and_error_order(self):
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Source.all(), self.Source
        )
        for owner, name in (
            (DatabaseBucket, "count"),
            (DatabaseBucket, "__len__"),
            (DatabaseBucket, "__iter__"),
            (models.QuerySet, "count"),
        ):
            failure = RuntimeError("custom terminal failed")

            def fail(*_args, failure=failure, **_kwargs):
                raise failure

            with (
                self.subTest(name=name, owner=owner),
                patch.object(owner, name, fail),
                self.assertRaises(RuntimeError) as observed,
            ):
                resolver(None, self.info)
            assert observed.exception is failure
        with patch.object(DatabaseBucket, "count", return_value=77) as count:
            result = resolver(None, self.info)
        assert result["pageInfo"]["total_count"] == 77
        assert len(result["items"]) == 1000
        assert count.call_count == 2

        count_failure, iter_failure = (
            RuntimeError("count failed first"),
            RuntimeError("iteration must follow count"),
        )
        with (
            patch.object(DatabaseBucket, "count", side_effect=count_failure),
            patch.object(DatabaseBucket, "__iter__", side_effect=iter_failure),
            self.assertRaises(RuntimeError) as observed,
        ):
            resolver(None, self.info)
        assert observed.exception is count_failure

    def test_native_unpaged_keeps_dynamic_count_dispatch_errors(self):
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Source.all(), self.Source
        )
        for owner, lookup in ((models.QuerySet, "count"), (Query, "get_count")):
            failure = RuntimeError("dynamic count dispatch failed")

            def intercepted(instance, name, *, lookup=lookup, failure=failure):
                if name == lookup:
                    raise failure
                return object.__getattribute__(instance, name)

            with (
                self.subTest(owner=owner),
                patch.object(owner, "__getattribute__", intercepted),
            ):
                with self.assertRaises(RuntimeError) as observed:
                    resolver(None, self.info)
                assert observed.exception is failure

    def test_native_unpaged_keeps_custom_iteration_count_semantics(self):
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Source.all(), self.Source
        )

        def empty_cache(queryset):
            queryset._result_cache = []

        for owner, name, replacement in (
            (models.QuerySet, "_fetch_all", empty_cache),
            (ModelIterable, "__iter__", lambda _iterable: iter(())),
        ):
            with self.subTest(owner=owner), patch.object(owner, name, replacement):
                result = resolver(None, self.info)
                assert result["items"] == []
                assert result["pageInfo"]["total_count"] == 1000

    def test_contributor_preparation_is_shared_per_class_and_invalidated_by_callback(
        self,
    ):
        authorizer = ContributorAuthorization(self.info)
        with structural_calls() as counts:
            assert authorizer.ordinary_path(self.Source, "target__name")
            assert authorizer.ordinary_path(self.Source, "target__region")
        assert counts["pushdown_preparations"] == 2
        original = self.Source.__getattr__

        def changed_getter(instance, name):
            return original(instance, name)

        def mutate(*_args):
            self.Source.__getattr__ = changed_getter
            return True

        with (
            patch.object(self.Source, "__getattr__", original),
            patch.object(
                self.Source.Permission, "hours", {"read": ["mutateProof"]}, create=True
            ),
            patch.dict(
                permission_functions,
                {
                    "mutateProof": {
                        "permission_method": mutate,
                        "permission_filter": lambda *_: None,
                    }
                },
            ),
        ):
            assert authorizer.field_allowed(self.rows[0], "hours")
            assert not authorizer.ordinary_path(self.Source, "name")

    def test_cached_unknown_plan_keeps_changed_runtime_authority(self):
        for raises in (False, True):
            seen = []
            changed = []
            failure = RuntimeError("changed runtime authority")

            def replacement(
                instance,
                _info,
                _field,
                *,
                changed=changed,
                raises=raises,
                failure=failure,
            ):
                changed.append(instance.id)
                if raises:
                    raise failure
                return False

            def mutate(instance, _user, _config, *, seen=seen, replacement=replacement):
                seen.append(instance.id)
                graphql_resolvers.check_read_permission = replacement
                return True

            resolver = graphql_resolvers.create_list_resolver(
                lambda *_: self.Source.all(), self.Source
            )
            with (
                self.subTest(raises=raises),
                patch.object(
                    graphql_resolvers,
                    "check_read_permission",
                    graphql_resolvers.check_read_permission,
                ),
                patch.object(
                    self.Source.Permission,
                    "hours",
                    {"read": ["mutateAuthority"]},
                    create=True,
                ),
                patch.dict(
                    permission_functions,
                    {
                        "mutateAuthority": {
                            "permission_method": mutate,
                            "permission_filter": lambda *_: None,
                        }
                    },
                ),
                pytest.raises(RuntimeError, match="changed runtime authority")
                if raises
                else nullcontext(),
            ):
                result = resolver(
                    None,
                    self.info,
                    order_by=[{"field": "hours", "direction": "ASC"}],
                    page=1,
                    page_size=1,
                )
            assert seen == [self.rows[0].id]
            assert changed[0] == self.rows[1].id
            if not raises:
                assert result["pageInfo"]["total_count"] == 1
                assert [row.id for row in result["items"]] == seen

    def test_public_calendar_has_no_redundant_operand_walk_or_empty_deferred_snapshot(
        self,
    ):
        """A 365-day native requested scope is validated by its calculation owner."""
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Calendar.all(), self.Calendar
        )
        with structural_calls() as counts:
            result = resolver(
                None,
                self.info,
                filter={
                    "employee": 0,
                    "day__gte": date(2025, 1, 1),
                    "day__lte": date(2025, 12, 31),
                },
                order_by=[{"field": "day", "direction": "ASC"}],
            )
        assert len(result["items"]) == result["pageInfo"]["total_count"] == 365
        assert [item.identification["day"] for item in result["items"]] == [
            date(2025, 1, 1) + timedelta(days=offset) for offset in range(365)
        ]
        assert counts["runtime_field_checks"] == 0
        assert counts["operand_shapes"] <= 12
        assert counts["calculation_snapshots"] == 1
        assert counts["local_read_classifications"] <= 12

    def test_sorted_page_keeps_late_field_denial_and_error(self):
        """A first page cannot hide a failing final source-field callback."""
        last_id = self.rows[-1].id
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Source.all(), self.Source
        )
        for raises in (False, True):
            seen = []

            def allowed(instance, _user, _config, *, seen=seen, raises=raises):
                seen.append(instance.id)
                if instance.id == last_id:
                    if raises:
                        message = "last sort candidate failed"
                        raise RuntimeError(message)
                    return False
                return True

            with (
                patch.dict(
                    permission_functions,
                    {"sortCandidate": {"permission_method": allowed}},
                ),
                patch.object(
                    self.Source.Permission,
                    "hours",
                    {"read": ["sortCandidate"]},
                    create=True,
                ),
                pytest.raises(RuntimeError, match="last sort candidate failed")
                if raises
                else nullcontext(),
            ):
                result = resolver(
                    None,
                    self.info,
                    order_by=[{"field": "hours", "direction": "ASC"}],
                    page=1,
                    page_size=100,
                )
            assert seen == [row.id for row in self.rows]
            if not raises:
                assert len(result["items"]) == 100
                assert result["pageInfo"]["total_count"] == 999

    def test_sorted_page_reads_database_changes_made_during_authorization(self):
        """Sorting must use fresh database values after every source check."""
        model = self.Source.Interface._model
        last_id = self.rows[-1].id
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Source.all(), self.Source
        )
        for scoped in (False, True):
            model.objects.update(hours=1)
            seen = []

            def allowed(instance, _user, _config, *, seen=seen):
                seen.append(instance.id)
                if len(seen) == 1:
                    model.objects.filter(pk=last_id).update(hours=-1)
                return True

            with (
                CalculationRunContext() if scoped else nullcontext(),
                patch.dict(
                    permission_functions,
                    {"updateSortValue": {"permission_method": allowed}},
                ),
                patch.object(
                    self.Source.Permission,
                    "hours",
                    {"read": ["updateSortValue"]},
                    create=True,
                ),
                self.assertNumQueries(4),
            ):
                result = resolver(
                    None,
                    self.info,
                    order_by=[{"field": "hours", "direction": "ASC"}],
                    page=1,
                    page_size=100,
                )
            assert len(seen) == 1000
            assert result["items"][0].id == last_id
            assert result["items"][0].hours == -1
            assert result["pageInfo"]["total_count"] == 1000

    def test_sorted_authorized_ids_do_not_admit_rows_inserted_by_a_callback(self):
        model = self.Source.Interface._model
        seen = []
        inserted = []

        def allowed(instance, _user, _config):
            seen.append(instance.id)
            if len(seen) == 1:
                inserted.append(
                    model.objects.create(
                        name="unchecked", hours=-1, target_id=self.target.id
                    ).pk
                )
            return True

        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Source.all(), self.Source
        )
        with (
            patch.object(
                self.Source.Permission,
                "hours",
                {"read": ["insertSortRow"]},
                create=True,
            ),
            patch.dict(
                permission_functions, {"insertSortRow": {"permission_method": allowed}}
            ),
        ):
            result = resolver(
                None,
                self.info,
                order_by=[{"field": "hours", "direction": "ASC"}],
                page=1,
                page_size=100,
            )
        assert model.objects.count() == 1001
        assert len(seen) == 1000
        assert seen == [row.id for row in self.rows]
        assert [row.id for row in result["items"]] == seen[:100]
        assert result["pageInfo"]["total_count"] == 1000
        assert inserted[0] not in seen

    def test_native_projection_trie_allocates_only_unique_prefixes(self):
        """Shared relation prefixes allocate one node while retaining both leaves."""
        paths = ("name", "target__id", "target__name", "target__region")
        with structural_calls() as counts:
            permissions.authorize_query_fields(
                self.rows[:1], self.info, paths, native_paths=True
            )
        # root + name + target + target.{id,name,region}
        assert counts["path_nodes"] == 6

    def test_small_native_batches_do_not_pay_for_structure_preparation(self):
        """Tiny projections retain direct reads instead of batch setup cost."""

        class NativeCandidates:
            def __init__(self, rows):
                self.rows = rows

            def __iter__(self):
                return iter(self.rows)

            def __len__(self):
                raise AssertionError

        for size in (1, 2, 10):
            rows = self.rows[:size]
            for candidates in (rows, tuple(rows), NativeCandidates(rows)):
                for additional_only in (False, True):
                    with structural_calls() as counts:
                        permissions.authorize_query_fields(
                            candidates,
                            self.info,
                            ("name", "hours"),
                            native_paths=True,
                            prepare_native_structure=True,
                            budget_additional_permissions_only=additional_only,
                        )
                    assert counts["native_preparations"] == 0, size

    def test_native_relation_metadata_is_bounded_without_caching_permissions(self):
        """Nested paths reuse schema for each type but revisit all relation gates."""
        counts = []
        visits = []
        row_visits = []
        original = graphql_resolvers.check_read_permission
        original_row_check = graphql_resolvers.can_read_instance

        def checked(instance, info, field, **kwargs):
            visits.append((type(instance), field))
            return original(instance, info, field, **kwargs)

        def checked_row(instance, info):
            row_visits.append((type(instance), instance.id))
            return original_row_check(instance, info)

        row = self.rows[0]
        # Prime the normal relation cache outside the measurement.
        assert row.target.id == self.target.id
        with (
            patch.object(graphql_resolvers, "check_read_permission", checked),
            patch.object(graphql_resolvers, "can_read_instance", checked_row),
        ):
            for rows in ([row] * 2, [row] * 1000):
                with structural_calls() as observed:
                    permissions.authorize_query_fields(
                        rows,
                        self.info,
                        ("target__name", "target__region"),
                        native_paths=True,
                    )
                counts.append(observed)
        assert visits[6:] == [
            (kind, name)
            for _ in range(1000)
            for kind, name in (
                (self.Source, "target"),
                (self.Target, "name"),
                (self.Target, "region"),
            )
        ]
        for kind in ("types", "attributes"):
            assert counts[1][kind] <= counts[0][kind], (kind, counts)
        assert row_visits[2:] == [(self.Target, self.target.id)] * 1000

        denied = []

        def deny_third_row(instance, info):
            denied.append(instance)
            return len(denied) < 3 and original_row_check(instance, info)

        with patch.object(graphql_resolvers, "can_read_instance", deny_third_row):
            with pytest.raises(permissions.QueryRelationPermissionError):
                permissions.authorize_query_fields(
                    [row] * 64, self.info, ("target__name",), native_paths=True
                )
        assert len(denied) == 3

    def test_new_call_observes_new_field_rule_and_keeps_budget(self):
        """Prepared metadata must never share authorization results or accounting."""
        permissions.authorize_query_fields(
            self.rows, self.info, ("hours",), native_paths=True
        )
        with patch.object(
            self.Source.Permission, "hours", {"read": ["isAdmin"]}, create=True
        ):
            with pytest.raises(GraphQLError, match="query field 'hours'"):
                permissions.authorize_query_fields(
                    self.rows, self.info, ("hours",), native_paths=True
                )
        with pytest.raises(GraphQLError, match="candidate limit"):
            permissions.authorize_query_fields(
                self.rows,
                self.info,
                ("hours",),
                native_paths=True,
                budget=permissions.QueryFieldAuthorizationBudget(limit=999),
            )

    def test_empty_deferred_calendar_plan_avoids_a_second_snapshot_copy(self):
        """Retain constructor scoping, snapshot validation and all 1,095 checks."""
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Calendar.all(), self.Calendar
        )
        visits = []
        original = graphql_resolvers.check_read_permission

        def checked(instance, info, field, **kwargs):
            visits.append(field)
            return original(instance, info, field, **kwargs)

        with (
            patch.object(graphql_resolvers, "check_read_permission", checked),
            structural_calls() as counts,
        ):
            result = resolver(
                None,
                self.info,
                filter={
                    "employee": 3,
                    "day__gte": date(2025, 1, 1),
                    "day__lte": date(2025, 12, 31),
                },
            )

        assert len(result["items"]) == 365
        assert {row.employee for row in result["items"]} == {3}
        assert min(row.day for row in result["items"]) == date(2025, 1, 1)
        assert max(row.day for row in result["items"]) == date(2025, 12, 31)
        assert visits == ["day", "day", "employee"] * 365
        # Constructor scoping validates once; there is no empty deferred pass.
        assert counts["calculation_snapshots"] == 1
        assert counts["snapshot_copies"] <= 1

        plan = CalculationBucket(self.Calendar)
        with pytest.raises(IncompatibleBucketTypeError):
            plan.filter_instances(self.rows[:1])
        with as_of(datetime(2020, 1, 1, tzinfo=UTC)):
            with pytest.raises(HistoricalContextConflictError):
                plan.filter_instances(())

    def test_calendar_operand_structure_is_bounded_without_reusing_decisions(self):
        """Growing the calendar keeps schema work fixed and all field callbacks."""
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.Calendar.all(), self.Calendar
        )
        counts = []
        visits = []
        original = graphql_resolvers.check_read_permission

        def checked(instance, info, field, **kwargs):
            visits.append(field)
            return original(instance, info, field, **kwargs)

        for days in (64, 365):
            visits.clear()
            with (
                patch.object(graphql_resolvers, "check_read_permission", checked),
                structural_calls() as observed,
            ):
                result = resolver(
                    None,
                    self.info,
                    filter={
                        "employee": 3,
                        "day__gte": date(2025, 1, 1),
                        "day__lte": date(2025, 1, 1) + timedelta(days=days - 1),
                    },
                )
            counts.append(observed)
            assert len(result["items"]) == days
            assert visits == ["day", "day", "employee"] * days

        for name in (
            "calculation_types",
            "calculation_accessors",
            "operand_shapes",
            "local_read_classifications",
        ):
            assert counts[1][name] <= counts[0][name], (name, counts)

    def test_related_calendar_reuses_schema_and_keeps_all_relation_gates(self):
        """Manager-ID traversal shares schema, while all 365 related rows are checked."""
        resolver = graphql_resolvers.create_list_resolver(
            lambda *_: self.RelatedCalendar.all(), self.RelatedCalendar
        )
        counts = []
        visits = []
        row_visits = []
        original = graphql_resolvers.check_read_permission
        original_row = graphql_resolvers.can_read_instance

        def checked(instance, info, field, **kwargs):
            visits.append(field)
            return original(instance, info, field, **kwargs)

        def checked_row(instance, info):
            row_visits.append((type(instance), instance.id))
            return original_row(instance, info)

        for days in (64, 365):
            visits.clear()
            row_visits.clear()
            with (
                patch.object(graphql_resolvers, "check_read_permission", checked),
                patch.object(graphql_resolvers, "can_read_instance", checked_row),
                structural_calls() as observed,
            ):
                result = resolver(
                    None,
                    self.info,
                    filter={
                        "employee__id": self.target.id,
                        "day__gte": date(2025, 1, 1),
                        "day__lte": date(2025, 1, 1) + timedelta(days=days - 1),
                    },
                )
            counts.append(observed)
            assert len(result["items"]) == days
            assert visits == ["day", "day", "employee", "id"] * days
            assert row_visits == [(self.Target, self.target.id)] * days

        for name in (
            "calculation_types",
            "operand_shapes",
            "local_read_classifications",
        ):
            assert counts[1][name] <= counts[0][name], (name, counts)
        # Resolving employee still builds each instance's own input accessors.
        assert (
            counts[1]["calculation_accessors"] - counts[0]["calculation_accessors"]
            <= 365 - 64
        )
