"""Trace artifacts for chat eval runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EvalTraceWriter:
    """Append deterministic JSONL records for eval case inspection."""

    def __init__(self, path: Path | str, *, append: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not append:
            self.path.write_text("", encoding="utf-8")

    def write_case(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(
            payload, default=str, separators=(",", ":"), sort_keys=True
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"{encoded}\n")
