from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from urllib.error import HTTPError

import pytest

from general_manager.interface.requests import (
    BasicAuthProvider,
    BearerTokenAuthProvider,
    FieldMappingSerializer,
    HeaderApiKeyAuthProvider,
    QueryApiKeyAuthProvider,
    RequestQueryOperation,
    RequestQueryPlan,
    RequestRetryPolicy,
    RequestSchemaError,
    RequestServerError,
    RequestTransportConfig,
    RequestTransportRequest,
    RequestTransportResponse,
    RequestTransportStatusError,
    SharedRequestTransport,
    UrllibRequestTransport,
    default_request_response_normalizer,
)


class DummyInterface:
    __name__ = "DummyInterface"
    auth_provider = None
    transport_config = RequestTransportConfig(base_url="https://service.example.test")


class FakeUrlopenResponse:
    def __init__(
        self,
        *,
        status: int,
        payload: Any,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class RecordingMetricsBackend:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []

    def record_request(
        self,
        *,
        service: str,
        operation: str,
        method: str,
        status_code: int,
        outcome: str,
        duration: float,
        retry_count: int,
    ) -> None:
        self.requests.append(
            {
                "service": service,
                "operation": operation,
                "method": method,
                "status_code": status_code,
                "outcome": outcome,
                "duration": duration,
                "retry_count": retry_count,
            }
        )

    def record_error(
        self,
        *,
        service: str,
        operation: str,
        method: str,
        error_class: str,
        status_code: int | None,
        retry_count: int,
    ) -> None:
        self.errors.append(
            {
                "service": service,
                "operation": operation,
                "method": method,
                "error_class": error_class,
                "status_code": status_code,
                "retry_count": retry_count,
            }
        )


class RecordingTraceBackend:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.completed: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []

    def on_request_start(self, **kwargs: Any) -> object:
        self.started.append(kwargs)
        return {"trace_id": "trace-1"}

    def on_request_end(self, *, trace_context: object, **kwargs: Any) -> None:
        self.completed.append({"trace_context": trace_context, **kwargs})

    def on_request_error(
        self,
        *,
        trace_context: object,
        error: Exception,
        **kwargs: Any,
    ) -> None:
        self.failed.append({"trace_context": trace_context, "error": error, **kwargs})


def test_urllib_request_transport_executes_json_request_and_response() -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(
        request: Any, timeout: float | int | None = None
    ) -> FakeUrlopenResponse:
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeUrlopenResponse(
            status=200,
            payload=[{"id": 1, "name": "Alpha"}],
            headers={"x-request-id": "req-123"},
        )

    transport = UrllibRequestTransport(urlopen=fake_urlopen)
    plan = RequestQueryPlan(
        operation_name="search",
        action="filter",
        method="POST",
        path="/projects/search",
        query_params={"page": 2},
        headers={"X-Test": "yes"},
        body={"query": "alpha"},
    )
    operation = RequestQueryOperation(
        name="search", method="POST", path="/projects/search"
    )

    result = transport.execute(
        interface_cls=type(
            "SearchInterface",
            (),
            {
                "transport_config": RequestTransportConfig(
                    base_url="https://service.example.test/api",
                ),
                "auth_provider": None,
                "__name__": "SearchInterface",
            },
        ),
        operation=operation,
        plan=plan,
    )

    assert captured["url"] == "https://service.example.test/api/projects/search?page=2"
    assert captured["method"] == "POST"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["headers"]["X-test"] == "yes"
    assert json.loads(captured["data"].decode("utf-8")) == {"query": "alpha"}
    assert captured["timeout"] == 10
    assert result.items == ({"id": 1, "name": "Alpha"},)
    assert result.metadata["status_code"] == 200
    assert result.metadata["request_id"] == "req-123"


def test_builtin_auth_providers_apply_expected_request_mutations() -> None:
    request = RequestTransportRequest(
        method="GET",
        url="https://service.example.test/projects",
        path="/projects",
        query_params={"page": 1},
    )
    operation = RequestQueryOperation(name="list", method="GET", path="/projects")
    plan = RequestQueryPlan(
        operation_name="list",
        action="filter",
        method="GET",
        path="/projects",
    )

    bearer = BearerTokenAuthProvider(token="secret")  # noqa: S106 - test credential
    header_key = HeaderApiKeyAuthProvider(header_name="X-Api-Key", api_key="key-1")
    query_key = QueryApiKeyAuthProvider(param_name="api_key", api_key="key-2")
    basic = BasicAuthProvider(
        username="user",
        password="pass",  # noqa: S106 - test credential
    )

    assert (
        bearer.apply(
            request, interface_cls=DummyInterface, operation=operation, plan=plan
        ).headers["Authorization"]
        == "Bearer secret"
    )
    assert (
        header_key.apply(
            request, interface_cls=DummyInterface, operation=operation, plan=plan
        ).headers["X-Api-Key"]
        == "key-1"
    )
    assert (
        query_key.apply(
            request, interface_cls=DummyInterface, operation=operation, plan=plan
        ).query_params["api_key"]
        == "key-2"
    )
    assert (
        basic.apply(
            request, interface_cls=DummyInterface, operation=operation, plan=plan
        )
        .headers["Authorization"]
        .startswith("Basic ")
    )


def test_meta_retry_policy_overrides_transport_config_retry_policy() -> None:
    attempts = 0

    class RetryAwareTransport(SharedRequestTransport):
        def send(
            self,
            request: RequestTransportRequest,
            *,
            interface_cls: type[Any],
            operation: Any,
            plan: Any,
            identification: dict[str, Any] | None,
        ) -> RequestTransportResponse:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RequestTransportStatusError(status_code=503, request=request)
            return RequestTransportResponse(payload={"id": 1}, status_code=200)

    interface_cls = type(
        "MetaRetryInterface",
        (),
        {
            "transport_config": RequestTransportConfig(
                base_url="https://service.example.test",
                retry_policy=RequestRetryPolicy(max_attempts=1),
            ),
            "retry_policy": RequestRetryPolicy(
                max_attempts=2,
                retryable_status_codes=frozenset({503}),
            ),
            "auth_provider": None,
            "__name__": "MetaRetryInterface",
        },
    )

    result = RetryAwareTransport().execute(
        interface_cls=interface_cls,
        operation=RequestQueryOperation(name="list", method="GET", path="/projects"),
        plan=RequestQueryPlan(
            operation_name="list",
            action="filter",
            method="GET",
            path="/projects",
        ),
    )

    assert attempts == 2
    assert result.metadata["retry_count"] == 1


def test_retry_policy_supports_capped_jittered_backoff() -> None:
    policy = RequestRetryPolicy(
        max_attempts=3,
        base_backoff_seconds=1.0,
        backoff_multiplier=2.0,
        max_backoff_seconds=2.5,
        jitter_ratio=0.5,
    )

    backoff = policy.compute_backoff_seconds(retry_count=3, random_factor=0.5)

    assert backoff == pytest.approx(2.5)


def test_retry_policy_applies_idempotency_key_on_retried_non_idempotent_methods() -> (
    None
):
    seen_headers: list[dict[str, Any]] = []

    class FlakyMutationTransport(SharedRequestTransport):
        def send(
            self,
            request: RequestTransportRequest,
            *,
            interface_cls: type[Any],
            operation: Any,
            plan: Any,
            identification: dict[str, Any] | None,
        ) -> RequestTransportResponse:
            seen_headers.append(dict(request.headers))
            if len(seen_headers) == 1:
                raise RequestTransportStatusError(status_code=503, request=request)
            return RequestTransportResponse(payload={"id": 1}, status_code=200)

    interface_cls = type(
        "MutationInterface",
        (),
        {
            "transport_config": RequestTransportConfig(
                base_url="https://service.example.test",
                retry_policy=RequestRetryPolicy(
                    max_attempts=2,
                    retryable_status_codes=frozenset({503}),
                    retry_non_idempotent_methods=True,
                    idempotency_key_header="Idempotency-Key",
                    idempotency_key_factory=lambda: "idem-123",
                ),
            ),
            "auth_provider": None,
            "__name__": "MutationInterface",
        },
    )
    result = FlakyMutationTransport().execute(
        interface_cls=interface_cls,
        operation=RequestQueryOperation(name="create", method="POST", path="/projects"),
        plan=RequestQueryPlan(
            operation_name="create",
            action="create",
            method="POST",
            path="/projects",
            body={"name": "Alpha"},
        ),
    )

    assert result.items == ({"id": 1},)
    assert seen_headers[0]["Idempotency-Key"] == "idem-123"
    assert seen_headers[1]["Idempotency-Key"] == "idem-123"
    assert result.metadata["retry_count"] == 1


def test_urllib_request_transport_maps_http_status_errors() -> None:
    def failing_urlopen(
        request: Any, timeout: float | int | None = None
    ) -> FakeUrlopenResponse:
        raise HTTPError(
            url=request.full_url,
            code=503,
            msg="Service Unavailable",
            hdrs={"x-request-id": "failed-1"},
            fp=BytesIO(b'{"detail": "retry later"}'),
        )

    transport = UrllibRequestTransport(urlopen=failing_urlopen)
    interface_cls = type(
        "FailingInterface",
        (),
        {
            "transport_config": RequestTransportConfig(
                base_url="https://service.example.test"
            ),
            "auth_provider": None,
            "__name__": "FailingInterface",
        },
    )

    with pytest.raises(RequestServerError) as error_info:
        transport.execute(
            interface_cls=interface_cls,
            operation=RequestQueryOperation(
                name="list", method="GET", path="/projects"
            ),
            plan=RequestQueryPlan(
                operation_name="list",
                action="filter",
                method="GET",
                path="/projects",
            ),
        )

    assert error_info.value.status_code == 503
    assert error_info.value.headers["x-request-id"] == "failed-1"


def test_urllib_request_transport_uses_operation_timeout_override() -> None:
    captured_timeout: list[float | int | None] = []

    def fake_urlopen(
        request: Any, timeout: float | int | None = None
    ) -> FakeUrlopenResponse:
        captured_timeout.append(timeout)
        return FakeUrlopenResponse(status=200, payload={"id": 1})

    transport = UrllibRequestTransport(urlopen=fake_urlopen)
    transport.execute(
        interface_cls=type(
            "TimeoutInterface",
            (),
            {
                "transport_config": RequestTransportConfig(
                    base_url="https://service.example.test",
                    timeout=30,
                ),
                "auth_provider": None,
                "__name__": "TimeoutInterface",
            },
        ),
        operation=RequestQueryOperation(
            name="detail",
            method="GET",
            path="/projects/{id}",
            timeout=5,
        ),
        plan=RequestQueryPlan(
            operation_name="detail",
            action="detail",
            method="GET",
            path="/projects/{id}",
            path_params={"id": 1},
        ),
    )

    assert captured_timeout == [5]


def test_urllib_request_transport_preserves_request_schema_errors() -> None:
    def fake_urlopen(
        request: Any, timeout: float | int | None = None
    ) -> FakeUrlopenResponse:
        del request, timeout
        return FakeUrlopenResponse(status=200, payload=["not-a-mapping"])

    transport = UrllibRequestTransport(urlopen=fake_urlopen)

    with pytest.raises(RequestSchemaError):
        transport.execute(
            interface_cls=type(
                "SchemaInterface",
                (),
                {
                    "transport_config": RequestTransportConfig(
                        base_url="https://service.example.test"
                    ),
                    "auth_provider": None,
                    "__name__": "SchemaInterface",
                },
            ),
            operation=RequestQueryOperation(
                name="list",
                method="GET",
                path="/projects",
            ),
            plan=RequestQueryPlan(
                operation_name="list",
                action="filter",
                method="GET",
                path="/projects",
            ),
        )


def test_urllib_request_transport_percent_encodes_path_parameters() -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(
        request: Any, timeout: float | int | None = None
    ) -> FakeUrlopenResponse:
        del timeout
        captured["url"] = request.full_url
        return FakeUrlopenResponse(status=200, payload={"id": "folder/item"})

    transport = UrllibRequestTransport(urlopen=fake_urlopen)
    result = transport.execute(
        interface_cls=type(
            "PathInterface",
            (),
            {
                "transport_config": RequestTransportConfig(
                    base_url="https://service.example.test/api"
                ),
                "auth_provider": None,
                "__name__": "PathInterface",
            },
        ),
        operation=RequestQueryOperation(
            name="detail",
            method="GET",
            path="/projects/{id}",
        ),
        plan=RequestQueryPlan(
            operation_name="detail",
            action="detail",
            method="GET",
            path="/projects/{id}",
            path_params={"id": "folder/item"},
        ),
    )

    assert captured["url"] == "https://service.example.test/api/projects/folder%2Fitem"
    assert result.items == ({"id": "folder/item"},)


def test_shared_transport_emits_metrics_and_trace_hooks() -> None:
    metrics = RecordingMetricsBackend()
    trace = RecordingTraceBackend()

    class SuccessfulTransport(SharedRequestTransport):
        def send(
            self,
            request: RequestTransportRequest,
            *,
            interface_cls: type[Any],
            operation: Any,
            plan: Any,
            identification: dict[str, Any] | None,
        ) -> RequestTransportResponse:
            return RequestTransportResponse(
                payload=[{"id": 1}],
                status_code=200,
                headers={"x-request-id": "trace-req"},
            )

    interface_cls = type(
        "ObservedInterface",
        (),
        {
            "transport_config": RequestTransportConfig(
                base_url="https://service.example.test",
                metrics_backend=metrics,
                trace_backend=trace,
            ),
            "auth_provider": None,
            "__name__": "ObservedInterface",
        },
    )
    SuccessfulTransport().execute(
        interface_cls=interface_cls,
        operation=RequestQueryOperation(name="list", method="GET", path="/projects"),
        plan=RequestQueryPlan(
            operation_name="list",
            action="filter",
            method="GET",
            path="/projects",
        ),
    )

    assert metrics.requests[0]["operation"] == "list"
    assert metrics.requests[0]["status_code"] == 200
    assert trace.started[0]["operation"] == "list"
    assert trace.completed[0]["request_id"] == "trace-req"


def test_shared_transport_emits_error_metrics_and_trace_hooks() -> None:
    metrics = RecordingMetricsBackend()
    trace = RecordingTraceBackend()

    class FailingTransport(SharedRequestTransport):
        def send(
            self,
            request: RequestTransportRequest,
            *,
            interface_cls: type[Any],
            operation: Any,
            plan: Any,
            identification: dict[str, Any] | None,
        ) -> RequestTransportResponse:
            raise RequestTransportStatusError(status_code=503, request=request)

    interface_cls = type(
        "ObservedFailureInterface",
        (),
        {
            "transport_config": RequestTransportConfig(
                base_url="https://service.example.test",
                metrics_backend=metrics,
                trace_backend=trace,
            ),
            "auth_provider": None,
            "__name__": "ObservedFailureInterface",
        },
    )

    with pytest.raises(RequestServerError):
        FailingTransport().execute(
            interface_cls=interface_cls,
            operation=RequestQueryOperation(
                name="list", method="GET", path="/projects"
            ),
            plan=RequestQueryPlan(
                operation_name="list",
                action="filter",
                method="GET",
                path="/projects",
            ),
        )

    assert metrics.errors[0]["error_class"] == "RequestServerError"
    assert trace.failed[0]["status_code"] == 503


def test_default_normalizer_rejects_malformed_payload_items() -> None:
    with pytest.raises(RequestSchemaError):
        default_request_response_normalizer(
            RequestTransportResponse(payload=["not-a-mapping"], status_code=200),  # type: ignore[list-item]
            DummyInterface,
            RequestQueryOperation(name="list", method="GET", path="/projects"),
            RequestQueryPlan(
                operation_name="list",
                action="filter",
                method="GET",
                path="/projects",
            ),
        )


def test_field_mapping_serializer_maps_declared_keys() -> None:
    serializer = FieldMappingSerializer(
        {
            "payloadName": "name",
            "payloadStatus": "status",
        }
    )

    assert serializer({"name": "Alpha", "status": "active"}) == {
        "payloadName": "Alpha",
        "payloadStatus": "active",
    }
