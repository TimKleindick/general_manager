from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from general_manager.interface import RequestInterface
from general_manager.interface.requests import (
    RequestAuthenticationError,
    RequestAuthorizationError,
    RequestConflictError,
    RequestField,
    RequestFilter,
    RequestMutationOperation,
    RequestNotFoundError,
    RequestQueryOperation,
    RequestRateLimitedError,
    RequestRetryPolicy,
    RequestServerError,
    RequestTransportError,
    RequestTransportConfig,
    RequestTransportRequest,
    RequestTransportResponse,
    RequestTransportStatusError,
    SharedRequestTransport,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import AttributeEvaluationError, GeneralManagerMeta
from general_manager.rule import Rule


class FakeBearerAuth:
    def apply(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: type[Any],
        operation: RequestQueryOperation,
        plan: Any,
    ) -> RequestTransportRequest:
        headers = dict(request.headers)
        headers["Authorization"] = f"Bearer {interface_cls.Meta.api_token}"
        return RequestTransportRequest(
            method=request.method,
            url=request.url,
            path=request.path,
            query_params=request.query_params,
            headers=headers,
            body=request.body,
            timeout=request.timeout,
            operation_name=request.operation_name,
            metadata=request.metadata,
        )


class FakeSharedTransport(SharedRequestTransport):
    def __init__(self) -> None:
        self.requests: list[RequestTransportRequest] = []

    def send(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: type[Any],
        operation: RequestQueryOperation,
        plan: Any,
        identification: dict[str, Any] | None,
    ) -> RequestTransportResponse:
        self.requests.append(request)
        auth_header = request.headers.get("Authorization")
        if auth_header == "Bearer bad-token":
            raise RequestTransportStatusError(status_code=401, request=request)
        if auth_header == "Bearer forbidden-token":
            raise RequestTransportStatusError(status_code=403, request=request)
        if auth_header == "Bearer missing-token":
            raise RequestTransportStatusError(status_code=404, request=request)
        if auth_header == "Bearer conflict-token":
            raise RequestTransportStatusError(status_code=409, request=request)
        if auth_header == "Bearer limited-token":
            raise RequestTransportStatusError(status_code=429, request=request)
        if auth_header == "Bearer broken-token":
            raise RequestTransportStatusError(status_code=503, request=request)
        if auth_header == "Bearer timeout-token":
            raise TimeoutError

        if operation.name == "detail":
            project_id = identification["id"] if identification is not None else None
            return RequestTransportResponse(
                payload={
                    "id": project_id,
                    "name": "Detail Alpha",
                    "status": "active",
                    "updated_at": datetime(2026, 3, 13, 10, 0, 0),
                },
                status_code=200,
                headers={"x-request-id": "detail-123"},
            )

        if operation.name == "search":
            return RequestTransportResponse(
                payload=[
                    {
                        "id": 9,
                        "name": "Search Alpha",
                        "status": "active",
                        "updated_at": datetime(2026, 3, 13, 9, 0, 0),
                    }
                ],
                status_code=200,
                headers={"x-request-id": "search-123"},
            )

        if operation.name == "create":
            payload = dict(request.body or {})
            return RequestTransportResponse(
                payload={
                    "id": 13,
                    "name": payload["name"],
                    "status": payload["status"],
                    "updated_at": datetime(2026, 3, 13, 11, 0, 0),
                },
                status_code=201,
                headers={"x-request-id": "create-123"},
            )

        if operation.name == "update":
            payload = dict(request.body or {})
            return RequestTransportResponse(
                payload={
                    "id": identification["id"] if identification is not None else 42,
                    "name": "Updated Alpha",
                    "status": payload["status"],
                    "updated_at": datetime(2026, 3, 13, 12, 0, 0),
                },
                status_code=200,
                headers={"x-request-id": "update-123"},
            )

        return RequestTransportResponse(
            payload=[
                {
                    "id": 1,
                    "name": "Alpha",
                    "status": "active",
                    "updated_at": datetime(2026, 3, 13, 8, 0, 0),
                }
            ],
            status_code=200,
            headers={"x-request-id": "list-123"},
        )


class FakeFlakySharedTransport(SharedRequestTransport):
    def __init__(self) -> None:
        self.requests: list[RequestTransportRequest] = []

    def send(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: type[Any],
        operation: RequestQueryOperation,
        plan: Any,
        identification: dict[str, Any] | None,
    ) -> RequestTransportResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            raise RequestTransportStatusError(status_code=503, request=request)
        return RequestTransportResponse(
            payload=[
                {
                    "id": 7,
                    "name": "Retried Alpha",
                    "status": "active",
                    "updated_at": datetime(2026, 3, 14, 10, 0, 0),
                }
            ],
            status_code=200,
            headers={"x-request-id": "retry-123"},
        )


class FakeSerializedTransport(SharedRequestTransport):
    def __init__(self) -> None:
        self.requests: list[RequestTransportRequest] = []

    def send(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: type[Any],
        operation: RequestQueryOperation,
        plan: Any,
        identification: dict[str, Any] | None,
    ) -> RequestTransportResponse:
        self.requests.append(request)
        if operation.name == "detail":
            return RequestTransportResponse(
                payload={
                    "identifier": identification["id"]
                    if identification is not None
                    else 77,
                    "displayName": "Serialized Detail",
                    "currentState": "active",
                    "modifiedAt": datetime(2026, 3, 16, 10, 0, 0),
                },
                status_code=200,
            )
        if operation.name == "create":
            body = dict(request.body or {})
            return RequestTransportResponse(
                payload={
                    "identifier": 21,
                    "displayName": body["payloadName"],
                    "currentState": body["payloadStatus"],
                    "modifiedAt": datetime(2026, 3, 16, 11, 0, 0),
                },
                status_code=201,
            )
        if operation.name == "update":
            body = dict(request.body or {})
            return RequestTransportResponse(
                payload={
                    "identifier": identification["id"]
                    if identification is not None
                    else 21,
                    "displayName": "Serialized Detail",
                    "currentState": body["payloadStatus"],
                    "modifiedAt": datetime(2026, 3, 16, 12, 0, 0),
                },
                status_code=200,
            )
        return RequestTransportResponse(
            payload=[
                {
                    "identifier": 21,
                    "displayName": "Serialized Alpha",
                    "currentState": "active",
                    "modifiedAt": datetime(2026, 3, 16, 9, 0, 0),
                }
            ],
            status_code=200,
        )


def serialize_create_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "payloadName": payload["name"],
        "payloadStatus": payload["status"],
    }


def serialize_update_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "payloadStatus": payload["status"],
    }


def normalize_serialized_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload["identifier"],
        "name": payload["displayName"],
        "status": payload["currentState"],
        "updated_at": payload["modifiedAt"],
    }


class TransportBackedProject(GeneralManager):
    class Interface(RequestInterface):
        id = Input(type=int)

        name = RequestField(str)
        status = RequestField(str)
        updated_at = RequestField(datetime)

        class Meta:
            filters: ClassVar[dict[str, RequestFilter]] = {
                "status": RequestFilter(remote_name="state", value_type=str),
                "page": RequestFilter(remote_name="page", value_type=int),
            }
            query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                "detail": RequestQueryOperation(
                    name="detail",
                    method="GET",
                    path="/projects/{id}",
                    static_headers={"X-Service": "request-tests"},
                ),
                "list": RequestQueryOperation(
                    name="list",
                    method="GET",
                    path="/projects",
                    static_query_params={"includeArchived": "false"},
                    static_headers={"X-Service": "request-tests"},
                ),
                "search": RequestQueryOperation(
                    name="search",
                    method="POST",
                    path="/projects/search",
                    static_headers={"X-Service": "request-tests"},
                    static_body={"scope": "all"},
                    filters={
                        "query": RequestFilter(
                            remote_name="query",
                            location="body",
                            value_type=str,
                        )
                    },
                ),
            }
            create_operation = RequestMutationOperation(
                name="create",
                method="POST",
                path="/projects",
                static_headers={"X-Service": "request-tests"},
            )
            update_operation = RequestMutationOperation(
                name="update",
                method="PATCH",
                path="/projects/{id}",
                static_headers={"X-Service": "request-tests"},
            )
            api_token = "good-token"  # noqa: S105 - fake integration test token
            transport = FakeSharedTransport()
            transport_config = RequestTransportConfig(
                base_url="https://api.example.test",
                timeout=15,
            )
            auth_provider = FakeBearerAuth()


TransportBackedProject._attributes = TransportBackedProject.Interface.get_attributes()
GeneralManagerMeta.create_at_properties_for_attributes(
    TransportBackedProject._attributes.keys(),
    TransportBackedProject,
)


