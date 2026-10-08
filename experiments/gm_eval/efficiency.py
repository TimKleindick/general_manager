"""Matched empirical overhead comparisons, separate from correctness scoring."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any

from .reporting import aggregate_calls

METRICS = (
    "provider_requests",
    "planning_requests",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "turn_latency_seconds",
    "strong_model_requests",
)


def measure_run(
    run: Mapping[str, Any], *, unnecessary_clarification: bool | None = None
) -> dict[str, Any]:
    """Measure actual calls; clarification quality requires separate adjudication."""
    calls = run.get("trace", {}).get("provider_calls", [])
    turns = run.get("turns", [])
    normalized = [
        {
            "kind": "provider",
            "model": call.get("model"),
            "usage": call.get("reported_usage"),
        }
        for call in calls
    ]
    usage = aggregate_calls(normalized)
    return {
        "provider_requests": sum(call.get("request_count", 0) for call in calls),
        "planning_requests": sum(
            call.get("request_count", 0)
            for call in calls
            if call.get("role") == "planner"
        ),
        "tool_calls": sum(len(turn.get("tool_calls", [])) for turn in turns),
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "reasoning_tokens": usage["reasoning_tokens"],
        "usage_coverage": usage["usage_coverage"],
        "turn_latency_seconds": sum(turn["latency_seconds"] for turn in turns)
        if all(isinstance(turn.get("latency_seconds"), (int, float)) for turn in turns)
        else None,
        "strong_model_requests": sum(
            call.get("request_count", 0) for call in calls if call.get("strong")
        ),
        "unnecessary_clarification": unnecessary_clarification,
        "estimated_cost_usd": usage["estimated_cost_usd"],
        "model_performance_measured": run.get("model_performance_measured", False),
        "interpretation": "Measurements do not establish task correctness. No universal request, token or latency budget is applied.",
    }


def compare_to_reference(
    candidate: Mapping[str, Any],
    references: Sequence[Mapping[str, Any]],
    *,
    axis: str = "profile",
) -> dict[str, Any]:
    """Compare only correctly answered, matched runs; expose repetition counts.

    A metric above the observed range is a descriptive signal for review, not
    a statistical claim or an automatic task failure. Scale comparisons hold
    the profile fixed; profile comparisons hold the manager count fixed.
    """
    if axis not in {"profile", "scale"}:
        message = "comparison axis must be profile or scale"
        raise ValueError(message)
    reasons: list[str] = []
    if not references:
        reasons.append("no empirical reference runs")
    participants = [candidate, *references]
    for record in participants:
        if record.get("task_quality_status") != "passed":
            reasons.append("a run has no passing task-quality adjudication")
        if record.get("mode") != "live":
            reasons.append("offline scripts cannot establish model efficiency")
    fields = [
        "core_id",
        "snapshot",
        "variant",
        "seed",
        "prompt_sha256",
        "reference_version",
        "fixture_sha256",
        "source_sha256",
    ]
    fields.append("manager_count" if axis == "profile" else "profile")
    for field in fields:
        if field not in candidate or candidate[field] is None:
            reasons.append(f"missing comparison field: {field}")
        elif any(reference.get(field) != candidate[field] for reference in references):
            reasons.append(f"mismatched comparison field: {field}")
    if reasons:
        return {
            "comparable": False,
            "reasons": sorted(set(reasons)),
            "reference_repetitions": len(references),
            "metrics": {},
            "universal_budget_applied": False,
        }
    metrics: dict[str, Any] = {}
    for name in METRICS:
        value = candidate.get("measurements", {}).get(name)
        observed = [
            reference.get("measurements", {}).get(name) for reference in references
        ]
        if not _number(value) or not all(_number(item) for item in observed):
            metrics[name] = {
                "candidate": value,
                "reference_median": None,
                "delta": None,
                "ratio": None,
                "above_observed_reference": None,
                "new_activity": None,
            }
            continue
        center = median(observed)
        metrics[name] = {
            "candidate": value,
            "reference_median": center,
            "reference_min": min(observed),
            "reference_max": max(observed),
            "delta": value - center,
            "ratio": value / center if center else None,
            "above_observed_reference": value > max(observed),
            "new_activity": center == 0 and value > 0,
        }
    return {
        "comparable": True,
        "axis": axis,
        "reference_repetitions": len(references),
        "metrics": metrics,
        "unnecessary_clarification": candidate.get("measurements", {}).get(
            "unnecessary_clarification"
        ),
        "universal_budget_applied": False,
        "interpretation": "Paired descriptive comparison of correct runs. Above the observed reference range is a review signal, not a universal budget or confidence interval.",
    }


def _number(value: Any) -> bool:
    import math

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )
