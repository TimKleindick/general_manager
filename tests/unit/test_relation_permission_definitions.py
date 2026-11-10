"""Relation and raw-ID spellings must enforce the same declared field gates."""

from __future__ import annotations

from typing import ClassVar
import subprocess
import sys

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.db import models
from django.test.utils import isolate_apps

from general_manager.interface import ExistingModelInterface, RequestInterface
from general_manager.interface.requests import (
    RequestField,
    RequestQueryOperation,
    RequestTransportConfig,
    SharedRequestTransport,
)
from general_manager.manager import GeneralManager, Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission import manager_based_permission as policies
from general_manager.permission.permission_data_manager import PermissionDataManager


class _NoMetadataRequests(SharedRequestTransport):
    def send(self, *args, **kwargs):
        """Reject accidental data access while resolving declaration metadata."""
        raise AssertionError


@pytest.fixture(scope="module")
def relation_managers():
    """Expose real ORM/request schemas without creating tables or fetching rows."""
    owned = []
    with isolate_apps():

        class AliasRuleTarget(models.Model):
            class Meta:
                app_label = "general_manager"

        class AliasRuleSource(models.Model):
            owner = models.ForeignKey(AliasRuleTarget, on_delete=models.CASCADE)
            single = models.OneToOneField(
                AliasRuleTarget, on_delete=models.CASCADE, related_name="+"
            )
            code = models.CharField(max_length=20)
            code_id = models.IntegerField()
            tags = models.ManyToManyField(AliasRuleTarget, related_name="+")

            class Meta:
                app_label = "general_manager"

        class OrmAliasRuleManager(GeneralManager):
            class Interface(ExistingModelInterface):
                model = AliasRuleSource

        class RequestAliasRuleTarget(GeneralManager):
            class Interface(RequestInterface):
                id = Input(int)
                label = RequestField(str)

                class Meta:
                    query_operations: ClassVar[dict] = {
                        "detail": RequestQueryOperation("detail", path="/targets/{id}")
                    }
                    transport = _NoMetadataRequests()
                    transport_config = RequestTransportConfig(
                        base_url="https://example.test"
                    )

        class RequestAliasRuleManager(GeneralManager):
            class Interface(RequestInterface):
                id = Input(int)
                owner = RequestField(RequestAliasRuleTarget)
                optional = RequestField(RequestAliasRuleTarget | None)
                code = RequestField(str)
                code_id = RequestField(int)
                tags = RequestField(list[RequestAliasRuleTarget])

                class Meta:
                    query_operations: ClassVar[dict] = {
                        "detail": RequestQueryOperation("detail", path="/sources/{id}")
                    }
                    transport = _NoMetadataRequests()
                    transport_config = RequestTransportConfig(
                        base_url="https://example.test"
                    )

        owned.extend(
            (OrmAliasRuleManager, RequestAliasRuleTarget, RequestAliasRuleManager)
        )
        try:
            yield {"orm": OrmAliasRuleManager, "request": RequestAliasRuleManager}
        finally:
            for name in (
                "all_classes",
                "pending_graphql_interfaces",
                "pending_attribute_initialization",
            ):
                setattr(
                    GeneralManagerMeta,
                    name,
                    [
                        manager
                        for manager in getattr(GeneralManagerMeta, name)
                        if manager not in owned
                    ],
                )


def _permission(kind, declarations, *, base="public"):
    """Build a plain policy whose CRUD defaults are independent of settings."""
    return type(
        "AliasPermission",
        (kind,),
        {
            "__read__": [base],
            "__create__": [base],
            "__update__": [base],
            "__delete__": [base],
            **declarations,
        },
    )


def _subject(manager, context="payload"):
    """Cover class, instance, and mutation-wrapper manager-context extraction."""
    if context == "class":
        return manager
    if context == "instance":
        return object.__new__(manager)
    return PermissionDataManager({}, manager)


@pytest.mark.parametrize("schema", ["orm", "request"])
@pytest.mark.parametrize(
    "kind", [policies.AdditiveManagerPermission, policies.OverrideManagerPermission]
)
@pytest.mark.parametrize("action", ["create", "read", "update", "delete"])
@pytest.mark.parametrize("declared", ["owner", "owner_id"])
def test_one_field_definition_restricts_both_aliases(
    relation_managers, schema, kind, action, declared
):
    """Changing the submitted spelling must never bypass a sole field restriction."""
    Permission = _permission(kind, {declared: {action: ["isAdmin"]}})
    permission = Permission(_subject(relation_managers[schema]), AnonymousUser())
    assert permission.check_permission(action, "name") is True
    for attribute in ("owner", "owner_id"):
        assert permission.check_permission(action, attribute) is False