class RetryingTransportProject(GeneralManager):
    class Interface(RequestInterface):
        id = Input(type=int)

        name = RequestField(str)
        status = RequestField(str)
        updated_at = RequestField(datetime)

        class Meta:
            filters: ClassVar[dict[str, RequestFilter]] = {
                "status": RequestFilter(remote_name="state", value_type=str),
            }
            query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                "detail": RequestQueryOperation(
                    name="detail",
                    method="GET",
                    path="/projects/{id}",
                ),
                "list": RequestQueryOperation(
                    name="list",
                    method="GET",
                    path="/projects",
                ),
            }
            transport = FakeFlakySharedTransport()
            transport_config = RequestTransportConfig(
                base_url="https://api.example.test",
                timeout=15,
                retry_policy=RequestRetryPolicy(
                    max_attempts=2,
                    retryable_status_codes=frozenset({503}),
                    base_backoff_seconds=0,
                ),
            )


RetryingTransportProject._attributes = (
    RetryingTransportProject.Interface.get_attributes()
)
GeneralManagerMeta.create_at_properties_for_attributes(
    RetryingTransportProject._attributes.keys(),
    RetryingTransportProject,
)


class RuleProtectedProject(GeneralManager):
    class Interface(RequestInterface):
        id = Input(type=int)

        name = RequestField(str)
        status = RequestField(str)
        updated_at = RequestField(datetime)

        class Meta:
            filters: ClassVar[dict[str, RequestFilter]] = {
                "status": RequestFilter(remote_name="state", value_type=str),
            }
            query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                "detail": RequestQueryOperation(
                    name="detail",
                    method="GET",
                    path="/projects/{id}",
                ),
                "list": RequestQueryOperation(
                    name="list",
                    method="GET",
                    path="/projects",
                ),
            }
            create_operation = RequestMutationOperation(
                name="create",
                method="POST",
                path="/projects",
            )
            update_operation = RequestMutationOperation(
                name="update",
                method="PATCH",
                path="/projects/{id}",
            )
            transport = FakeSharedTransport()
            transport_config = RequestTransportConfig(
                base_url="https://api.example.test",
                timeout=15,
            )
            auth_provider = FakeBearerAuth()
            rules: ClassVar[list[Rule]] = [
                Rule(
                    lambda project: bool(project.name),
                    custom_error_message="Name is required: {name}",
                ),
                Rule(
                    lambda project: project.status in {"active", "inactive"},
                    custom_error_message="Invalid status: {status}",
                ),
            ]


RuleProtectedProject._attributes = RuleProtectedProject.Interface.get_attributes()
GeneralManagerMeta.create_at_properties_for_attributes(
    RuleProtectedProject._attributes.keys(),
    RuleProtectedProject,
)


class SerializedTransportProject(GeneralManager):
    class Interface(RequestInterface):
        id = Input(type=int)

        name = RequestField(str)
        status = RequestField(str)
        updated_at = RequestField(datetime)

        class Meta:
            filters: ClassVar[dict[str, RequestFilter]] = {
                "status": RequestFilter(remote_name="state", value_type=str),
            }
            query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                "detail": RequestQueryOperation(
                    name="detail",
                    method="GET",
                    path="/serialized/{id}",
                ),
                "list": RequestQueryOperation(
                    name="list",
                    method="GET",
                    path="/serialized",
                ),
            }
            create_operation = RequestMutationOperation(
                name="create",
                method="POST",
                path="/serialized",
            )
            update_operation = RequestMutationOperation(
                name="update",
                method="PATCH",
                path="/serialized/{id}",
            )
            transport = FakeSerializedTransport()
            transport_config = RequestTransportConfig(
                base_url="https://api.example.test",
                timeout=15,
            )
            create_serializer = serialize_create_payload
            update_serializer = serialize_update_payload
            response_serializer = normalize_serialized_payload


SerializedTransportProject._attributes = (
    SerializedTransportProject.Interface.get_attributes()
)
GeneralManagerMeta.create_at_properties_for_attributes(
    SerializedTransportProject._attributes.keys(),
    SerializedTransportProject,
)


