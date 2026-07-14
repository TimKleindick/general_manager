from __future__ import annotations

import io
import sys
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest


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


def _write_wheel(
    dist_dir: Path,
    version: str,
    members: frozenset[str] = REQUIRED_MEMBERS,
    *,
    suffix: str = "",
) -> None:
    wheel = dist_dir / f"generalmanager-{version}{suffix}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for member in members:
            archive.writestr(member, "test data")


def _write_sdist(
    dist_dir: Path,
    version: str,
    members: frozenset[str] = REQUIRED_MEMBERS,
    *,
    suffix: str = "",
) -> None:
    root = f"generalmanager-{version}{suffix}"
    sdist = dist_dir / f"{root}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for member in members:
            contents = b"test data"
            info = tarfile.TarInfo(f"{root}/{member}")
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))


def _validator() -> Callable[[Path], None]:
    from scripts.validate_distribution import validate_archives

    return validate_archives


def test_accepts_complete_archives_with_matching_versions(tmp_path: Path) -> None:
    _write_wheel(tmp_path, "1.2.3")
    _write_sdist(tmp_path, "1.2.3")

    _validator()(tmp_path)


def test_archives_cli_validates_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_wheel(tmp_path, "1.2.3")
    _write_sdist(tmp_path, "1.2.3")
    monkeypatch.setattr(
        sys, "argv", ["validate_distribution.py", "archives", str(tmp_path)]
    )
    from scripts import validate_distribution

    validate_distribution.main()


def test_rejects_archive_missing_required_dataset(tmp_path: Path) -> None:
    missing = "general_manager/chat/evals/datasets/multi_hop.yaml"
    _write_wheel(tmp_path, "1.2.3", REQUIRED_MEMBERS - {missing})
    _write_sdist(tmp_path, "1.2.3")

    with pytest.raises(ValueError, match=missing):
        _validator()(tmp_path)


def test_rejects_mismatched_archive_versions(tmp_path: Path) -> None:
    _write_wheel(tmp_path, "1.2.3")
    _write_sdist(tmp_path, "1.2.4")

    with pytest.raises(ValueError, match=r"versions.*1\.2\.3.*1\.2\.4"):
        _validator()(tmp_path)


@pytest.mark.parametrize(
    ("extra_archive", "expected_error"),
    [
        (lambda path: _write_wheel(path, "1.2.3", suffix=".post1"), "wheel"),
        (lambda path: _write_sdist(path, "1.2.3", suffix=".post1"), "sdist"),
    ],
)
def test_rejects_extra_archives(
    tmp_path: Path,
    extra_archive: Callable[[Path], None],
    expected_error: str,
) -> None:
    _write_wheel(tmp_path, "1.2.3")
    _write_sdist(tmp_path, "1.2.3")
    extra_archive(tmp_path)

    with pytest.raises(ValueError, match=rf"exactly one {expected_error}"):
        _validator()(tmp_path)
