from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from scripts.validate_distribution import validate_archives


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


def test_accepts_src_layout_source_distribution(tmp_path: Path) -> None:
    version = "1.2.3"
    root = f"generalmanager-{version}"
    with zipfile.ZipFile(tmp_path / f"{root}-py3-none-any.whl", "w") as wheel:
        for member in REQUIRED_MEMBERS:
            wheel.writestr(member, "test data")

    with tarfile.open(tmp_path / f"{root}.tar.gz", "w:gz") as sdist:
        for member in REQUIRED_MEMBERS:
            contents = b"test data"
            info = tarfile.TarInfo(f"{root}/src/{member}")
            info.size = len(contents)
            sdist.addfile(info, io.BytesIO(contents))

    validate_archives(tmp_path)
