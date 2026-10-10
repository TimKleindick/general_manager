"""Offline contracts: no real identity, credentials, or network."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from experiments.siwc_eval.provider import Provider, EvalError, payload
from experiments.siwc_eval.auth import Attempt
from general_manager.chat.providers.base import (
    Message,
    ToolDefinition,
    ToolCallEvent,
    TextChunkEvent,
    DoneEvent,
)


class Transport:
    def __init__(self, events):
        self.events = events
        self.requests = []

    async def stream(self, body):
        self.requests.append(body)
        for event in self.events:
            yield event


def completed(output=None):
    return {
        "type": "response.completed",
        "response": {
            "output": output or [],
            "usage": {"input_tokens": 7, "output_tokens": 2},
        },
    }


def collect(provider, messages=None, tools=None):
    async def run():
        return [
            event
            async for event in provider.complete(
                messages or [Message("user", "Synthetic hello")], tools or []
            )
        ]

    return asyncio.run(run())


def test_payload_maps_system_and_namespaces_without_unsupported_parameters():
    body = payload(
        "catalog-model",
        [Message("system", "Be precise"), Message("user", "Hi")],
        [ToolDefinition("query", "Synthetic query", {"type": "object"})],
    )
    assert body["store"] is False and body["stream"] is True
    assert body["input"][0]["role"] == "developer"
    assert body["tools"][0]["type"] == "namespace"
    assert body["tools"][0]["tools"][0]["strict"] is False
    assert set(body) == {"model", "input", "store", "stream", "tools", "include"}


@pytest.mark.parametrize(
    "terminal", ["response.failed", "response.incomplete", "error", "eof"]
)
def test_failed_stream_never_releases_tool_calls(terminal):
    events = [
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "call_id": "c1",
                "name": "query",
                "arguments": "{}",
            },
        }
    ]
    if terminal != "eof":
        events.append({"type": terminal, "message": "PRIVATE SENTINEL"})
    provider = Provider("catalog-model", Transport(events))
    with pytest.raises(EvalError) as error:
        collect(provider)
    assert "PRIVATE" not in str(error.value)
    with pytest.raises(EvalError, match="stopped"):
        collect(provider)


def test_completed_tool_call_and_raw_reasoning_are_replayed():
    call = {
        "type": "function_call",
        "call_id": "c1",
        "name": "query",
        "namespace": "gm",
        "arguments": '{"manager":"MaterialManager"}',
    }
    reasoning = {"type": "reasoning", "id": "r1", "encrypted_content": "synthetic"}
    transport = Transport([completed([reasoning, call])])
    provider = Provider("catalog-model", transport)
    first = [Message("user", "List synthetic materials")]
    events = collect(provider, first, [ToolDefinition("query", "query", {})])
    assert events[0] == ToolCallEvent("c1", "query", {"manager": "MaterialManager"})
    assert isinstance(events[-1], DoneEvent)
    transport.events = [completed()]
    collect(
        provider,
        [
            *first,
            Message("assistant", "", tool_calls=(events[0],)),
            Message("tool", "[]", tool_call_id="c1"),
        ],
    )
    assert transport.requests[1]["input"][1:3] == [reasoning, call]
    assert transport.requests[1]["input"][-1]["type"] == "function_call_output"
    assert "previous_response_id" not in transport.requests[1]


def test_request_budget_has_no_retry_or_fallback():
    transport = Transport([completed()])
    provider = Provider("catalog-model", transport, max_requests=1)
    collect(provider)
    with pytest.raises(EvalError, match="budget"):
        collect(provider)
    assert len(transport.requests) == 1


def test_larger_request_budget_is_enforced_without_retry():
    transport = Transport([completed()])
    provider = Provider("catalog-model", transport, max_requests=16)
    for _ in range(16):
        collect(provider)
    with pytest.raises(EvalError, match="request_budget_exhausted"):
        collect(provider)
    assert len(transport.requests) == 16


@pytest.mark.parametrize("budget", [True, 0, -1, 257, 16.0])
def test_larger_budget_still_rejects_invalid_configuration(budget):
    with pytest.raises(EvalError, match="invalid_configuration"):
        Provider("offline", Transport([]), max_requests=budget)


def test_responses_history_survives_real_harness_follow_up():
    from general_manager.chat.evals.fixtures import setup_toy_schema
    from general_manager.chat.evals.runner import load_dataset, run_case
    from general_manager.chat.tools import get_tool_definitions

    reasoning = {"type": "reasoning", "id": "r1", "encrypted_content": "synthetic"}

    class ConversationTransport:
        def __init__(self):
            self.requests = []

        async def stream(self, body):
            self.requests.append(body)
            index = len(self.requests)
            if index in {1, 3}:
                args = {"manager": "PartManager", "fields": ["name"]}
                if index == 3:
                    args["filters"] = {"material_Name": "Steel"}
                yield completed(
                    [
                        reasoning,
                        {
                            "type": "function_call",
                            "call_id": f"call-{index}",
                            "namespace": "gm",
                            "name": "query",
                            "arguments": json.dumps(args),
                        },
                    ]
                )
            else:
                answer = "Bolt, Bearing, Gear" if index == 2 else "Bolt"
                yield {"type": "response.output_text.delta", "delta": answer}
                yield completed(
                    [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": answer}],
                        }
                    ]
                )

    setup_toy_schema()
    transport = ConversationTransport()
    case = load_dataset("follow_ups")[0]
    result = asyncio.run(
        run_case(
            Provider("offline", transport, max_requests=32),
            case,
            get_tool_definitions(),
            max_tool_iterations=16,
        )
    )
    assert result.passed
    history = transport.requests[2]["input"]
    assert reasoning in history
    assert any(item.get("type") == "function_call_output" for item in history)
    assert history[-1] == {"role": "user", "content": case.conversation[1]["user"]}
    assert len(transport.requests) == 4


def test_text_and_usage_wait_for_completed():
    provider = Provider(
        "catalog-model",
        Transport(
            [{"type": "response.output_text.delta", "delta": "synthetic"}, completed()]
        ),
    )
    assert collect(provider) == [
        TextChunkEvent("synthetic"),
        DoneEvent(
            __import__(
                "general_manager.chat.providers.base", fromlist=["TokenUsage"]
            ).TokenUsage(7, 2)
        ),
    ]


def test_transport_exception_is_sanitized():
    class Broken:
        async def stream(self, body):
            raise RuntimeError("PRIVATE_TOKEN")
            yield

    with pytest.raises(EvalError) as error:
        collect(Provider("catalog-model", Broken()))
    assert str(error.value) == "transport_error"
    assert error.value.__cause__ is None


def test_oauth_dynamic_registration_pkce_and_one_time_state():
    attempt = Attempt("urn:uuid:synthetic", "http://127.0.0.1:1234/auth/callback")
    params = parse_qs(urlsplit(attempt.url()).query)
    assert params["client_id"] == ["dynamic_agent_client"]
    assert params["code_challenge_method"] == ["S256"]
    assert "chatgpt.tokens.use.direct" in params["scope"][0]
    form = attempt.exchange_form(
        {
            "state": [attempt.state],
            "code": ["synthetic-code"],
            "client_id": ["oaiapp_synthetic"],
        }
    )
    assert form["client_id"] == "oaiapp_synthetic"
    assert form["redirect_uri"] == attempt.redirect_uri
    assert form["code_verifier"] == attempt.verifier
    with pytest.raises(EvalError):
        attempt.exchange_form({})


@pytest.mark.parametrize(
    "callback",
    [
        {"state": ["wrong"], "code": ["synthetic"]},
        {"state": [""], "code": ["synthetic"]},
        {"error": ["access_denied"]},
        {"code": ["synthetic"], "client_id": ["dynamic_agent_client"]},
        {"code": ["synthetic"], "client_id": ["another-client"]},
    ],
)
def test_callback_rejects_invalid_state_consent_and_client(callback):
    attempt = Attempt("host", "http://127.0.0.1:1234/auth/callback", "oaiapp_saved")
    callback.setdefault("state", [attempt.state])
    with pytest.raises(EvalError):
        attempt.exchange_form(callback)


def test_returning_client_omits_registration_hint():
    attempt = Attempt("host", "http://127.0.0.1:1234/auth/callback", "oaiapp_saved")
    assert "agent_name_hint" not in parse_qs(urlsplit(attempt.url()).query)
    assert (
        attempt.exchange_form({"state": [attempt.state], "code": ["code"]})["client_id"]
        == "oaiapp_saved"
    )


def test_mapping_cannot_persist_tokens_and_is_private(tmp_path):
    from experiments.siwc_eval.auth import save_mapping, load_mapping

    path = tmp_path / "account" / "registration.json"
    with pytest.raises(EvalError):
        save_mapping(path, {"access_token": "synthetic"})
    save_mapping(path, {"host": "synthetic"})
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_mapping(path) == {"host": "synthetic"}


def test_logout_clears_tokens_even_if_revoke_fails(monkeypatch):
    from experiments.siwc_eval.auth import Session

    def fail(*args, **kwargs):
        raise EvalError("PRIVATE")

    monkeypatch.setattr("experiments.siwc_eval.auth.request", fail)
    session = Session(
        "oaiapp_synthetic",
        {"refresh_token": "synthetic"},
        {"revocation_endpoint": "https://auth.openai.com/revoke"},
        0,
    )
    assert session.logout() is False
    assert session.tokens == {}


def test_offline_runner_uses_existing_harness_and_negative_control():
    import os
    import subprocess
    import sys

    result = subprocess.run(  # noqa: S603 -- fixed local module, no user command
        [sys.executable, "-m", "experiments.siwc_eval.runner"],
        capture_output=True,
        text=True,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "nonexistent_company_settings"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert len(report["results"]) == 6
    assert all(
        row["passed"] == (row["provider"] != "negative-control")
        for row in report["results"]
    )
    assert "Steel" not in result.stdout
    assert report["quality_comparison"] is False


@pytest.mark.parametrize("change", [None, "iss", "aud", "nonce", "exp", "signature"])
def test_identity_signature_and_all_claims_are_validated(monkeypatch, change):
    import time
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from experiments.siwc_eval.auth import verify_identity

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "synthetic"
    monkeypatch.setattr(
        "experiments.siwc_eval.auth.json_request", lambda _url: {"keys": [jwk]}
    )
    claims = {
        "iss": "https://auth.openai.com",
        "aud": "oaiapp_synthetic",
        "nonce": "nonce",
        "sub": "synthetic-subject",
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
    }
    if change in {"iss", "aud", "nonce"}:
        claims[change] = "wrong"
    elif change == "exp":
        claims["exp"] = int(time.time()) - 300
    signing_key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if change == "signature"
        else key
    )
    token = jwt.encode(
        claims, signing_key, algorithm="RS256", headers={"kid": "synthetic"}
    )
    discovery = {
        "issuer": "https://auth.openai.com",
        "jwks_uri": "https://auth.openai.com/.well-known/jwks.json",
    }
    if change is None:
        assert (
            verify_identity(
                {"id_token": token}, "oaiapp_synthetic", "nonce", discovery
            )["sub"]
            == "synthetic-subject"
        )
    else:
        with pytest.raises(EvalError, match="identity_validation_failed"):
            verify_identity({"id_token": token}, "oaiapp_synthetic", "nonce", discovery)


def test_real_sse_parser_handles_multiline_unicode_and_terminal(monkeypatch):
    import httpx
    from experiments.siwc_eval.http import LiveTransport

    wire = (
        'event: response.output_text.delta\r\ndata: {"type":"response.output_text.delta",\r\n'
        'data: "delta":"Grüße"}\r\n\r\ndata: {"type":"response.completed","response":{"output":[]}}\n\n'
    ).encode()
    factory = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: factory(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, headers={"Content-Type": "text/event-stream"}, content=wire
                )
            ),
            **kwargs,
        ),
    )
    events = collect(Provider("offline", LiveTransport("synthetic-not-a-token")))
    assert events[0] == TextChunkEvent("Grüße")


def test_inference_read_timeout_allows_reasoning_pauses(monkeypatch):
    import httpx
    from experiments.siwc_eval.http import LiveTransport

    observed = []

    def respond(request):
        observed.append(request.extensions["timeout"])
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=b'data: {"type":"response.completed","response":{"output":[]}}\n\n',
        )

    factory = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs),
    )
    collect(Provider("offline", LiveTransport("synthetic")))
    assert observed == [{"connect": 15, "read": 45, "write": 15, "pool": 15}]


def test_budget_invalid_config_and_unknown_tool_are_rejected():
    with pytest.raises(EvalError):
        Provider("offline", Transport([]), max_requests=0)
    bad = {"type": "function_call", "call_id": "c", "name": "mutate", "arguments": "{}"}
    with pytest.raises(EvalError, match="invalid_tool_call"):
        collect(Provider("offline", Transport([completed([bad])])))


def test_http_error_shape_and_code_survive_without_remote_text(monkeypatch):
    import io
    from urllib.error import HTTPError
    from experiments.siwc_eval.http import request

    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError(
                "https://api.openai.com/v1/responses",
                429,
                "PRIVATE",
                {},
                io.BytesIO(
                    b'{"error":{"code":"subscription_sharing_usage_limit_exceeded","message":"PRIVATE"}}'
                ),
            )

    monkeypatch.setattr(
        "experiments.siwc_eval.http.build_opener", lambda *_args: Opener()
    )
    with pytest.raises(EvalError) as result:
        request("https://api.openai.com/v1/responses")
    assert "PRIVATE" not in str(result.value)
    assert result.value.diagnostic["status"] == 429
    assert result.value.diagnostic["shape"] == "error"
    assert (
        result.value.diagnostic["code"] == "subscription_sharing_usage_limit_exceeded"
    )


def test_loopback_connections_have_read_timeout():
    from experiments.siwc_eval.auth import LoopbackServer
    from http.server import BaseHTTPRequestHandler
    import socket

    with LoopbackServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
        with socket.create_connection(server.server_address, timeout=2):
            connection, _ = server.get_request()
            with connection:
                assert connection.gettimeout() == 1.0


def test_cancelled_stream_closes_async_transport(monkeypatch):
    import httpx
    from experiments.siwc_eval.http import LiveTransport

    closed = []

    def deny_network(*_args, **_kwargs):
        raise EvalError("network_forbidden_in_test")

    monkeypatch.setattr("experiments.siwc_eval.http.request", deny_network)

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"unfinished":'
            await asyncio.sleep(5)

        async def aclose(self):
            closed.append(True)

    factory = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: factory(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    stream=SlowStream(),
                )
            ),
            **kwargs,
        ),
    )

    async def run():
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                async for _ in LiveTransport("synthetic").stream({}):
                    pass

    asyncio.run(run())
    assert closed == [True]


def test_incomplete_callback_has_absolute_deadline(monkeypatch):
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler
    from experiments.siwc_eval.auth import LoopbackServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    monkeypatch.setattr(LoopbackServer, "request_deadline_seconds", 0.05, raising=False)
    with LoopbackServer(("127.0.0.1", 0), Handler) as server:
        with socket.create_connection(server.server_address, timeout=2) as client:
            client.sendall(b"GET /auth/callback HTTP/1.1\r\nX-Incomplete:")
            worker = threading.Thread(target=server.handle_request, daemon=True)
            worker.start()
            worker.join(0.3)
            assert not worker.is_alive()


def test_declined_first_consent_never_logs_in(monkeypatch):
    from experiments.siwc_eval.runner import live

    calls = []
    monkeypatch.setattr("builtins.input", lambda _prompt: "NEIN")
    monkeypatch.setattr(
        "experiments.siwc_eval.auth.login", lambda: calls.append("login")
    )
    with pytest.raises(EvalError, match="user_declined"):
        live(0)
    assert calls == []


@pytest.mark.parametrize("approve_inference", [False, True])
def test_declined_inference_or_failure_revokes_session(monkeypatch, approve_inference):
    from experiments.siwc_eval.runner import live

    calls = []

    class Session:
        def access_token(self):
            return "synthetic"

        def logout(self):
            calls.append("logout")
            return True

    class Transport:
        def __init__(self, _token):
            calls.append("transport")

        def close(self):
            calls.append("close")

    async def failure(*_args):
        calls.append("evaluate")
        raise EvalError("synthetic_failure")

    answers = iter(["JA", "1", "JA" if approve_inference else "NEIN"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr("experiments.siwc_eval.auth.login", Session)
    monkeypatch.setattr("experiments.siwc_eval.http.LiveTransport", Transport)
    monkeypatch.setattr(
        "experiments.siwc_eval.http.json_request",
        lambda *_args, **_kwargs: {
            "models": [{"visibility": "list", "slug": "synthetic"}]
        },
    )
    monkeypatch.setattr("experiments.siwc_eval.runner.evaluate", failure)
    with pytest.raises(EvalError):
        live(0)
    assert calls == (
        ["transport", "evaluate", "close", "logout"]
        if approve_inference
        else ["logout"]
    )


def test_completed_items_survive_empty_terminal_output_and_replay():
    call = {
        "type": "function_call",
        "name": "query",
        "namespace": "gm",
        "call_id": "call",
        "arguments": "{}",
    }
    reasoning = {"type": "reasoning", "encrypted_content": "synthetic"}
    transport = Transport(
        [
            {"type": "response.output_item.done", "output_index": 1, "item": call},
            {"type": "response.output_item.done", "output_index": 0, "item": reasoning},
            completed(),
        ]
    )
    provider = Provider("offline", transport)
    first = [Message("user", "Synthetic query")]
    result = collect(provider, first, [ToolDefinition("query", "query", {})])
    assert result[0] == ToolCallEvent("call", "query", {})
    transport.events = [completed()]
    collect(
        provider,
        [
            *first,
            Message("assistant", "", tool_calls=(result[0],)),
            Message("tool", "[]", tool_call_id="call"),
        ],
    )
    assert transport.requests[-1]["input"][1:3] == [reasoning, call]


def test_completed_stream_is_closed_before_returning():
    closed = []

    class Closing:
        async def stream(self, body):
            try:
                yield completed()
                await asyncio.sleep(10)
            finally:
                closed.append(True)

    collect(Provider("offline", Closing()))
    assert closed == [True]
