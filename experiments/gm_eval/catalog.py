"""Frozen, reviewed conversations; this module never rewrites the reference."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("catalog.json")
ORIGINAL_REVIEWED_SHA256 = (
    "972636e1e2a7ebebdab2fc03d7d17fac73cd60c6c6b8d6c5857a8dc4452b4f6f"
)
REVIEWED_SHA256 = "d105613e4560b63e6bf69181d61ce395a28feb82f91b51b6afa6c30eed0b8c11"
REFERENCE_VERSION = "1.10"
SCALES = (5, 10, 50, 100, 250)
DIMENSIONS = ("D", "I", "R", "C", "Q", "A")
CONTRACTS = frozenset(
    {
        "steel_inventory_scope",
        "active_customers",
        "ongoing_projects",
        "project_costs_wbs",
        "material_density",
        "project_costs_project",
        "recent_shipments",
        "actual_plan",
        "ambiguous_customer",
        "both_materials",
        "brass",
        "clarify_actuals",
        "clarify_development",
        "clarify_importance",
        "copper_projects",
        "customer",
        "cycle",
        "disconnected",
        "empty",
        "final",
        "follow_filter",
        "forecast",
        "forecast_boreal",
        "fx_rank",
        "insufficient",
        "material",
        "metric_correction",
        "missing_revenue",
        "mixed",
        "no_shipments",
        "owner_billto",
        "plan",
        "projects",
        "provenance",
        "rank_revenue",
        "recommend",
        "returns",
        "revenue",
        "shared_material",
        "shipments",
        "supplier_path",
        "three_turn",
        "top_material",
        "weight",
        "zero_missing",
    }
)
TREND_CONTRACTS = frozenset(
    {
        "clarify_development",
        "plan",
        "clarify_actuals",
        "actual_plan",
        "forecast",
        "forecast_boreal",
        "insufficient",
        "recommend",
        "metric_correction",
        "final",
    }
)
VARIANTS = frozenset(
    {
        "project_costs",
        "recent_shipments",
        "base",
        "duplicate_customer",
        "date_edges",
        "weight",
        "returns",
        "fx",
        "missing_revenue",
        "page2",
        "both",
        "no_shipments",
        "supplier",
        "owner_billto",
        "cycle",
        "disconnected",
        "missing_history",
    }
)


def catalog_sha256() -> str:
    """Fingerprint the original bytes, including the reviewed expectation prose."""
    return sha256(CATALOG_PATH.read_bytes()).hexdigest()


def reference_corrections() -> list[dict[str, Any]]:
    """Read the versioned, authorized change log without changing the catalog."""
    metadata = json.loads(
        CATALOG_PATH.with_name("catalog_corrections.json").read_text(encoding="utf-8")
    )
    if (
        metadata["original_catalog_sha256"] != ORIGINAL_REVIEWED_SHA256
        or metadata["catalog_sha256"] != REVIEWED_SHA256
    ):
        message = "Reference correction fingerprints do not match the frozen catalog"
        raise ValueError(message)
    corrections: list[dict[str, Any]] = metadata["corrections"]
    return corrections


def load_catalog() -> list[dict[str, Any]]:
    """Return fresh case dictionaries after validating the immutable reference."""
    if catalog_sha256() != REVIEWED_SHA256:
        message = "The reviewed catalog changed; version its reference before use"
        raise ValueError(message)
    cases: list[dict[str, Any]] = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    validate_catalog(cases)
    return cases


def validate_catalog(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate count, language, complete paired cores and all explicit contracts."""
    errors: list[str] = []
    identifiers = [row.get("id") for row in cases]
    if identifiers != [f"E{number:03}" for number in range(1, 122)]:
        errors.append("exactly ordered unique E001..E121 IDs are required")
    scales = Counter(row.get("managers") for row in cases)
    languages = Counter(row.get("language") for row in cases)
    if scales != {5: 20, 10: 23, 50: 24, 100: 26, 250: 28}:
        errors.append("manager-scale counts differ from the reviewed catalog")
    if languages != {"DE": 41, "FR": 40, "EN": 40}:
        errors.append("language counts must be DE=41, FR=40, EN=40")
    turns = 0
    for row in cases:
        label = str(row.get("id", "unknown"))
        prompts = row.get("turns")
        if (
            not isinstance(prompts, list)
            or not prompts
            or not all(isinstance(prompt, str) and prompt.strip() for prompt in prompts)
        ):
            errors.append(f"{label}: nonempty natural-language turns are required")
        else:
            turns += len(prompts)
        contract = row.get("contract")
        if contract not in CONTRACTS:
            errors.append(f"{label}: unknown contract")
        if row.get("fixture_variant") not in VARIANTS:
            errors.append(f"{label}: unknown fixture variant")
        if row.get("snapshot") != ("TREND" if contract in TREND_CONTRACTS else "RANK"):
            errors.append(f"{label}: inconsistent source snapshot")
        if row.get("scoring_dimensions") != list(DIMENSIONS):
            errors.append(f"{label}: all six separate scoring dimensions required")
        if not row.get("expected") or not row.get("focus"):
            errors.append(f"{label}: missing reviewed contract/focus")
    if turns != 149:
        errors.append("exactly 149 turns are required")
    cores = [row for row in cases if row.get("core_id") is not None]
    if len(cores) != 75 or {row.get("core_id") for row in cores} != {
        f"K{number:02}" for number in range(1, 16)
    }:
        errors.append(
            "exactly fifteen core intents and seventy-five core instances required"
        )
    paired_fields = (
        "language",
        "turns",
        "contract",
        "focus",
        "fixture_variant",
        "snapshot",
        "expected",
        "scoring_dimensions",
    )
    for number in range(1, 16):
        key = f"K{number:02}"
        group = [row for row in cores if row.get("core_id") == key]
        if Counter(row.get("managers") for row in group) != Counter(SCALES):
            errors.append(f"{key}: one instance at each of the five scales required")
        if group and any(
            any(row.get(field) != group[0].get(field) for field in paired_fields)
            for row in group
        ):
            errors.append(
                f"{key}: core question and gold must be identical across scales"
            )
    if any(
        row.get("managers") not in (5, 10) or row.get("core_id") is not None
        for row in cases[:10]
    ):
        errors.append("the ten introductory cases must use five or ten managers")
    targeted = [row for row in cases[10:] if row.get("core_id") is None]
    if len(targeted) != 36:
        errors.append("exactly thirty-six targeted cases required")
    canonical = (json.dumps(cases, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if sha256(canonical).hexdigest() != REVIEWED_SHA256:
        errors.append("case content differs from the versioned reviewed reference")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "total": len(cases),
        "turns": turns,
        "by_scale": dict(scales),
        "original_catalog_sha256": ORIGINAL_REVIEWED_SHA256,
        "catalog_sha256": catalog_sha256(),
        "reference_corrections": reference_corrections(),
        "original_case_count": 100,
        "reference_version": REFERENCE_VERSION,
        "reference_expansions": json.loads(
            CATALOG_PATH.with_name("catalog_corrections.json").read_text(
                encoding="utf-8"
            )
        )["expansions"],
        "by_language": dict(languages),
        "core_intents": 15,
        "core_instances": len(cores),
        "intro": 10,
        "targeted": 36,
    }
