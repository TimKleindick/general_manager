"""Tests for hash-safe PyPI artifact verification."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Sequence
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Protocol, Self, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest


class ArtifactVerifier(Protocol):
    """Callable interface exposed by the PyPI verifier."""

    def __call__(
        self,
        project: str,
        version: str,
        dist_dir: Path,
        *,
        require_all: bool = False,
        wait_seconds: float = 0,
    ) -> None: ...


class FakeResponse:
    """Minimal context-managed urllib response."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.status = 200
        self.headers = Message()
        self.headers["X-Cache"] = "HIT"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _module() -> ModuleType:
    try:
        return importlib.import_module("scripts.verify_pypi_artifacts")
    except ModuleNotFoundError:
        pytest.fail("scripts.verify_pypi_artifacts is missing")


def _verifier() -> ArtifactVerifier:
    verifier = getattr(_module(), "verify_artifacts", None)
    assert callable(verifier), "verify_artifacts is missing"
    return cast(ArtifactVerifier, verifier)


def _write_dist(dist_dir: Path) -> dict[str, str]:
    contents = {
        "generalmanager-1.2.3-py3-none-any.whl": b"wheel contents",
        "generalmanager-1.2.3.tar.gz": b"sdist contents",
    }
    checksums: dict[str, str] = {}
    for filename, payload in contents.items():
        (dist_dir / filename).write_bytes(payload)
        checksums[filename] = hashlib.sha256(payload).hexdigest()
    return checksums


def _remote_payload(checksums: dict[str, str]) -> bytes:
    return json.dumps(
        {
            "urls": [
                {"filename": filename, "digests": {"sha256": checksum}}
                for filename, checksum in checksums.items()
            ]
        }
    ).encode()


def _install_response(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> list[str]:
    requested_urls: list[str] = []

    def fake_urlopen(request: Request, *, timeout: int) -> FakeResponse:
        assert timeout == 30
        requested_urls.append(request.full_url)
        return FakeResponse(payload)

    monkeypatch.setattr(_module(), "urlopen", fake_urlopen)
    return requested_urls


def test_allows_missing_remote_release_before_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_dist(tmp_path)

    def missing_release(request: Request, *, timeout: int) -> FakeResponse:
        del timeout
        raise HTTPError(request.full_url, 404, "Not Found", Message(), None)

    monkeypatch.setattr(_module(), "urlopen", missing_release)

    _verifier()("GeneralManager", "1.2.3", tmp_path)


def test_accepts_matching_existing_filename_and_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checksums = _write_dist(tmp_path)
    wheel = next(name for name in checksums if name.endswith(".whl"))
    requested_urls = _install_response(
        monkeypatch, _remote_payload({wheel: checksums[wheel]})
    )

    _verifier()("GeneralManager", "1.2.3", tmp_path)

    assert requested_urls == ["https://pypi.org/pypi/generalmanager/1.2.3/json"]


def test_rejects_existing_filename_with_mismatched_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checksums = _write_dist(tmp_path)
    wheel = next(name for name in checksums if name.endswith(".whl"))
    _install_response(monkeypatch, _remote_payload({wheel: "0" * 64}))

    with pytest.raises(ValueError, match=rf"{wheel}.*SHA-256"):
        _verifier()("GeneralManager", "1.2.3", tmp_path)


def test_require_all_rejects_missing_remote_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checksums = _write_dist(tmp_path)
    wheel = next(name for name in checksums if name.endswith(".whl"))
    sdist = next(name for name in checksums if name.endswith(".tar.gz"))
    _install_response(monkeypatch, _remote_payload({wheel: checksums[wheel]}))

    with pytest.raises(ValueError, match=rf"missing from PyPI.*{sdist}"):
        _verifier()("GeneralManager", "1.2.3", tmp_path, require_all=True)


def test_require_all_accepts_every_matching_remote_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checksums = _write_dist(tmp_path)
    _install_response(monkeypatch, _remote_payload(checksums))

    _verifier()("GeneralManager", "1.2.3", tmp_path, require_all=True)


@pytest.mark.parametrize(
    "failure",
    [
        URLError("connection failed"),
        HTTPError("https://pypi.org", 500, "Server Error", Message(), None),
    ],
)
def test_network_errors_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    _write_dist(tmp_path)

    def failing_urlopen(request: Request, *, timeout: int) -> FakeResponse:
        del request, timeout
        raise failure

    monkeypatch.setattr(_module(), "urlopen", failing_urlopen)

    with pytest.raises(ValueError, match="Could not query PyPI"):
        _verifier()("GeneralManager", "1.2.3", tmp_path)


def test_invalid_json_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_dist(tmp_path)
    _install_response(monkeypatch, b"not JSON")

    with pytest.raises(ValueError, match="invalid JSON"):
        _verifier()("GeneralManager", "1.2.3", tmp_path)


@pytest.mark.parametrize("wheel_count", [0, 2])
def test_requires_exactly_one_local_wheel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wheel_count: int,
) -> None:
    (tmp_path / "generalmanager-1.2.3.tar.gz").write_bytes(b"sdist")
    for index in range(wheel_count):
        (tmp_path / f"generalmanager-1.2.3-{index}-py3-none-any.whl").write_bytes(
            b"wheel"
        )

    def unexpected_request(request: Request, *, timeout: int) -> FakeResponse:
        del request, timeout
        pytest.fail("PyPI queried before local artifact validation")

    monkeypatch.setattr(_module(), "urlopen", unexpected_request)

    with pytest.raises(ValueError, match="exactly one wheel"):
        _verifier()("GeneralManager", "1.2.3", tmp_path)