@pytest.mark.parametrize("schema", ["orm", "request"])
@pytest.mark.parametrize("context", ["payload", "class", "instance"])
def test_alias_context_does_not_read_candidate_data(relation_managers, schema, context):
    """Only owning manager metadata, never deferred values, identifies aliases."""
    Permission = _permission(
        policies.OverrideManagerPermission, {"owner_id": {"update": ["isAdmin"]}}
    )
    permission = Permission(
        _subject(relation_managers[schema], context), AnonymousUser()
    )
    assert permission.check_permission("update", "owner") is False


@pytest.mark.parametrize("schema", ["orm", "request"])
@pytest.mark.parametrize(
    "groups,expected",
    [
        ((["public", "isAdmin"], ["isAdmin"]), False),
        (([], ["isAdmin"]), False),
        ((["isAdmin"], []), False),
        (([], ["public"]), True),
    ],
)
def test_explicit_groups_are_and_but_each_list_remains_or(
    relation_managers, schema, groups, expected
):
    """A permissive alias must not override the other alias's stricter group."""
    Permission = _permission(
        policies.OverrideManagerPermission,
        {
            "owner": {"update": groups[0]},
            "owner_id": {"update": groups[1]},
        },
    )
    permission = Permission(_subject(relation_managers[schema]), AnonymousUser())
    assert permission.check_permission("update", "owner") is expected
    assert permission.check_permission("update", "owner_id") is expected


@pytest.mark.parametrize(
    "kind,expected",
    [
        (policies.AdditiveManagerPermission, False),
        (policies.OverrideManagerPermission, True),
    ],
)
def test_empty_alias_rule_preserves_merge_and_action_cache_semantics(
    relation_managers, kind, expected
):
    """An empty field override is present without turning unrelated row gates public."""
    Permission = _permission(kind, {"owner_id": {"read": []}}, base="isAdmin")
    permission = Permission(_subject(relation_managers["orm"]), AnonymousUser())
    assert permission.check_permission("read", "owner") is expected
    assert permission.check_permission("read", "name") is False


@pytest.mark.parametrize("schema", ["orm", "request"])
def test_partial_action_dicts_select_both_aliases_independently(
    relation_managers, schema
):
    """A dictionary for one action must not hide the other alias's different action."""
    Permission = _permission(
        policies.OverrideManagerPermission,
        {
            "owner": {"update": ["isAdmin"]},
            "owner_id": {"read": ["public"]},
        },
        base="isAdmin",
    )
    permission = Permission(_subject(relation_managers[schema]), AnonymousUser())
    for alias in ("owner", "owner_id"):
        assert permission.check_permission("read", alias) is True
        assert permission.check_permission("update", alias) is False
        assert permission.check_permission("delete", alias) is False


@pytest.mark.parametrize("schema", ["orm", "request"])
def test_equal_alias_definitions_evaluate_only_once(relation_managers, schema):
    """Copied and shared equal rule groups must not repeat custom callbacks."""

    class Permission(policies.OverrideManagerPermission):
        __update__: ClassVar[list[str]] = ["isAdmin"]
        owner: ClassVar[dict] = {"update": ["public"]}
        owner_id: ClassVar[dict] = {"update": ["public"]}

        def validate_permission_string(self, expression):
            """Count real predicate evaluation while keeping normal callback behavior."""
            self.evaluations.append(expression)
            return super().validate_permission_string(expression)

    permission = Permission(_subject(relation_managers[schema]), AnonymousUser())
    permission.evaluations = []
    assert permission.check_permission("update", "owner_id") is True
    assert permission.evaluations == ["public"]


@pytest.mark.parametrize("schema", ["orm", "request"])
def test_nonrelations_and_collection_spellings_remain_independent(
    relation_managers, schema
):
    """Scalar suffixes and M2M/list spellings must never acquire relation rules."""
    Permission = _permission(
        policies.OverrideManagerPermission,
        {
            "code": {"update": ["isAdmin"]},
            "tags_list": {"update": ["isAdmin"]},
        },
    )
    permission = Permission(_subject(relation_managers[schema]), AnonymousUser())
    assert permission.check_permission("update", "code") is False
    assert permission.check_permission("update", "code_id") is True
    assert permission.check_permission("update", "tags_id_list") is True


def test_o2o_and_optional_request_relations_use_declared_pairs(relation_managers):
    """Both supported singular relation kinds share their explicit ID gate."""
    for schema, field in (("orm", "single"), ("request", "optional")):
        Permission = _permission(
            policies.OverrideManagerPermission, {f"{field}_id": {"update": ["isAdmin"]}}
        )
        assert (
            Permission(
                _subject(relation_managers[schema]), AnonymousUser()
            ).check_permission("update", field)
            is False
        )


