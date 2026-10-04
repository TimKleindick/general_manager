"""Verify local distribution hashes against a version published on PyPI."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
import sys
from collections.abc import Sequence
from email.message import Message
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


class VerificationError(ValueError):
    """Raised when local artifacts cannot be proven safe to publish."""


def _single_artifact(dist_dir: Path, pattern: str, label: str) -> Path:
    try:
        artifacts = sorted(dist_dir.glob(pattern))
    except OSError as exc:
        message = f"Could not inspect distribution directory {dist_dir}: {exc}"
        raise VerificationError(message) from exc
    if len(artifacts) != 1:
        names = ", ".join(artifact.name for artifact in artifacts) or "none"
        message = (
            f"Expected exactly one {label} in {dist_dir}, found "
            f"{len(artifacts)}: {names}"
        )
        raise VerificationError(message)
    return artifacts[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        message = f"Could not hash local artifact {path}: {exc}"
        raise VerificationError(message) from exc
    return digest.hexdigest()


def _local_checksums(dist_dir: Path) -> dict[str, str]:
    wheel = _single_artifact(dist_dir, "*.whl", "wheel")
    sdist = _single_artifact(dist_dir, "*.tar.gz", "sdist")
    intended = {wheel, sdist}
    try:
        entries = set(dist_dir.iterdir())
    except OSError as exc:
        message = f"Could not inspect distribution directory {dist_dir}: {exc}"
        raise VerificationError(message) from exc
    unexpected = sorted(entry.name for entry in entries - intended)
    if unexpected:
        message = f"Unexpected local artifact entries: {', '.join(unexpected)}"
        raise VerificationError(message)
    for artifact in intended:
        try:
            mode = artifact.stat(follow_symlinks=False).st_mode
        except OSError as exc:
            message = f"Could not inspect local artifact {artifact}: {exc}"
            raise VerificationError(message) from exc
        if not stat.S_ISREG(mode):
            message = f"Local artifact is not a regular file: {artifact.name}"
            raise VerificationError(message)
    return {artifact.name: _sha256(artifact) for artifact in (wheel, sdist)}


def _response_details(status: int, headers: Message) -> str:
    details = [f"HTTP {status}"]
    for name in ("Age", "X-Cache", "X-PyPI-Last-Serial", "Retry-After"):
        if value := headers.get(name):
            details.append(f"{name}={value}")
    return "; ".join(details)


def _remote_checksums(
    project: str, version: str, *, timeout: float = 30
) -> tuple[dict[str, str], str]:
    # Use the canonical package name without relying on a redirect.
    normalized_project = re.sub(r"[-_.]+", "-", project).lower()
    endpoint = (
        f"https://pypi.org/pypi/{quote(normalized_project, safe='')}/"
        f"{quote(version, safe='')}/json"
    )
    request = Request(  # noqa: S310 - the endpoint has a fixed HTTPS PyPI host.
        # Request revalidation; this is not a guarantee of immediate visibility.
        endpoint,
        headers={"Accept": "application/json", "Cache-Control": "no-cache"},
    )
    try:
        with urlopen(  # noqa: S310 - the request has a fixed HTTPS PyPI host.
            request, timeout=timeout
        ) as response:
            details = _response_details(response.status, response.headers)
            print(f"PyPI query {endpoint}: {details}", file=sys.stderr)
            payload = response.read()
    except HTTPError as exc:
        details = _response_details(exc.code, exc.headers)
        exc.close()
        print(f"PyPI query {endpoint}: {details}", file=sys.stderr)
        if exc.code == 404:
            return {}, details
        message = f"Could not query PyPI for {project} {version}: {details}"
        raise VerificationError(message) from exc
    except (OSError, URLError) as exc:
        message = f"Could not query PyPI for {project} {version}: {exc}"
        raise VerificationError(message) from exc

    try:
        document: Any = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        message = f"PyPI returned invalid JSON for {project} {version}"
        raise VerificationError(message) from exc
    if not isinstance(document, dict) or not isinstance(document.get("urls"), list):
        message = f"PyPI returned an invalid release response for {project} {version}"
        raise VerificationError(message)

    checksums: dict[str, str] = {}
    for item in document["urls"]:
        if not isinstance(item, dict):
            message = f"PyPI returned an invalid file entry for {project} {version}"
            raise VerificationError(message)
        filename = item.get("filename")
        digests = item.get("digests")
        checksum = digests.get("sha256") if isinstance(digests, dict) else None
        if not isinstance(filename, str) or not isinstance(checksum, str):
            message = f"PyPI returned incomplete file metadata for {project} {version}"
            raise VerificationError(message)
        if _SHA256.fullmatch(checksum) is None:
            message = f"PyPI returned an invalid SHA-256 for {filename}"
            raise VerificationError(message)
        normalized = checksum.lower()
        if filename in checksums and checksums[filename] != normalized:
            message = f"PyPI returned conflicting SHA-256 values for {filename}"
            raise VerificationError(message)
        checksums[filename] = normalized
    return checksums, details


def verify_artifacts(
    project: str,
    version: str,
    dist_dir: Path,
    *,
    require_all: bool = False,
    wait_seconds: float = 0,
) -> None:
    """Verify hashes, optionally waiting for a complete public release file set.

    Only missing files may be polled. The monotonic deadline bounds polling and
    result acceptance; urllib's socket timeout is not a total request deadline.
    """
    if not math.isfinite(wait_seconds) or wait_seconds < 0:
        message = "wait_seconds must be a finite non-negative number"
        raise VerificationError(message)
    if wait_seconds and not require_all:
        message = "A visibility wait requires --require-all"
        raise VerificationError(message)
    local = _local_checksums(dist_dir)
    deadline = monotonic() + wait_seconds if wait_seconds else None
    missing = sorted(local)
    details = "no response received"
    while True:
        remaining = 30.0 if deadline is None else deadline - monotonic()
        if remaining <= 0:
            break
        remote, details = _remote_checksums(
            project, version, timeout=min(30, remaining)
        )

        unexpected = sorted(remote.keys() - local.keys())
        if unexpected:
            message = f"Unexpected PyPI artifacts: {', '.join(unexpected)}"
            raise VerificationError(message)

        # Validate every visible file before considering a retry for missing ones.
        for filename, local_checksum in local.items():
            remote_checksum = remote.get(filename)
            if remote_checksum is not None and remote_checksum != local_checksum:
                message = (
                    f"{filename} has PyPI SHA-256 {remote_checksum}, "
                    f"not local SHA-256 {local_checksum}"
                )
                raise VerificationError(message)

        missing = sorted(local.keys() - remote.keys())
        if deadline is not None and monotonic() >= deadline:
            break
        if not require_all or not missing:
            print(
                f"Verified {len(remote)} PyPI artifacts for {project} {version}",
                file=sys.stderr,
            )
            return
        if deadline is None:
            message = f"Local artifacts missing from PyPI: {', '.join(missing)}"
            raise VerificationError(message)
        print(
            f"Waiting for PyPI visibility: {', '.join(missing)} ({details})",
            file=sys.stderr,
        )
        sleep(min(5, max(0, deadline - monotonic())))

    message = (
        f"PyPI visibility deadline exceeded after {wait_seconds:g}s for "
        f"{project} {version}; last response: {details}; "
        f"Local artifacts missing from PyPI: {', '.join(missing) or 'none (response too late)'}. "
        "Public availability was not verified in time; this does not establish an upload failure."
    )
    raise VerificationError(message)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("version")
    parser.add_argument("dist_dir", type=Path)
    parser.add_argument("--require-all", action="store_true")
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=0,
        help="Poll missing files every 5s up to this visibility budget (requires --require-all)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Run PyPI artifact verification from the command line."""
    args = _parse_args(argv)
    verify_artifacts(
        args.project,
        args.version,
        args.dist_dir,
        require_all=args.require_all,
        wait_seconds=args.wait_seconds,
    )


if __name__ == "__main__":
    try:
        main()
    except VerificationError as exc:
        raise SystemExit(str(exc)) from exc
