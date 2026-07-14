"""Validate GeneralManager distribution artifacts."""

from __future__ import annotations

import argparse
import re
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
    r"^generalmanager-(?P<version>[^-]+)-[^-]+-[^-]+-[^-]+\.whl$",
    re.IGNORECASE,
)
_SDIST_FILENAME = re.compile(
    r"^generalmanager-(?P<version>[^-]+)\.tar\.gz$",
    re.IGNORECASE,
)


def _single_archive(dist_dir: Path, pattern: str, label: str) -> Path:
    archives = sorted(dist_dir.glob(pattern))
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


def _sdist_members(sdist: Path) -> set[str]:
    with tarfile.open(sdist, "r:gz") as archive:
        members: set[str] = set()
        for member in archive.getmembers():
            parts = PurePosixPath(member.name).parts
            if len(parts) > 1:
                members.add("/".join(parts[1:]))
        return members


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

    with zipfile.ZipFile(wheel) as archive:
        _require_members(wheel, set(archive.namelist()))
    _require_members(sdist, _sdist_members(sdist))


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
