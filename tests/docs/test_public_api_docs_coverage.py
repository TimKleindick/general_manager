from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "tests" / "snapshots" / "public_api_exports.json"
DOC_PATHS = [ROOT / "README.md", *(ROOT / "docs").rglob("*.md")]


def _docs_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in DOC_PATHS)


def test_every_public_export_is_mentioned_in_documentation() -> None:
    exports = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
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
