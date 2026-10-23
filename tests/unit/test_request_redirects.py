from __future__ import annotations

from email.message import Message
from io import BytesIO
from typing import Any
from urllib.request import HTTPHandler, HTTPSHandler, Request as UrlRequest
from urllib.response import addinfourl

import pytest

from general_manager.interface.requests import (
    BasicAuthProvider,
    BearerTokenAuthProvider,
    HeaderApiKeyAuthProvider,
    RequestAuthProvider,
    RequestQueryOperation,
    RequestQueryPlan,
    RequestRetryPolicy,
    RequestTransportConfig,
    RequestTransportError,
    UrllibRequestTransport,
)


def _record_http_requests(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_code: int,
    location: str | None = None,
) -> list[UrlRequest]:
    """Replace network I/O while retaining urllib's real response handlers."""
    requests: list[UrlRequest] = []

    def open_http(handler: object, request: UrlRequest) -> Any:
        requests.append(request)
        headers = Message()
        code = status_code if len(requests) == 1 else 200
        if code != 200:
            headers["Location"] = location or "/redirected"
            headers["X-Request-ID"] = "redirect-1"
            payload = b"<html>Redirect</html>"
        else:
            payload = b'{"id": 1}'
        response = addinfourl(BytesIO(payload), headers, request.full_url, code)
        response.msg = "Test response"
        return response

    monkeypatch.setattr(HTTPHandler, "http_open", open_http)
    monkeypatch.setattr(HTTPSHandler, "https_open", open_http)
    return requests


@pytest.mark.parametrize("status_code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    "location",
    [
        "https://attacker.example.test/collect",
        "//attacker.example.test/collect",
        "http://service.example.test/downgrade",
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "http://10.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "/same-origin",
        pytest.param("https://[invalid", id="malformed-ipv6"),
        pytest.param("https://example.test\uff0fpath", id="malformed-netloc"),
    ],
)
@pytest.mark.parametrize(
    ("auth_provider", "expected_headers"),
    [
        (None, {}),
        (
            BearerTokenAuthProvider("test-token"),
            {"Authorization": "Bearer test-token"},
        ),
        (
            BasicAuthProvider("user", "pass"),
            {"Authorization": "Basic dXNlcjpwYXNz"},
        ),
        (
            HeaderApiKeyAuthProvider("X-Custom-Secret", "test-key"),
            {"X-custom-secret": "test-key"},
        ),
    ],
)
def test_default_urllib_transport_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    method: str,
    location: str,
    auth_provider: RequestAuthProvider | None,
    expected_headers: dict[str, str],
) -> None:
    requests = _record_http_requests(
        monkeypatch, status_code=status_code, location=location
    )

    class RedirectInterface:
        transport_config = RequestTransportConfig(
            base_url="https://service.example.test",
            auth_provider=auth_provider,
            retry_policy=RequestRetryPolicy(max_attempts=2),
        )

    with pytest.raises(RequestTransportError) as error_info:
        UrllibRequestTransport().execute(
            interface_cls=RedirectInterface,
            operation=RequestQueryOperation(
                name="list", method=method, path="/projects"
            ),
            plan=RequestQueryPlan(
                operation_name="list",
                action="filter",
                method=method,
                path="/projects",
                body={"name": "Alpha"} if method == "POST" else None,
            ),
        )

    error = error_info.value
    assert error.status_code == status_code
    assert error.headers["Location"] == location
    assert error.headers["X-Request-ID"] == "redirect-1"
    assert error.retry_count == 0
    assert len(requests) == 1
    assert requests[0].full_url == "https://service.example.test/projects"
    assert requests[0].get_method() == method
    assert requests[0].data == (b'{"name": "Alpha"}' if method == "POST" else None)
    for name, value in expected_headers.items():
        assert requests[0].get_header(name) == value


def test_default_urllib_transport_still_sends_json_without_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _record_http_requests(monkeypatch, status_code=200)

    class JsonInterface:
        transport_config = RequestTransportConfig(
            base_url="https://service.example.test",
            auth_provider=BearerTokenAuthProvider("test-token"),
        )

    result = UrllibRequestTransport().execute(
        interface_cls=JsonInterface,
        operation=RequestQueryOperation(
            name="search", method="POST", path="/projects/search"
        ),
        plan=RequestQueryPlan(
            operation_name="search",
            action="filter",
            method="POST",
            path="/projects/search",
            body={"name": "Alpha"},
        ),
    )

    assert result.items == ({"id": 1},)
    assert len(requests) == 1
    assert requests[0].get_method() == "POST"
    assert requests[0].data == b'{"name": "Alpha"}'
    assert requests[0].get_header("Content-type") == "application/json"
    assert requests[0].get_header("Authorization") == "Bearer test-token"
