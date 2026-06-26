"""Stable fingerprints for chat readiness artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_hash(payload: Any, *, length: int = 16) -> str:
    """Return a short deterministic hash for JSON-shaped data."""
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:length]


def build_run_fingerprint(
    *,
    provider: str,
    model: str,
    fixture: str,
    datasets: list[str],
    tier: int | None,
    tags: list[str] | None,
    prompt: str,
    tool_definitions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build metadata for the prompt/tool contract evaluated in this run."""
    tags_list = sorted(tags or [])
    dataset_list = sorted(datasets)
    prompt_hash = stable_hash(prompt)
    tool_hash = stable_hash(tool_definitions)
    run_material = {
        "provider": provider,
        "model": model,
        "fixture": fixture,
        "datasets": dataset_list,
        "tier": tier,
        "tags": tags_list,
        "prompt_hash": prompt_hash,
        "tool_hash": tool_hash,
    }
    return {
        **run_material,
        "run_hash": stable_hash(run_material),
    }