def test_snapshots_mro_and_live_dictionary_contents_are_preserved(relation_managers):
    """Resolve MRO dictionaries before actions without freezing their mutable contents."""

    class Parent(policies.OverrideManagerPermission):
        __update__: ClassVar[list[str]] = ["public"]
        owner: ClassVar[dict] = {"update": ["isAdmin"]}

    class Child(Parent):
        owner: ClassVar[dict] = {"read": ["public"]}
        owner_id: ClassVar[dict] = {"update": ["public"]}

    subject = _subject(relation_managers["orm"])
    previous = Child(subject, AnonymousUser())
    assert previous.check_permission("update", "owner") is True
    Child.owner_id["update"][:] = ["isAdmin"]
    assert previous.check_permission("update", "owner") is False
    Child.owner_id = {"update": ["public"]}
    assert previous.check_permission("update", "owner") is False
    assert Child(subject, AnonymousUser()).check_permission("update", "owner") is True
    assert (
        Child(subject, User(is_superuser=True)).check_permission("update", "owner")
        is True
    )


@pytest.mark.parametrize("schema", ["orm", "request"])
@pytest.mark.parametrize(
    "declared,queried", [("owner", "owner_id"), ("owner_id", "owner")]
)
def test_static_read_classifier_uses_owning_manager_context(
    relation_managers, schema, declared, queried
):
    """Read optimizations must retain a counterpart-only restrictive field gate."""
    Permission = _permission(
        policies.OverrideManagerPermission, {declared: {"read": ["isAdmin"]}}
    )
    assert (
        policies.additional_local_read_permission(
            Permission, queried, manager=relation_managers[schema]
        )
        is True
    )
    assert policies.additional_local_read_permission(Permission, queried) is None
    assert (
        policies.additional_local_read_permission(
            Permission, "code_id", manager=relation_managers[schema]
        )
        is False
    )


def test_static_read_classifier_keeps_conflicting_groups_separate(relation_managers):
    """A public group must not make a stricter counterpart look row-equivalent."""
    Permission = _permission(
        policies.OverrideManagerPermission,
        {
            "owner": {"read": ["public"]},
            "owner_id": {"read": ["isAdmin"]},
        },
    )
    for alias in ("owner", "owner_id"):
        assert (
            policies.additional_local_read_permission(
                Permission, alias, manager=relation_managers["orm"]
            )
            is True
        )


def test_alias_merge_hooks_keep_flat_groups_and_deterministic_order(relation_managers):
    """Preserve private flat-list hook contracts and canonical group ordering."""

    class Permission(policies.OverrideManagerPermission):
        owner: ClassVar[dict] = {"update": ["public"]}
        owner_id: ClassVar[dict] = {"update": ["public", "isAdmin"]}

        def _evaluate_local_permission(self, **kwargs):
            """Record the existing merge hook's single flat OR group."""
            self.groups.append(kwargs["attribute_permissions"])
            assert all(
                isinstance(rule, str) for rule in kwargs["attribute_permissions"]
            )
            return super()._evaluate_local_permission(**kwargs)

    permission = Permission(_subject(relation_managers["orm"]), AnonymousUser())
    permission.groups = []
    assert permission.check_permission("update", "owner") is True
    assert permission.check_permission("update", "owner_id") is True
    assert permission.groups == [["public"], ["public"]]
    assert permission.describe_permissions("update", "owner") == (
        "public",
        "public",
        "isAdmin",
    )
    assert permission.describe_permissions("update", "owner_id") == (
        "public",
        "public",
        "isAdmin",
    )


@pytest.mark.parametrize(
    "expression",
    [
        "PermissionDataManager({'count': 3}).count == 3",
        "PermissionDataManager({'count': 3}, GeneralManager).count == 3",
        "permission_attribute_names(None, 'count') == ('count',)",
        "permission_attribute_names(GeneralManager, 'count') == ('count',)",
    ],
)
def test_plain_metadata_context_does_not_require_the_app_registry(expression):
    """Keep manager-free dictionary and metadata access usable before Django setup."""
    code = (
        "from django.conf import settings; settings.configure(GENERAL_MANAGER={}); "
        "from general_manager.permission.permission_data_manager import PermissionDataManager; "
        "from general_manager.permission.relation_aliases import permission_attribute_names; "
        "from general_manager.manager.general_manager import GeneralManager; import sys; "
        f"assert {expression}; "
        "assert 'general_manager.interface.orm_interface' not in sys.modules; "
        "assert 'general_manager.interface.interfaces.request' not in sys.modules"
    )
    result = subprocess.run(  # noqa: S603 - executable and expressions are test constants
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
