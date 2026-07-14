"""Verify chat eval datasets in distributions and an installed wheel."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DATASETS = ROOT / "src/general_manager/chat/evals/datasets"
ARCHIVE_MARKER = "general_manager/chat/evals/datasets/"


def _archive_dataset_names(artifact: Path) -> set[str]:
    if artifact.name.endswith(".whl"):
        with zipfile.ZipFile(artifact) as archive:
            members = archive.namelist()
    elif artifact.name.endswith(".tar.gz"):
        with tarfile.open(artifact, "r:gz") as archive:
            members = archive.getnames()
    else:
        msg = f"Unsupported distribution artifact: {artifact}"
        raise AssertionError(msg)

    return {
        Path(member).name
        for member in members
        if ARCHIVE_MARKER in member and member.endswith(".yaml")
    }


def main(arguments: list[str]) -> None:
    assert len(arguments) == 2, "Expected exactly one wheel and one sdist"

    artifacts = [Path(argument).resolve() for argument in arguments]
    wheels = [artifact for artifact in artifacts if artifact.name.endswith(".whl")]
    sdists = [artifact for artifact in artifacts if artifact.name.endswith(".tar.gz")]
    assert len(wheels) == 1, "Expected exactly one wheel"
    assert len(sdists) == 1, "Expected exactly one sdist"

    source_names = {path.name for path in SOURCE_DATASETS.glob("*.yaml")}
    assert source_names, "Expected at least one source chat eval dataset"
    assert _archive_dataset_names(wheels[0]) == source_names
    assert _archive_dataset_names(sdists[0]) == source_names

    from django.conf import settings

    settings.configure(
        SECRET_KEY="chat-eval-distribution-smoke",  # noqa: S106
        INSTALLED_APPS=[],
    )

    import django

    django.setup()

    import general_manager
    from general_manager.chat.evals.runner import list_datasets, load_dataset

    installed_package = Path(general_manager.__file__).resolve()
    assert not installed_package.is_relative_to(ROOT / "src")
    assert set(list_datasets()) == {Path(name).stem for name in source_names}
    assert load_dataset("basic_queries")


if __name__ == "__main__":
    main(sys.argv[1:])
