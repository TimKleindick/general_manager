from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "tests" / "snapshots" / "public_api_exports.json"
DOCS_ROOT = ROOT / "docs"
ADDITIONAL_PUBLIC_MODULES = (
    "general_manager.workflow",
    "general_manager.metrics",
    "general_manager.seeding",
)
DOC_PATHS = [
    ROOT / "README.md",
    *[
        path
        for path in DOCS_ROOT.rglob("*.md")
        if not path.relative_to(DOCS_ROOT).as_posix().startswith("superpowers/")
    ],
]


def _docs_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in DOC_PATHS)


def _public_exports() -> dict[str, list[str]]:
    snapshot_exports = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    exports = {
        module_name: list(module_exports)
        for module_name, module_exports in snapshot_exports.items()
    }
    for module_name in ADDITIONAL_PUBLIC_MODULES:
        module = import_module(module_name)
        exports[module_name] = list(module.__all__)
    return exports


def test_unpublished_superpowers_docs_are_excluded_from_docs_corpus() -> None:
    assert not any(
        path.relative_to(DOCS_ROOT).as_posix().startswith("superpowers/")
        for path in DOC_PATHS
        if path.is_relative_to(DOCS_ROOT)
    )


def test_every_public_export_is_mentioned_in_documentation() -> None:
    exports = _public_exports()
    docs_text = _docs_text()

    missing: dict[str, list[str]] = {}
    for module_name, module_exports in exports.items():
        missing_names = [
            export_name
            for export_name in module_exports
            if export_name not in docs_text
        ]
        if missing_names:
            missing[module_name] = missing_names

    assert missing == {}
