"""Stable fingerprints for chat readiness artifacts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from itertools import product
from typing import Any, TypeGuard

EVALUATION_CONTRACT = "chat-evaluation/5"
SELECTION_MANIFEST_VERSION = "chat-eval-selection/1"
NATIVE_FINGERPRINT_CONTRACT = "chat-run-fingerprint/2"


def stable_hash(payload: Any, *, length: int = 16) -> str:
    """Return a short deterministic hash for JSON-shaped data."""
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:length]


def selection_manifest_is_valid(value: object) -> TypeGuard[dict[str, Any]]:
    """Check the actual-cohort proof independently of a model-dependent run hash."""
    if (
        not isinstance(value, dict)
        or value.get("version") != SELECTION_MANIFEST_VERSION
    ):
        return False
    cases = value.get("cases")
    if not isinstance(cases, list):
        return False
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"dataset", "name", "case_hash"}:
            return False
        if any(not isinstance(item, str) or not item for item in case.values()):
            return False
        digest = case["case_hash"]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            return False
    return value.get("hash") == stable_hash(cases, length=64)


def build_native_prompt_contract() -> dict[str, Any]:
    """Bind owner-consumed instructions, including conditional native variants.

    Reuse instruction assembly without introducing case/history/evidence data or
    reading original imports that the native owners no longer consume directly.
    """
    from general_manager.chat.planned import (
        planner,
        scheduler,
        schema_projection,
        synthesis,
    )

    return {
        "planner": tuple(
            planner._system_instructions(
                choices=choices, definitions=definitions, correction=correction
            )
            for choices, definitions, correction in product((False, True), repeat=3)
        ),
        "executor": tuple(
            scheduler._executor_instruction(
                deferred=deferred, pending_schemas=pending_schemas
            )
            for deferred, pending_schemas in product((False, True), repeat=2)
        ),
        "synthesis": synthesis._SYNTHESIS_INSTRUCTION,
        "schema_transport": {
            schema_projection.VERSION: schema_projection.INSTRUCTION,
            schema_projection.REFERENCE_VERSION: schema_projection.REFERENCE_INSTRUCTION,
            schema_projection.SYNTHESIS_REFERENCE_VERSION: (
                schema_projection.SYNTHESIS_REFERENCE_INSTRUCTION
            ),
        },
    }


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
    selection_manifest: dict[str, Any] | None = None,
    native_prompt_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build metadata for the prompt/tool contract evaluated in this run."""
    tags_list = sorted(tags or [])
    dataset_list = sorted(datasets)
    prompt_hash = stable_hash(prompt)
    if native_prompt_contract is not None:
        prompt_hash = stable_hash(
            {
                "fingerprint_contract": NATIVE_FINGERPRINT_CONTRACT,
                "prompt": prompt,
                "native_prompt_contract": native_prompt_contract,
            }
        )
    tool_hash = stable_hash(tool_definitions)
    run_material: dict[str, Any] = {
        "evaluation_contract": EVALUATION_CONTRACT,
        "provider": provider,
        "model": model,
        "fixture": fixture,
        "datasets": dataset_list,
        "tier": tier,
        "tags": tags_list,
        "prompt_hash": prompt_hash,
        "tool_hash": tool_hash,
    }
    if native_prompt_contract is not None:
        run_material["fingerprint_contract"] = NATIVE_FINGERPRINT_CONTRACT
    if selection_manifest is not None:
        if not selection_manifest_is_valid(selection_manifest):
            message = "Invalid eval selection manifest."
            raise ValueError(message)
        actual_datasets = sorted(
            {case["dataset"] for case in selection_manifest["cases"]}
        )
        if dataset_list != actual_datasets:
            message = "Datasets do not match the actual eval selection."
            raise ValueError(message)
        run_material["selection_manifest"] = deepcopy(selection_manifest)
    return {
        **run_material,
        "run_hash": stable_hash(run_material),
    }
