from __future__ import annotations

import io
import re
import stat
import sys
import tarfile
import tomllib
import zipfile
from collections.abc import Callable, Mapping
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


def test_distribution_metadata_packages_datasets_and_uses_workflow_uploads() -> None:
    pyproject_path = Path(__file__).parents[2] / "pyproject.toml"
    with pyproject_path.open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert package_data["general_manager"] == ["py.typed"]
    assert "datasets/*.yaml" in package_data["general_manager.chat.evals"]

    semantic_release = pyproject["tool"]["semantic_release"]
    assert "upload_to_PyPI" not in semantic_release
    assert "upload_to_release" not in semantic_release


def _write_wheel(
    dist_dir: Path,
    version: str,
    members: frozenset[str] = REQUIRED_MEMBERS,
    *,
    build_tag: str | None = None,
    extra_members: tuple[str, ...] = (),
    member_modes: Mapping[str, int] | None = None,
    suffix: str = "",
    metadata_version: str | None = None,
) -> None:
    build = f"-{build_tag}" if build_tag is not None else ""
    wheel = dist_dir / f"generalmanager-{version}{suffix}{build}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for member in (*members, *extra_members):
            info = zipfile.ZipInfo(member)
            if member_modes is not None and member in member_modes:
                info.create_system = 3
                info.external_attr = member_modes[member] << 16
            archive.writestr(info, "test data")
        archive.writestr(
            f"generalmanager-{version}.dist-info/METADATA",
            "Metadata-Version: 2.4\n"
            "Name: GeneralManager\n"
            f"Version: {metadata_version or version}\n",
        )


def _write_sdist(
    dist_dir: Path,
    version: str,
    members: frozenset[str] = REQUIRED_MEMBERS,
    *,
    directory_members: frozenset[str] = frozenset(),
    extra_members: tuple[str, ...] = (),
    root: str | None = None,
    suffix: str = "",
    metadata_version: str | None = None,
) -> None:
    filename_root = f"generalmanager-{version}{suffix}"
    archive_root = root or filename_root
    sdist = dist_dir / f"{filename_root}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for member in members:
            info = tarfile.TarInfo(f"{archive_root}/{member}")
            if member in directory_members:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
                continue
            contents = b"test data"
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))
        for member in extra_members:
            contents = b"test data"
            info = tarfile.TarInfo(member)
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))
        metadata = (
            "Metadata-Version: 2.4\n"
            "Name: GeneralManager\n"
            f"Version: {metadata_version or version}\n"
        ).encode()
        info = tarfile.TarInfo(f"{archive_root}/PKG-INFO")
        info.size = len(metadata)
        archive.addfile(info, io.BytesIO(metadata))


def _validator() -> Callable[..., None]:
    from scripts.validate_distribution import validate_archives

    return validate_archives


def test_accepts_complete_archives_with_matching_versions(tmp_path: Path) -> None:
    _write_wheel(tmp_path, "1.2.3")
    _write_sdist(tmp_path, "1.2.3")

    _validator()(tmp_path)


def test_accepts_wheel_with_dotted_build_tag(tmp_path: Path) -> None:
    _write_wheel(tmp_path, "1.2.3", build_tag="1.foo")
    _write_sdist(tmp_path, "1.2.3")

    _validator()(tmp_path)


def test_accepts_regular_unix_wheel_members(tmp_path: Path) -> None:
    regular_modes = {member: stat.S_IFREG | 0o644 for member in REQUIRED_MEMBERS}
    _write_wheel(tmp_path, "1.2.3", member_modes=regular_modes)
    _write_sdist(tmp_path, "1.2.3")

    _validator()(tmp_path)


def test_archives_cli_validates_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_wheel(tmp_path, "1.2.3")
    _write_sdist(tmp_path, "1.2.3")
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_distribution.py", "archives", str(tmp_path), "1.2.3"],
    )
    from scripts import validate_distribution

    validate_distribution.main()


def test_archives_cli_rejects_metadata_not_bound_to_expected_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_wheel(tmp_path, "1.2.3", metadata_version="9.9.9")
    _write_sdist(tmp_path, "1.2.3")
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_distribution.py", "archives", str(tmp_path), "1.2.3"],
    )
    from scripts import validate_distribution

    with pytest.raises(ValueError, match="wheel METADATA version"):
        validate_distribution.main()


@pytest.mark.parametrize(
    ("wheel_version", "wheel_metadata", "sdist_version", "sdist_metadata", "field"),
    [
        ("1.2.2", "1.2.3", "1.2.3", "1.2.3", "wheel filename"),
        ("1.2.3", "1.2.3", "1.2.2", "1.2.3", "sdist filename"),
        ("1.2.3", "1.2.2", "1.2.3", "1.2.3", "wheel METADATA"),
        ("1.2.3", "1.2.3", "1.2.3", "1.2.2", "sdist PKG-INFO"),
    ],
)
def test_expected_version_binds_archive_filenames_and_metadata(
    tmp_path: Path,
    wheel_version: str,
    wheel_metadata: str,
    sdist_version: str,
    sdist_metadata: str,
    field: str,
) -> None:
    _write_wheel(tmp_path, wheel_version, metadata_version=wheel_metadata)
    _write_sdist(tmp_path, sdist_version, metadata_version=sdist_metadata)

    with pytest.raises(
        ValueError,
        match=rf"{field} version.*1\.2\.2.*expected.*1\.2\.3",
    ):
        _validator()(tmp_path, expected_version="1.2.3")


