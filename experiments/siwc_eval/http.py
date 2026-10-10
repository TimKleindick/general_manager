"""Fixed-origin HTTPS, bounded reads, no redirects or implicit retries."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from collections.abc import AsyncGenerator
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import EvalError, fail, diagnostic

API = "https://api.openai.com/v1"
AUTH = "https://auth.openai.com"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        fail("redirect_refused")


def request(
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    form: bool = False,
) -> Any:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc not in {
        "api.openai.com",
        "auth.openai.com",
    }:
        fail("untrusted_endpoint")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = (urlencode(body) if form else json.dumps(body)).encode()
        headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if form else "application/json"
        )
    try:
        return build_opener(NoRedirect()).open(
            Request(url, data=data, headers=headers),  # noqa: S310 -- exact HTTPS origin allowlist
            timeout=15,
        )
    except HTTPError as error:
        # No response text, URL, headers, or credential-bearing exception chain.
        status = error.code
        try:
            body = json.loads(error.read(8192))
        except (ValueError, OSError):
            body = None
        details = diagnostic(body, status, error.headers.get("x-request-id", ""))
        error.close()
        raise EvalError(f"http_{status}", details) from None
    except (OSError, ValueError):
        raise EvalError("network_error") from None


def json_request(url: str, **kwargs: Any) -> dict[str, Any]:
    with request(url, **kwargs) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        fail("response_too_large")
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError):
        raise EvalError("invalid_json") from None
    if not isinstance(result, dict):
        fail("invalid_json")
    return result


class LiveTransport:
    def __init__(self, token: str) -> None:
        self._token = token

    async def stream(
        self, body: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        import httpx

        # Async I/O is cancelled and closed with the deadline, including partial
        # SSE lines and connection establishment. No abandoned executor thread.
        async with (
            asyncio.timeout(75),
            httpx.AsyncClient(
                # Reasoning can pause the stream longer than connection setup.
                # The outer deadline still bounds the whole request to 75 s.
                timeout=httpx.Timeout(15, read=45),
                follow_redirects=False,
                trust_env=False,
            ) as client,
        ):
            async with client.stream(
                "POST",
                API + "/responses",
                json=body,
                headers={
                    "Authorization": "Bearer " + self._token,
                    "Accept": "text/event-stream",
                },
            ) as response:
                size = 0
                pending = b""
                data: list[str] = []
                if response.status_code != 200:
                    error_bytes = b""
                    async for chunk in response.aiter_bytes():
                        error_bytes += chunk[: 8192 - len(error_bytes)]
                        if len(error_bytes) >= 8192:
                            break
                    try:
                        error_body = json.loads(error_bytes)
                    except ValueError:
                        error_body = None
                    raise EvalError(
                        f"http_{response.status_code}",
                        diagnostic(
                            error_body,
                            response.status_code,
                            response.headers.get("x-request-id", ""),
                        ),
                    )
                content_type = response.headers.get("Content-Type")
                # The live SIWC route can omit this header despite returning SSE.
                # An absent header still requires valid SSE and response.completed.
                if content_type and "text/event-stream" not in content_type:
                    fail("expected_event_stream")
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 1_000_000:
                        fail("stream_budget_exhausted")
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        value = line.decode("utf-8").rstrip("\r")
                        if value.startswith("data:"):
                            data.append(value[5:].lstrip(" "))
                        elif not value and data:
                            joined, data = "\n".join(data), []
                            if joined == "[DONE]":
                                return
                            event = json.loads(joined)
                            yield event
                            if event.get("type") in {
                                "response.completed",
                                "response.failed",
                                "response.incomplete",
                                "error",
                            }:
                                return

    def close(self) -> None:
        self._token = ""