def test_rejects_unexpected_local_entries_before_querying_pypi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_dist(tmp_path)
    (tmp_path / "unexpected.txt").write_text("not a release artifact")

    def unexpected_request(request: Request, *, timeout: int) -> FakeResponse:
        del request, timeout
        pytest.fail("PyPI queried before exact local artifact validation")

    monkeypatch.setattr(_module(), "urlopen", unexpected_request)

    with pytest.raises(ValueError, match=r"Unexpected local artifact.*unexpected\.txt"):
        _verifier()("GeneralManager", "1.2.3", tmp_path)


@pytest.mark.parametrize("require_all", [False, True])
def test_rejects_unexpected_remote_filenames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    require_all: bool,
) -> None:
    checksums = _write_dist(tmp_path)
    checksums["generalmanager-1.2.3-py2-none-any.whl"] = "a" * 64
    _install_response(monkeypatch, _remote_payload(checksums))

    with pytest.raises(
        ValueError,
        match=r"Unexpected PyPI artifact.*generalmanager-1\.2\.3-py2",
    ):
        _verifier()(
            "GeneralManager",
            "1.2.3",
            tmp_path,
            require_all=require_all,
        )


def test_cli_passes_require_all_to_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    calls: list[tuple[str, str, Path, bool, float]] = []

    def record_verification(
        project: str,
        version: str,
        dist_dir: Path,
        *,
        require_all: bool = False,
        wait_seconds: float = 0,
    ) -> None:
        calls.append((project, version, dist_dir, require_all, wait_seconds))

    monkeypatch.setattr(module, "verify_artifacts", record_verification)

    main = getattr(module, "main", None)
    assert callable(main), "main is missing"
    main(["GeneralManager", "1.2.3", "dist", "--require-all", "--wait-seconds", "60"])

    assert calls == [("GeneralManager", "1.2.3", Path("dist"), True, 60)]


