"""Validate GeneralManager distribution artifacts."""

from __future__ import annotations

import argparse
import re
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


REQUIRED_MEMBERS = frozenset(
    {
        "general_manager/py.typed",
        "general_manager/migrations/__init__.py",
        "general_manager/chat/evals/datasets/basic_queries.yaml",
        "general_manager/chat/evals/datasets/demo_readiness.yaml",
        "general_manager/chat/evals/datasets/edge_cases.yaml",
        "general_manager/chat/evals/datasets/follow_ups.yaml",
        "general_manager/chat/evals/datasets/large_schema.yaml",
        "general_manager/chat/evals/datasets/multi_hop.yaml",
    }
)

_WHEEL_FILENAME = re.compile(
    r"^generalmanager-(?P<version>[^-]+)(?:-[0-9][^-]*)?"
    r"-[^-]+-[^-]+-[^-]+\.whl$",
    re.IGNORECASE,
)
_SDIST_FILENAME = re.compile(
    r"^generalmanager-(?P<version>[^-]+)\.tar\.gz$",
    re.IGNORECASE,
)


def _single_archive(dist_dir: Path, pattern: str, label: str) -> Path:
    try:
        archives = sorted(dist_dir.glob(pattern))
    except OSError as exc:
        message = f"Could not inspect distribution directory {dist_dir}: {exc}"
        raise ValueError(message) from exc
    if len(archives) != 1:
        names = ", ".join(archive.name for archive in archives) or "none"
        message = (
            f"Expected exactly one {label} in {dist_dir}, found {len(archives)}: "
            f"{names}"
        )
        raise ValueError(message)
    return archives[0]


def _archive_version(archive: Path, pattern: re.Pattern[str], label: str) -> str:
    match = pattern.fullmatch(archive.name)
    if match is None:
        message = f"Unexpected GeneralManager {label} filename: {archive.name}"
        raise ValueError(message)
    return match.group("version")


def _require_members(archive: Path, members: set[str]) -> None:
    missing = sorted(REQUIRED_MEMBERS - members)
    if missing:
        message = f"{archive.name} is missing required files: {', '.join(missing)}"
        raise ValueError(message)


def _member_parts(archive: Path, member_name: str) -> tuple[str, ...]:
    path = PurePosixPath(member_name)
    if "\\" in member_name or path.is_absolute() or ".." in path.parts:
        message = f"Unsafe archive member path in {archive.name}: {member_name}"
        raise ValueError(message)
    return path.parts


def _wheel_members(wheel: Path) -> set[str]:
    try:
        with zipfile.ZipFile(wheel) as archive:
            members: set[str] = set()
            for member in archive.infolist():
                _member_parts(wheel, member.filename)
                file_type = stat.S_IFMT(member.external_attr >> 16)
                if file_type not in (0, stat.S_IFREG):
                    message = (
                        f"Non-regular wheel member in {wheel.name}: {member.filename}"
                    )
                    raise ValueError(message)
                if not member.is_dir():
                    members.add(member.filename)
            return members
    except (OSError, zipfile.BadZipFile) as exc:
        message = f"Could not inspect wheel {wheel.name}: {exc}"
        raise ValueError(message) from exc


def _sdist_members(sdist: Path, expected_root: str) -> set[str]:
    try:
        with tarfile.open(sdist, "r:gz") as archive:
            members: set[str] = set()
            for member in archive.getmembers():
                parts = _member_parts(sdist, member.name)
                if not parts or parts[0] != expected_root:
                    message = (
                        f"{sdist.name} member {member.name!r} is outside expected "
                        f"top-level root {expected_root!r}"
                    )
                    raise ValueError(message)
                if member.isfile() and len(parts) > 1:
                    members.add("/".join(parts[1:]))
            return members
    except (OSError, tarfile.TarError) as exc:
        message = f"Could not inspect sdist {sdist.name}: {exc}"
        raise ValueError(message) from exc


def validate_archives(dist_dir: Path) -> None:
    """Validate the wheel and source archive in ``dist_dir``."""
    wheel = _single_archive(dist_dir, "*.whl", "wheel")
    sdist = _single_archive(dist_dir, "*.tar.gz", "sdist")

    wheel_version = _archive_version(wheel, _WHEEL_FILENAME, "wheel")
    sdist_version = _archive_version(sdist, _SDIST_FILENAME, "sdist")
    if wheel_version != sdist_version:
        message = (
            "Archive versions do not match: "
            f"wheel {wheel_version}; sdist {sdist_version}"
        )
        raise ValueError(message)

    _require_members(wheel, _wheel_members(wheel))
    expected_sdist_root = sdist.name.removesuffix(".tar.gz")
    _require_members(sdist, _sdist_members(sdist, expected_sdist_root))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    archives_parser = subparsers.add_parser(
        "archives", help="Validate wheel and sdist archives"
    )
    archives_parser.add_argument("dist_dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.command == "archives":
        validate_archives(args.dist_dir)


if __name__ == "__main__":
    main()
