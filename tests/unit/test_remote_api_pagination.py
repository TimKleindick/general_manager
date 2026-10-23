"""Finite server-owned RemoteAPI pages, including upstream page provenance."""

import json
from typing import ClassVar
from unittest.mock import Mock, patch

import pytest
from django.test import RequestFactory, override_settings

from general_manager.api.remote_api import (
    _build_query_view,
    _resolve_remote_pagination,
    get_remote_api_config,
)
from general_manager.interface import RemoteManagerInterface, RequestInterface
from general_manager.interface.requests import (
    RequestField,
    RequestQueryOperation,
    RequestQueryPlan,
    RequestQueryResult,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta


@pytest.fixture(autouse=True)
def isolate_pagination_manager_registries(monkeypatch):
    """Keep per-test manager declarations out of later application startup."""
    for name in (
        "all_classes",
        "read_only_classes",
        "pending_graphql_interfaces",
        "pending_attribute_initialization",
    ):
        monkeypatch.setattr(
            GeneralManagerMeta, name, list(getattr(GeneralManagerMeta, name))
        )


def _query(manager, data):
    config = get_remote_api_config(manager)
    assert config is not None
    with patch(
        "general_manager.api.remote_api._serialize_manager",
        side_effect=lambda item, _request: {"id": item.id, "name": item.name},
    ):
        response = _build_query_view(config)(
            RequestFactory().post(
                "/gm/pages/query", data=data, content_type="application/json"
            )
        )
    return response.status_code, json.loads(response.content)


def _complete_manager():
    class CompletePages(GeneralManager):
        class Interface(RequestInterface):
            id = Input(int)
            name = RequestField(str)

            class Meta:
                query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                    "list": RequestQueryOperation(
                        name="list", method="GET", path="/pages"
                    ),
                    "detail": RequestQueryOperation(
                        name="detail", method="GET", path="/pages/{id}"
                    ),
                }

            @classmethod
            def execute_request_plan(cls, plan):
                return RequestQueryResult(
                    items=tuple(
                        {"id": index, "name": str(index)} for index in range(1, 22)
                    ),
                    total_count=21,
                )

        class RemoteAPI:
            enabled = True
            resource_name = "pages"
            allow_filter = True

    return CompletePages


def test_omitted_pagination_returns_finite_first_page_and_full_count():
    status, payload = _query(_complete_manager(), {})
    assert status == 200
    assert [item["id"] for item in payload["items"]] == list(range(1, 11))
    assert payload["total_count"] == 21
    assert payload["metadata"]["page"] == 1
    assert payload["metadata"]["page_size"] == 10


@override_settings(
    GENERAL_MANAGER={"REMOTE_API_DEFAULT_PAGE_SIZE": 3, "REMOTE_API_MAX_PAGE_SIZE": 4}
)
def test_server_page_configuration_and_last_page_metadata():
    status, payload = _query(_complete_manager(), {"page": 7})
    assert status == 200
    assert [item["id"] for item in payload["items"]] == [19, 20, 21]
    assert payload["metadata"]["page_size"] == 3
    assert payload["total_count"] == 21
    status, payload = _query(_complete_manager(), {"page_size": 5})
    assert status == 400
    assert payload["error_code"] == "invalid_request"


@pytest.mark.parametrize("size", [101, 1000000, True, 0, -1, "10"])
def test_invalid_or_oversized_page_rejected_before_remote_execution(size):
    manager = _complete_manager()

    manager.all = Mock(side_effect=AssertionError("query must not execute"))
    status, payload = _query(manager, {"page_size": size})
    assert status == 400
    assert payload["error_code"] == "invalid_request"


@pytest.mark.parametrize(
    "config",
    [
        {"REMOTE_API_DEFAULT_PAGE_SIZE": 0},
        {"REMOTE_API_MAX_PAGE_SIZE": True},
        {"REMOTE_API_DEFAULT_PAGE_SIZE": 11, "REMOTE_API_MAX_PAGE_SIZE": 10},
    ],
)
def test_invalid_server_limits_fail_closed(config):
    with override_settings(GENERAL_MANAGER=config), pytest.raises(ValueError):
        _resolve_remote_pagination(None, None)


def test_default_controls_forwarded_and_upstream_page_cannot_expand_output():
    plans: list[RequestQueryPlan] = []

    class UpstreamPages(GeneralManager):
        class Interface(RemoteManagerInterface):
            id = Input(int)
            name = RequestField(str)

            class Meta:
                base_url = "https://remote.example.test"
                remote_manager = "upstream-pages"

            @classmethod
            def execute_request_plan(cls, plan):
                plans.append(plan)
                # Upstream claims a matching page but returns too many rows.
                return RequestQueryResult(
                    items=tuple(
                        {"id": index, "name": str(index)} for index in range(1, 22)
                    ),
                    total_count=100,
                    page=1,
                    page_size=10,
                )

        class RemoteAPI:
            enabled = True
            resource_name = "upstream-pages"
            allow_filter = True

    status, payload = _query(UpstreamPages, {})
    assert status == 200
    assert len(plans) == 1
    assert plans[0].body["page"] == 1
    assert plans[0].body["page_size"] == 10
    assert len(payload["items"]) == 10
    assert payload["total_count"] == 100


@pytest.mark.parametrize(
    "controls, expected_ids",
    [
        ({}, list(range(10))),
        ({"page": 2, "page_size": 3, "ordering": "-id"}, [17, 16, 15]),
    ],
)
def test_materialized_remote_bucket_still_pages_locally(
    monkeypatch, controls, expected_ids
):
    class SnapshotPages(GeneralManager):
        class Interface(RemoteManagerInterface):
            id = Input(int)
            name = RequestField(str)

            class Meta:
                base_url = "https://remote.example.test"
                remote_manager = "snapshot-pages"

            @classmethod
            def execute_request_plan(cls, plan):
                return RequestQueryResult(
                    items=tuple({"id": i, "name": str(i)} for i in range(21)),
                    total_count=21,
                )

        class RemoteAPI:
            enabled = True
            resource_name = "snapshot-pages"
            allow_filter = True

    source = SnapshotPages.all()
    snapshot = source.with_instances(list(source))
    assert snapshot.request_plan is None
    monkeypatch.setattr(SnapshotPages, "all", lambda: snapshot)
    status, payload = _query(SnapshotPages, controls)
    assert status == 200
    assert [item["id"] for item in payload["items"]] == expected_ids, payload
    assert payload["total_count"] == 21
