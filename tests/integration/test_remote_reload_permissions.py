"""Fresh request principals and remote row state govern API read authorization."""

import json
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

import pytest
from django.test import RequestFactory
from django.contrib.auth import get_user_model
from general_manager.api.remote_api import (
    _build_query_view,
    _serialize_readable_manager,
    get_remote_api_config,
)
from general_manager.api.graphql_resolvers import (
    create_list_resolver,
    can_read_instance_for_user,
)
from general_manager.interface import RemoteManagerInterface
from general_manager.interface.requests import RequestField, RequestQueryResult
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission import OverrideManagerPermission
from general_manager.permission.permission_checks import (
    permission_functions,
    register_permission,
)


def reload_row_allowed(instance, _user, _config):
    return bool(instance.allowed)


@pytest.fixture(autouse=True)
def isolate_reload_test_registries(monkeypatch):
    """Keep dynamically declared managers and the test rule out of later startup."""
    for name in (
        "all_classes",
        "read_only_classes",
        "pending_graphql_interfaces",
        "pending_attribute_initialization",
    ):
        monkeypatch.setattr(
            GeneralManagerMeta, name, list(getattr(GeneralManagerMeta, name))
        )
    with patch.dict(permission_functions):
        register_permission("reloadCurrentRowAllowed")(reload_row_allowed)
        yield


def user(staff=True):
    return get_user_model()(id=1, is_staff=staff, is_superuser=False)


def source(rules):
    state = {"allowed": True, "calls": 0}

    class AuditRemote(GeneralManager):
        class Interface(RemoteManagerInterface):
            id = Input(int)
            name = RequestField(str)
            allowed = RequestField(bool)

            class Meta:
                base_url = "https://audit.invalid"
                remote_manager = "audit-remote"

            @classmethod
            def execute_request_plan(cls, plan):
                state["calls"] += 1
                return RequestQueryResult(
                    items=({"id": 1, "name": "secret", "allowed": state["allowed"]},),
                    total_count=1,
                )

        class Permission(OverrideManagerPermission):
            __read__: ClassVar[list[str]] = rules

        class RemoteAPI:
            enabled = True
            resource_name = "audit-remote"
            allow_filter = True

    # Normal application bootstrap supplies the serialization field inventory.
    live = AuditRemote.all()
    snapshot = live.with_instances(list(live))
    assert next(iter(snapshot)).name == "secret"
    assert state["calls"] == 1
    assert snapshot.request_plan is None
    return AuditRemote, snapshot, state


def query(manager, principal):
    request = RequestFactory().post(
        "/gm/audit-remote/query", data={}, content_type="application/json"
    )
    request.user = principal
    response = _build_query_view(get_remote_api_config(manager))(request)
    assert response.status_code == 200, response.content
    return json.loads(response.content)


def graphql_list(manager, snapshot, principal):
    info = SimpleNamespace(
        context=SimpleNamespace(user=principal), field_nodes=(), fragments={}
    )
    return create_list_resolver(lambda *_: snapshot, manager)(None, info)["items"]


def test_fresh_principal_revocation_rechecks_cached_remote_rows():
    manager, snapshot, state = source(["isAdmin"])
    row = next(iter(snapshot))
    with patch.object(manager, "all", return_value=snapshot):
        assert len(query(manager, user(True))["items"]) == 1
        assert len(graphql_list(manager, snapshot, user(True))) == 1
        assert can_read_instance_for_user(row, user(True))
        assert query(manager, user(False))["items"] == []
        assert list(graphql_list(manager, snapshot, user(False))) == []
        assert not can_read_instance_for_user(row, user(False))
        request = RequestFactory().get("/gm/audit-remote/1")
        request.user = user(False)
        with pytest.raises(PermissionError):
            _serialize_readable_manager(row, request, require_read=True)
    assert state["calls"] == 1  # no extra remote fetch necessary for principal rule


@pytest.mark.parametrize(
    "transport", ["rest_list", "rest_detail", "graphql_list", "graphql_detail_gate"]
)
def test_builtin_fresh_requests_fetch_current_row_state(transport):
    from general_manager.api.remote_api import _build_item_view

    manager, _snapshot, state = source(["reloadCurrentRowAllowed"])
    manager.RemoteAPI.allow_detail = True

    def request():
        if transport == "rest_list":
            return bool(query(manager, user())["items"])
        if transport == "graphql_list":
            return bool(graphql_list(manager, manager.all(), user()))
        if transport == "graphql_detail_gate":
            return can_read_instance_for_user(manager(id=1), user())
        req = RequestFactory().get("/gm/audit-remote/1")
        req.user = user()
        response = _build_item_view(get_remote_api_config(manager))(req, "1")
        assert response.status_code in (200, 403), response.content
        return response.status_code == 200

    assert request()
    assert state["calls"] == 2
    state["allowed"] = False
    assert not request()
    assert state["calls"] == 3


def test_delegated_permission_reads_current_parent_on_each_request():
    parent_manager, _parent_snapshot, parent_state = source(["reloadCurrentRowAllowed"])
    child_manager, _child_snapshot, child_state = source(["public"])

    class DelegatedPermission(OverrideManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        __based_on__: ClassVar[str] = "parent"

    class CurrentParent:
        def __get__(self, instance, owner):
            return parent_manager if instance is None else parent_manager(id=1)

    child_manager.Permission = DelegatedPermission
    child_manager.parent = CurrentParent()
    assert len(query(child_manager, user())["items"]) == 1
    assert parent_state["calls"] > 1
    calls_before_revoke = parent_state["calls"]
    parent_state["allowed"] = False
    assert query(child_manager, user())["items"] == []
    assert parent_state["calls"] > calls_before_revoke
    assert child_state["calls"] == 3
    assert list(graphql_list(child_manager, child_manager.all(), user())) == []
    assert not can_read_instance_for_user(child_manager(id=1), user())