class FakeClock:
    """Advance monotonic time without real sleeps or network access."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        monkeypatch.setattr(_module(), "monotonic", lambda: self.now, raising=False)
        monkeypatch.setattr(_module(), "sleep", self.sleep, raising=False)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _install_sequence(
    monkeypatch: pytest.MonkeyPatch,
    responses: Sequence[bytes | Exception],
) -> list[float]:
    timeouts: list[float] = []

    def fake_urlopen(request: Request, *, timeout: float) -> FakeResponse:
        assert request.full_url == "https://pypi.org/pypi/generalmanager/1.2.3/json"
        assert request.get_header("Cache-control") == "no-cache"
        timeouts.append(timeout)
        assert len(timeouts) <= len(responses), "Unexpected extra PyPI request"
        response = responses[len(timeouts) - 1]
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)

    monkeypatch.setattr(_module(), "urlopen", fake_urlopen)
    return timeouts


def _http_error(status: int) -> HTTPError:
    headers = Message()
    headers["X-Cache"] = "HIT"
    headers["Age"] = "2"
    return HTTPError("https://pypi.org", status, "test response", headers, None)


def test_waits_only_for_valid_delayed_visibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checksums = _write_dist(tmp_path)
    wheel = next(iter(checksums))
    clock = FakeClock(monkeypatch)
    timeouts = _install_sequence(
        monkeypatch,
        [
            _http_error(404),
            _remote_payload({}),
            _remote_payload({wheel: checksums[wheel]}),
            _remote_payload(checksums),
        ],
    )

    _verifier()("GeneralManager", "1.2.3", tmp_path, require_all=True, wait_seconds=60)

    assert timeouts == [30, 30, 30, 30]
    assert clock.sleeps == [5, 5, 5]
    output = capsys.readouterr().err
    assert "HTTP 404" in output and "HTTP 200" in output
    assert "X-Cache=HIT" in output and "Age=2" in output
    assert "Verified 2 PyPI artifacts" in output


@pytest.mark.parametrize("not_found", [False, True])
def test_missing_files_fail_at_visibility_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, not_found: bool
) -> None:
    checksums = _write_dist(tmp_path)
    wheel, sdist = checksums
    clock = FakeClock(monkeypatch)
    responses = [
        _http_error(404) if not_found else _remote_payload({wheel: checksums[wheel]})
        for _ in range(3)
    ]
    timeouts = _install_sequence(monkeypatch, responses)

    with pytest.raises(ValueError, match=rf"visibility deadline.*{sdist}"):
        _verifier()(
            "GeneralManager", "1.2.3", tmp_path, require_all=True, wait_seconds=12
        )

    assert timeouts == [12, 7, 2]
    assert clock.sleeps == [5, 5, 2]
    assert clock.now == 12


@pytest.mark.parametrize("after_missing", [False, True])
@pytest.mark.parametrize(
    "failure",
    [
        401,
        403,
        429,
        500,
        URLError("connection failed"),
        TimeoutError("read timeout"),
        b"not JSON",
        b'{"urls": [null]}',
    ],
)
def test_query_failures_are_never_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: int | Exception | bytes,
    after_missing: bool,
) -> None:
    _write_dist(tmp_path)
    clock = FakeClock(monkeypatch)
    response = _http_error(failure) if isinstance(failure, int) else failure
    sequence = ([_remote_payload({})] if after_missing else []) + [response]
    timeouts = _install_sequence(monkeypatch, sequence)
    message = f"HTTP {failure}" if isinstance(failure, int) else "PyPI"

    with pytest.raises(ValueError, match=message):
        _verifier()(
            "GeneralManager", "1.2.3", tmp_path, require_all=True, wait_seconds=60
        )

    assert len(timeouts) == 1 + int(after_missing)
    assert clock.sleeps == ([5] if after_missing else [])


@pytest.mark.parametrize("corruption", ["hash", "extra", "invalid_digest"])
def test_visible_corruption_fails_even_when_other_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    checksums = _write_dist(tmp_path)
    wheel = next(iter(checksums))
    remote = {wheel: checksums[wheel]}
    if corruption == "extra":
        remote["unexpected.whl"] = "0" * 64
    else:
        remote[wheel] = "0" * 64 if corruption == "hash" else "invalid"
    clock = FakeClock(monkeypatch)
    timeouts = _install_sequence(monkeypatch, [_remote_payload(remote)])

    with pytest.raises(ValueError, match=r"SHA-256|Unexpected PyPI"):
        _verifier()(
            "GeneralManager", "1.2.3", tmp_path, require_all=True, wait_seconds=60
        )

    assert len(timeouts) == 1
    assert clock.sleeps == []


def test_does_not_accept_matching_response_at_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checksums = _write_dist(tmp_path)
    clock = FakeClock(monkeypatch)

    def slow_response(request: Request, *, timeout: float) -> FakeResponse:
        assert timeout == 3
        clock.now += 3
        return FakeResponse(_remote_payload(checksums))

    monkeypatch.setattr(_module(), "urlopen", slow_response)
    with pytest.raises(ValueError, match="visibility deadline"):
        _verifier()(
            "GeneralManager", "1.2.3", tmp_path, require_all=True, wait_seconds=3
        )
    assert clock.sleeps == []


@pytest.mark.parametrize("wait_seconds", [-1, float("nan"), float("inf")])
def test_rejects_invalid_wait_budget_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_seconds: float
) -> None:
    timeouts = _install_sequence(monkeypatch, [])
    with pytest.raises(ValueError, match="finite non-negative"):
        _verifier()(
            "GeneralManager",
            "1.2.3",
            tmp_path,
            require_all=True,
            wait_seconds=wait_seconds,
        )
    assert timeouts == []


def test_wait_budget_requires_post_upload_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeouts = _install_sequence(monkeypatch, [])
    with pytest.raises(ValueError, match="require-all"):
        _verifier()("GeneralManager", "1.2.3", tmp_path, wait_seconds=60)
    assert timeouts == []


def test_zero_wait_preserves_one_shot_missing_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_dist(tmp_path)
    clock = FakeClock(monkeypatch)
    timeouts = _install_sequence(monkeypatch, [_http_error(404)])
    with pytest.raises(ValueError, match="missing from PyPI"):
        _verifier()("GeneralManager", "1.2.3", tmp_path, require_all=True)
    assert timeouts == [30]
    assert clock.sleeps == []
