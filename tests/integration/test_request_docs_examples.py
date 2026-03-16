from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from django.test import SimpleTestCase

from general_manager.interface import (
    BearerTokenAuthProvider,
    FieldMappingSerializer,
    RequestField,
    RequestFilter,
    RequestInterface,
    RequestMutationOperation,
    RequestQueryOperation,
    RequestRetryPolicy,
    RequestTransportConfig,
    UrllibRequestTransport,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta


class FakeExampleResponse:
    def __init__(
        self, *, status: int, payload: Any, headers: dict[str, str] | None = None
    ) -> None:
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    def read(self) -> bytes:
        import json

        return json.dumps(self._payload).encode("utf-8")


class RequestDocsExampleTests(SimpleTestCase):
    def test_request_cookbook_example_exists_and_mentions_builtin_transport_path(
        self,
    ) -> None:
        example_path = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "examples"
            / "request_interface_end_to_end.md"
        )

        self.assertTrue(example_path.exists())
        content = example_path.read_text()
        self.assertIn("UrllibRequestTransport", content)
        self.assertIn("BearerTokenAuthProvider", content)
        self.assertIn("FieldMappingSerializer", content)

    def test_documented_builtin_transport_example_is_executable(self) -> None:
        recorded_requests: list[dict[str, Any]] = []

        def fake_urlopen(
            request: Any, timeout: float | int | None = None
        ) -> FakeExampleResponse:
            recorded_requests.append(
                {
                    "url": request.full_url,
                    "method": request.get_method(),
                    "headers": dict(request.header_items()),
                    "body": request.data,
                    "timeout": timeout,
                }
            )
            if request.full_url.endswith("/projects/1"):
                return FakeExampleResponse(
                    status=200,
                    payload={
                        "id": 1,
                        "displayName": "Example Alpha",
                        "state": "active",
                        "modifiedAt": "2026-03-16T10:00:00",
                    },
                    headers={"x-request-id": "detail-1"},
                )
            if request.get_method() == "POST":
                return FakeExampleResponse(
                    status=201,
                    payload={
                        "id": 2,
                        "displayName": "Created Example",
                        "state": "active",
                        "modifiedAt": "2026-03-16T11:00:00",
                    },
                    headers={"x-request-id": "create-1"},
                )
            return FakeExampleResponse(
                status=200,
                payload=[
                    {
                        "id": 1,
                        "displayName": "Example Alpha",
                        "state": "active",
                        "modifiedAt": "2026-03-16T10:00:00",
                    }
                ],
                headers={"x-request-id": "list-1"},
            )

        class ExampleProject(GeneralManager):
            class Interface(RequestInterface):
                id = Input(type=int)

                name = RequestField(str, source="displayName")
                status = RequestField(str, source="state")
                updated_at = RequestField(datetime, source="modifiedAt")

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
                    transport = UrllibRequestTransport(urlopen=fake_urlopen)
                    transport_config = RequestTransportConfig(
                        base_url="https://docs.example.test/api",
                        timeout=5,
                    )
                    auth_provider = BearerTokenAuthProvider(token=lambda: "docs-token")
                    retry_policy = RequestRetryPolicy(max_attempts=2)
                    create_serializer = FieldMappingSerializer(
                        {"displayName": "name", "state": "status"}
                    )

        ExampleProject._attributes = ExampleProject.Interface.get_attributes()
        GeneralManagerMeta.create_at_properties_for_attributes(
            ExampleProject._attributes.keys(),
            ExampleProject,
        )

        bucket = ExampleProject.filter(status="active")
        project = next(iter(bucket))
        detail = ExampleProject(id=1)
        detail_name = detail.name
        created = ExampleProject.create(
            name="Created Example",
            status="active",
            ignore_permission=True,
        )

        self.assertEqual(project.name, "Example Alpha")
        self.assertEqual(project.id, 1)
        self.assertEqual(detail_name, "Example Alpha")
        self.assertEqual(detail.id, 1)
        self.assertEqual(created.id, 2)
        self.assertEqual(
            recorded_requests[0]["url"],
            "https://docs.example.test/api/projects?state=active",
        )
        self.assertEqual(
            recorded_requests[0]["headers"]["Authorization"], "Bearer docs-token"
        )
        self.assertEqual(
            recorded_requests[1]["url"], "https://docs.example.test/api/projects/1"
        )
        self.assertEqual(recorded_requests[2]["method"], "POST")
        self.assertEqual(recorded_requests[2]["timeout"], 5)
