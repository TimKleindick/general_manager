"""Delegate an exact run request to the existing reviewed single-run supervisor.

This boundary never authenticates or constructs a provider. The supervisor owns
fresh admission, serial ownership, source verification, STOPs and no retries.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any

from .selection import ValidationError
from .trace import fingerprint


def _public_path(path: Path, root: Path) -> Path:
    """Keep indirect inputs inside the public run-artifact workspace."""
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(root):
        message = "start artifact is outside the public workspace"
        raise ValidationError(message)
    if any(
        part.startswith(".")
        or any(
            word in part.lower()
            for word in ("credential", "session", "token", "secret")
        )
        for part in path.parts
    ):
        message = "private paths cannot be start artifacts"
        raise ValidationError(message)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            message = "start artifact path contains a symlink"
            raise ValidationError(message)
    return path


def _frozen_bytes(path: Path, expected: str, root: Path) -> bytes:
    path = _public_path(path, root)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o222:
        message = "start artifact must be a frozen regular file"
        raise ValidationError(message)
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != expected:
        message = "start artifact hash changed"
        raise ValidationError(message)
    return raw


def run_reviewed(
    request: dict[str, Any],
    *,
    start_record: Path | None,
    start_record_sha256: str | None,
) -> int:
    """Launch once only when selection and override match the bound request."""
    if start_record is None or start_record_sha256 is None:
        message = "live execution requires --start-record and --start-record-sha256; use --prepare-only to prepare the selected request for review"
        raise ValidationError(message)
    start_record = start_record.absolute()
    workspace = start_record.parent.parent
    record = json.loads(_frozen_bytes(start_record, start_record_sha256, workspace))
    # This is the existing independent launch pin, not a hash accepted from the
    # record it is meant to authenticate. The supervisor consumes it again.
    expected_control = os.environ.get("R26_EXPECTED_CONTROL_MANIFEST_SHA256")
    if (
        expected_control is None
        or expected_control != record["control_manifest_sha256"]
    ):
        message = "independent control manifest pin is missing or changed"
        raise ValidationError(message)
    item = record["run_request"]
    bound = json.loads(_frozen_bytes(Path(item["path"]), item["sha256"], workspace))
    if fingerprint(request) != fingerprint(bound):
        message = "requested cases or first message differ from reviewed run"
        raise ValidationError(message)
    root = _public_path(Path(record["control_root"]), workspace)
    manifest = json.loads(
        _frozen_bytes(root / "control-manifest.json", expected_control, workspace)
    )
    launcher = root / "root_r26_launcher.py"
    _frozen_bytes(launcher, manifest[launcher.name], workspace)
    # No direct suite invocation: the existing launcher revalidates all actual
    # receipts and acquires its exclusive claim before any inference request.
    return subprocess.run(  # noqa: S603 -- fixed reviewed supervisor, no shell or retries
        [
            sys.executable,
            "-B",
            str(launcher),
            "--start-record",
            str(start_record),
            "--start-record-sha256",
            start_record_sha256,
        ],
        check=False,
    ).returncode
