"""Verify local distribution hashes against a version published on PyPI."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from collections.abc import Sequence
from pathlib import Path
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


def _remote_checksums(project: str, version: str) -> dict[str, str]:
    endpoint = (
        f"https://pypi.org/pypi/{quote(project, safe='')}/"
        f"{quote(version, safe='')}/json"
    )
    request = Request(  # noqa: S310 - the endpoint has a fixed HTTPS PyPI host.
        endpoint, headers={"Accept": "application/json"}
    )
    try:
        with urlopen(  # noqa: S310 - the request has a fixed HTTPS PyPI host.
            request, timeout=30
        ) as response:
            payload = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        message = f"Could not query PyPI for {project} {version}: HTTP {exc.code}"
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
    return checksums


def verify_artifacts(
    project: str,
    version: str,
    dist_dir: Path,
    *,
    require_all: bool = False,
) -> None:
    """Verify matching PyPI files and optionally require every local artifact."""
    local = _local_checksums(dist_dir)
    remote = _remote_checksums(project, version)

    unexpected = sorted(remote.keys() - local.keys())
    if unexpected:
        message = f"Unexpected PyPI artifacts: {', '.join(unexpected)}"
        raise VerificationError(message)

    for filename, local_checksum in local.items():
        remote_checksum = remote.get(filename)
        if remote_checksum is not None and remote_checksum != local_checksum:
            message = (
                f"{filename} has PyPI SHA-256 {remote_checksum}, "
                f"not local SHA-256 {local_checksum}"
            )
            raise VerificationError(message)

    if require_all:
        missing = sorted(local.keys() - remote.keys())
        if missing:
            message = f"Local artifacts missing from PyPI: {', '.join(missing)}"
            raise VerificationError(message)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("version")
    parser.add_argument("dist_dir", type=Path)
    parser.add_argument("--require-all", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Run PyPI artifact verification from the command line."""
    args = _parse_args(argv)
    verify_artifacts(
        args.project,
        args.version,
        args.dist_dir,
        require_all=args.require_all,
    )


if __name__ == "__main__":
    try:
        main()
    except VerificationError as exc:
        raise SystemExit(str(exc)) from exc
