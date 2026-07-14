"""Tests for hash-safe PyPI artifact verification."""

from __future__ import annotations

import hashlib
import importlib
import json
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
    ) -> None: ...


class FakeResponse:
    """Minimal context-managed urllib response."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

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

    assert requested_urls == ["https://pypi.org/pypi/GeneralManager/1.2.3/json"]


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
    calls: list[tuple[str, str, Path, bool]] = []

    def record_verification(
        project: str,
        version: str,
        dist_dir: Path,
        *,
        require_all: bool = False,
    ) -> None:
        calls.append((project, version, dist_dir, require_all))

    monkeypatch.setattr(module, "verify_artifacts", record_verification)

    main = getattr(module, "main", None)
    assert callable(main), "main is missing"
    main(["GeneralManager", "1.2.3", "dist", "--require-all"])

    assert calls == [("GeneralManager", "1.2.3", Path("dist"), True)]