class RequestTransportIntegrationTest(SimpleTestCase):
    def setUp(self) -> None:
        TransportBackedProject.Interface.transport.requests.clear()
        TransportBackedProject.Interface.Meta.api_token = "good-token"  # noqa: S105
        RetryingTransportProject.Interface.transport.requests.clear()
        RuleProtectedProject.Interface.transport.requests.clear()
        RuleProtectedProject.Interface.Meta.api_token = "good-token"  # noqa: S105
        SerializedTransportProject.Interface.transport.requests.clear()

    def test_transport_executes_filter_and_lazy_detail_reads(self) -> None:
        bucket = TransportBackedProject.filter(status="active", page=2)

        items = list(bucket)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "Alpha")
        list_request = TransportBackedProject.Interface.transport.requests[-1]
        self.assertEqual(list_request.url, "https://api.example.test/projects")
        self.assertEqual(
            dict(list_request.query_params),
            {"includeArchived": "false", "state": "active", "page": 2},
        )
        self.assertEqual(list_request.headers["X-Service"], "request-tests")
        self.assertEqual(list_request.headers["Authorization"], "Bearer good-token")
        self.assertEqual(list_request.timeout, 15)

        project = TransportBackedProject(id=42)

        self.assertEqual(project.name, "Detail Alpha")
        detail_request = TransportBackedProject.Interface.transport.requests[-1]
        self.assertEqual(detail_request.url, "https://api.example.test/projects/42")
        self.assertEqual(detail_request.headers["Authorization"], "Bearer good-token")

    def test_transport_executes_named_search_with_body(self) -> None:
        items = list(
            TransportBackedProject.Interface.query_operation("search", query="alpha")
        )

        self.assertEqual(len(items), 1)
        request = TransportBackedProject.Interface.transport.requests[-1]
        self.assertEqual(request.url, "https://api.example.test/projects/search")
        self.assertEqual(dict(request.body or {}), {"scope": "all", "query": "alpha"})
        self.assertEqual(request.headers["Authorization"], "Bearer good-token")

    def test_transport_executes_create_operation(self) -> None:
        project = TransportBackedProject.create(
            name="Created Alpha",
            status="active",
            ignore_permission=True,
        )

        self.assertEqual(project.identification, {"id": 13})
        request = TransportBackedProject.Interface.transport.requests[-1]
        self.assertEqual(request.url, "https://api.example.test/projects")
        self.assertEqual(
            dict(request.body or {}), {"name": "Created Alpha", "status": "active"}
        )
        self.assertEqual(request.headers["Authorization"], "Bearer good-token")

    def test_transport_executes_update_operation(self) -> None:
        project = TransportBackedProject(id=42).update(
            status="inactive",
            ignore_permission=True,
        )

        self.assertEqual(project.identification, {"id": 42})
        request = TransportBackedProject.Interface.transport.requests[-1]
        self.assertEqual(request.url, "https://api.example.test/projects/42")
        self.assertEqual(dict(request.body or {}), {"status": "inactive"})
        self.assertEqual(request.headers["Authorization"], "Bearer good-token")

    def test_request_rules_block_invalid_create_mutations(self) -> None:
        with self.assertRaises(ValidationError):
            RuleProtectedProject.create(
                name="",
                status="active",
                ignore_permission=True,
            )

        self.assertEqual(RuleProtectedProject.Interface.transport.requests, [])

    def test_request_rules_block_invalid_update_mutations(self) -> None:
        with self.assertRaises(ValidationError):
            RuleProtectedProject(id=42).update(
                status="archived",
                ignore_permission=True,
            )

        self.assertEqual(RuleProtectedProject.Interface.transport.requests, [])

    def test_request_rules_do_not_run_for_queries(self) -> None:
        items = list(RuleProtectedProject.filter(status="active"))

        self.assertEqual(len(items), 1)

    def test_create_serializer_shapes_outbound_mutation_payload(self) -> None:
        project = SerializedTransportProject.create(
            name="Serialized Alpha",
            status="active",
            ignore_permission=True,
        )

        self.assertEqual(project.identification, {"id": 21})
        request = SerializedTransportProject.Interface.transport.requests[-1]
        self.assertEqual(
            dict(request.body or {}),
            {"payloadName": "Serialized Alpha", "payloadStatus": "active"},
        )

    def test_update_serializer_shapes_outbound_mutation_payload(self) -> None:
        project = SerializedTransportProject(id=21).update(
            status="inactive",
            ignore_permission=True,
        )

        self.assertEqual(project.identification, {"id": 21})
        request = SerializedTransportProject.Interface.transport.requests[-1]
        self.assertEqual(dict(request.body or {}), {"payloadStatus": "inactive"})

    def test_response_serializer_normalizes_query_and_detail_payloads(self) -> None:
        item = SerializedTransportProject.filter(status="active").first()

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.name, "Serialized Alpha")
        self.assertEqual(SerializedTransportProject(id=21).name, "Serialized Detail")

    def test_transport_status_error_maps_to_authentication_error(self) -> None:
        TransportBackedProject.Interface.Meta.api_token = "bad-token"  # noqa: S105

        with self.assertRaises(RequestAuthenticationError):
            list(TransportBackedProject.filter(status="active"))

    def test_transport_status_errors_map_to_stable_remote_exceptions(self) -> None:
        cases = [
            ("forbidden-token", RequestAuthorizationError),
            ("missing-token", RequestNotFoundError),
            ("conflict-token", RequestConflictError),
            ("limited-token", RequestRateLimitedError),
            ("broken-token", RequestServerError),
        ]

        for token, expected_error in cases:
            with self.subTest(token=token):
                TransportBackedProject.Interface.Meta.api_token = token
                with self.assertRaises(expected_error):
                    list(TransportBackedProject.filter(status="active"))

    def test_transport_runtime_failures_map_to_transport_error(self) -> None:
        TransportBackedProject.Interface.Meta.api_token = "timeout-token"  # noqa: S105

        with self.assertRaises(RequestTransportError):
            list(TransportBackedProject.filter(status="active"))

    def test_descriptor_wraps_transport_errors_with_cause(self) -> None:
        TransportBackedProject.Interface.Meta.api_token = "bad-token"  # noqa: S105

        with self.assertRaises(AttributeEvaluationError) as error:
            _ = TransportBackedProject(id=1).name

        self.assertIsInstance(error.exception.__cause__, RequestAuthenticationError)

    def test_transport_observability_logs_sanitized_request_metadata(self) -> None:
        fake_logger = mock.MagicMock()
        handler = TransportBackedProject.Interface.get_capability_handler(
            "observability"
        )
        handler._logger = fake_logger  # type: ignore[attr-defined]

        list(TransportBackedProject.filter(status="active"))
        _ = TransportBackedProject(id=42).name

        end_contexts = [
            call.kwargs["context"]
            for call in fake_logger.debug.call_args_list
            if call.args and call.args[0] == "interface operation end"
        ]
        self.assertTrue(end_contexts)

        query_context = next(
            context
            for context in end_contexts
            if context["operation"] == "request.query.execute"
        )
        self.assertEqual(query_context["service"], "TransportBackedProject")
        self.assertEqual(query_context["method"], "GET")
        self.assertEqual(query_context["path"], "/projects")
        self.assertEqual(query_context["status_code"], 200)
        self.assertEqual(query_context["retry_count"], 0)
        self.assertEqual(query_context["request_id"], "list-123")
        self.assertNotIn("Authorization", repr(query_context))
        self.assertNotIn("Bearer good-token", repr(query_context))
        self.assertNotIn("active", repr(query_context))

        detail_context = next(
            context
            for context in end_contexts
            if context["operation"] == "request.read.detail"
        )
        self.assertEqual(detail_context["request_id"], "detail-123")
        self.assertEqual(detail_context["path"], "/projects/{id}")

    def test_transport_observability_logs_error_class_and_status(self) -> None:
        fake_logger = mock.MagicMock()
        handler = TransportBackedProject.Interface.get_capability_handler(
            "observability"
        )
        handler._logger = fake_logger  # type: ignore[attr-defined]
        TransportBackedProject.Interface.Meta.api_token = "bad-token"  # noqa: S105

        with self.assertRaises(RequestAuthenticationError):
            list(TransportBackedProject.filter(status="active"))

        error_context = fake_logger.error.call_args.kwargs["context"]
        self.assertEqual(error_context["operation"], "request.query.execute")
        self.assertEqual(error_context["error_class"], "RequestAuthenticationError")
        self.assertEqual(error_context["status_code"], 401)
        self.assertEqual(error_context["method"], "GET")
        self.assertEqual(error_context["path"], "/projects")
        self.assertNotIn("Bearer bad-token", repr(error_context))

    def test_framework_retry_policy_retries_transient_get_failures(self) -> None:
        fake_logger = mock.MagicMock()
        handler = RetryingTransportProject.Interface.get_capability_handler(
            "observability"
        )
        handler._logger = fake_logger  # type: ignore[attr-defined]

        items = list(RetryingTransportProject.filter(status="active"))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "Retried Alpha")
        self.assertEqual(len(RetryingTransportProject.Interface.transport.requests), 2)
        first_request, second_request = (
            RetryingTransportProject.Interface.transport.requests
        )
        self.assertEqual(first_request.url, second_request.url)
        self.assertEqual(first_request.query_params, second_request.query_params)
        self.assertEqual(dict(second_request.query_params), {"state": "active"})

        end_context = next(
            call.kwargs["context"]
            for call in fake_logger.debug.call_args_list
            if call.args
            and call.args[0] == "interface operation end"
            and call.kwargs["context"]["operation"] == "request.query.execute"
        )
        self.assertEqual(end_context["status_code"], 200)
        self.assertEqual(end_context["retry_count"], 1)
