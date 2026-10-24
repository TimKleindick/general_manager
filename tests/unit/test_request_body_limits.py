"""Bound actual response bytes and native socket body-read duration."""

import io
import socket
import threading
import time
from http.client import HTTPException, HTTPResponse
from typing import ClassVar
from urllib.error import HTTPError

import pytest

from general_manager.interface.requests import (
    RequestQueryOperation,
    RequestQueryPlan,
    RequestSchemaError,
    RequestTransportRequest,
    RequestTransportStatusError,
    UrllibRequestTransport,
)


class Body(io.BytesIO):
    status = 200
    headers: ClassVar[dict[str, str]] = {"Content-Length": "1"}

    def __init__(self, data):
        super().__init__(data)
        self.sizes = []

    def read1(self, size=-1):
        self.sizes.append(size)
        return super().read1(size)


def send(transport):
    return transport.send(
        RequestTransportRequest(method="GET", url="https://example.test/", path="/"),
        interface_cls=object,
        operation=RequestQueryOperation(name="all", method="GET", path="/"),
        plan=RequestQueryPlan(
            operation_name="all", action="all", method="GET", path="/"
        ),
        identification=None,
    )


def test_response_limit_uses_actual_bytes_and_closes_stream():
    body = Body(b'{"x":"' + b"a" * 100 + b'"}')
    transport = UrllibRequestTransport(
        urlopen=lambda *_args, **_kwargs: body, max_response_bytes=20
    )
    with pytest.raises(RequestSchemaError, match="byte limit"):
        send(transport)
    assert body.closed
    assert body.sizes and all(0 < size <= 21 for size in body.sizes)


def test_response_at_exact_limit_is_accepted_and_closed():
    body = Body(b'{"x":1}')
    result = send(
        UrllibRequestTransport(
            urlopen=lambda *_args, **_kwargs: body, max_response_bytes=7
        )
    )
    assert result.payload == {"x": 1}
    assert body.closed


def test_oversized_error_body_is_discarded_but_status_headers_survive():
    body = Body(b'{"x":"' + b"a" * 100 + b'"}')
    error = HTTPError(
        "https://example.test/", 503, "Unavailable", {"X-Request-ID": "abc"}, body
    )

    def opener(*args, **kwargs):
        raise error

    with pytest.raises(RequestTransportStatusError) as raised:
        send(UrllibRequestTransport(urlopen=opener, max_response_bytes=20))
    assert raised.value.status_code == 503
    assert raised.value.headers["X-Request-ID"] == "abc"
    assert raised.value.payload is None
    assert body.closed


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("http_error", [False, True])
def test_body_deadline_interrupts_native_slow_drip_reads(chunked, http_error):
    reader, writer = socket.socketpair()
    reader.settimeout(1)
    stopped = threading.Event()
    header = (
        b"Transfer-Encoding: chunked\r\n" if chunked else b"Content-Length: 100000\r\n"
    )
    writer.sendall(b"HTTP/1.1 200 OK\r\n" + header + b"\r\n")
    response = HTTPResponse(reader)
    response.begin()

    def drip():
        try:
            while not stopped.wait(0.005):
                # No newline: the chunk-size parser itself must be interrupted.
                writer.sendall(b"1" if chunked else b" ")
        except OSError:
            pass

    thread = threading.Thread(target=drip, daemon=True)
    thread.start()

    def opener(*_args, **_kwargs):
        if http_error:
            raise HTTPError(
                "https://example.test/", 503, "Unavailable", {"X-Test": "yes"}, response
            )
        return response

    started = time.monotonic()
    try:
        expected = RequestTransportStatusError if http_error else TimeoutError
        with pytest.raises(expected) as raised:
            send(UrllibRequestTransport(urlopen=opener, body_read_timeout=0.05))
        if http_error:
            assert raised.value.status_code == 503
            assert raised.value.headers["X-Test"] == "yes"
            assert raised.value.payload is None
        else:
            assert "body read deadline" in str(raised.value)
        assert time.monotonic() - started < 0.7
        assert response.closed
    finally:
        stopped.set()
        reader.close()
        writer.close()
        thread.join(timeout=1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_response_bytes": 0},
        {"max_response_bytes": True},
        {"body_read_timeout": 0},
        {"body_read_timeout": float("inf")},
        {"body_read_timeout": float("nan")},
    ],
)
def test_response_bounds_require_positive_finite_configuration(kwargs):
    with pytest.raises(ValueError):
        UrllibRequestTransport(**kwargs)


@pytest.mark.parametrize("body", [b"NOTHEX\r\n", b"5\r\nab"])
@pytest.mark.parametrize("http_error", [False, True])
def test_native_framing_failure_preserves_only_known_http_error_metadata(
    body, http_error
):
    reader, writer = socket.socketpair()
    reader.settimeout(1)
    writer.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + body)
    writer.shutdown(socket.SHUT_WR)
    response = HTTPResponse(reader)
    response.begin()

    def opener(*_args, **_kwargs):
        if http_error:
            raise HTTPError(
                "https://example.test/",
                503,
                "Unavailable",
                {"X-Test": "framing"},
                response,
            )
        return response

    try:
        expected = RequestTransportStatusError if http_error else HTTPException
        with pytest.raises(expected) as raised:
            send(UrllibRequestTransport(urlopen=opener))
        if http_error:
            assert raised.value.status_code == 503
            assert raised.value.headers["X-Test"] == "framing"
            assert raised.value.payload is None
        assert response.closed
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize("failure_type", [ConnectionResetError, OSError])
@pytest.mark.parametrize("http_error", [False, True])
def test_socket_read_failure_preserves_only_known_http_error_metadata(
    failure_type, http_error
):
    failure = failure_type("connection failed during body read")

    class FailingBody(Body):
        def read1(self, size=-1):
            raise failure

    body = FailingBody(b"")
    error = HTTPError(
        "https://example.test/", 503, "Unavailable", {"Retry-After": "7"}, body
    )

    def opener(*_args, **_kwargs):
        if http_error:
            raise error
        return body

    expected = RequestTransportStatusError if http_error else failure_type
    with pytest.raises(expected) as raised:
        send(UrllibRequestTransport(urlopen=opener))
    if http_error:
        assert raised.value.status_code == 503
        assert raised.value.headers["Retry-After"] == "7"
        assert raised.value.payload is None
        assert raised.value.__cause__ is error
    else:
        assert raised.value is failure
    assert body.closed