def test_rejects_duplicate_sdist_pkg_info(tmp_path: Path) -> None:
    version = "1.2.3"
    _write_wheel(tmp_path, version)
    _write_sdist(
        tmp_path,
        version,
        extra_members=(f"generalmanager-{version}/PKG-INFO",),
    )

    with pytest.raises(ValueError, match="exactly one sdist PKG-INFO"):
        _validator()(tmp_path, expected_version=version)


@pytest.mark.parametrize("archive_kind", ["wheel", "sdist"])
def test_rejects_archive_missing_required_dataset(
    tmp_path: Path, archive_kind: str
) -> None:
    missing = "general_manager/chat/evals/datasets/multi_hop.yaml"
    wheel_members = (
        REQUIRED_MEMBERS - {missing} if archive_kind == "wheel" else REQUIRED_MEMBERS
    )
    sdist_members = (
        REQUIRED_MEMBERS - {missing} if archive_kind == "sdist" else REQUIRED_MEMBERS
    )
    _write_wheel(tmp_path, "1.2.3", wheel_members)
    _write_sdist(tmp_path, "1.2.3", sdist_members)

    with pytest.raises(ValueError, match=re.escape(missing)):
        _validator()(tmp_path)


def test_rejects_sdist_with_unrelated_top_level_root(tmp_path: Path) -> None:
    _write_wheel(tmp_path, "1.2.3")
    _write_sdist(tmp_path, "1.2.3", root="unrelated-1.2.3")

    with pytest.raises(ValueError, match="top-level root"):
        _validator()(tmp_path)


def test_rejects_sdist_directory_in_place_of_required_file(tmp_path: Path) -> None:
    required_file = "general_manager/chat/evals/datasets/multi_hop.yaml"
    _write_wheel(tmp_path, "1.2.3")
    _write_sdist(
        tmp_path,
        "1.2.3",
        directory_members=frozenset({required_file}),
    )

    with pytest.raises(ValueError, match=re.escape(required_file)):
        _validator()(tmp_path)


@pytest.mark.parametrize(
    ("archive_kind", "unsafe_member"),
    [
        ("wheel", "/escape.txt"),
        ("wheel", "../escape.txt"),
        ("wheel", r"general_manager\escape.txt"),
        ("sdist", "/escape.txt"),
        ("sdist", "generalmanager-1.2.3/../escape.txt"),
        ("sdist", r"generalmanager-1.2.3/general_manager\escape.txt"),
    ],
)
def test_rejects_unsafe_archive_member_paths(
    tmp_path: Path, archive_kind: str, unsafe_member: str
) -> None:
    wheel_extras = (unsafe_member,) if archive_kind == "wheel" else ()
    sdist_extras = (unsafe_member,) if archive_kind == "sdist" else ()
    _write_wheel(tmp_path, "1.2.3", extra_members=wheel_extras)
    _write_sdist(tmp_path, "1.2.3", extra_members=sdist_extras)

    with pytest.raises(ValueError, match="Unsafe archive member path"):
        _validator()(tmp_path)


@pytest.mark.parametrize(
    "member_type",
    [stat.S_IFLNK, stat.S_IFDIR],
    ids=["symlink", "disguised-directory"],
)
def test_rejects_non_regular_required_wheel_member(
    tmp_path: Path, member_type: int
) -> None:
    required_file = "general_manager/py.typed"
    _write_wheel(
        tmp_path,
        "1.2.3",
        member_modes={required_file: member_type | 0o755},
    )
    _write_sdist(tmp_path, "1.2.3")

    with pytest.raises(
        ValueError, match=rf"Non-regular wheel member.*{re.escape(required_file)}"
    ):
        _validator()(tmp_path)


@pytest.mark.parametrize(
    ("archive_kind", "cause_type"),
    [("wheel", zipfile.BadZipFile), ("sdist", tarfile.ReadError)],
)
def test_wraps_corrupt_archive_errors(
    tmp_path: Path, archive_kind: str, cause_type: type[Exception]
) -> None:
    if archive_kind == "wheel":
        (tmp_path / "generalmanager-1.2.3-py3-none-any.whl").write_bytes(b"invalid")
        _write_sdist(tmp_path, "1.2.3")
    else:
        _write_wheel(tmp_path, "1.2.3")
        (tmp_path / "generalmanager-1.2.3.tar.gz").write_bytes(b"invalid")

    with pytest.raises(ValueError, match=rf"Could not inspect {archive_kind}") as error:
        _validator()(tmp_path)

    assert isinstance(error.value.__cause__, cause_type)


@pytest.mark.parametrize("archive_kind", ["wheel", "sdist"])
def test_wraps_archive_open_errors(tmp_path: Path, archive_kind: str) -> None:
    if archive_kind == "wheel":
        (tmp_path / "generalmanager-1.2.3-py3-none-any.whl").mkdir()
        _write_sdist(tmp_path, "1.2.3")
    else:
        _write_wheel(tmp_path, "1.2.3")
        (tmp_path / "generalmanager-1.2.3.tar.gz").mkdir()

    with pytest.raises(ValueError, match=rf"Could not inspect {archive_kind}") as error:
        _validator()(tmp_path)

    assert isinstance(error.value.__cause__, OSError)


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
