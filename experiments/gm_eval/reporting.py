"""Truthful coverage and telemetry for the private experimental evaluation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

DIMENSIONS = ("D", "I", "R", "C", "Q", "A")
STATUSES = frozenset(
    {
        "fixture_invalid",
        "interface_capability_gap",
        "harness_failure",
        "transport_failure",
        "judge_failure",
        "budget_exhausted",
        "model_task_failure",
        "passed",
        "infrastructure_check",
        "unscored",
    }
)


def _dimension_status(value: Any) -> Any:
    return value.get("status") if isinstance(value, Mapping) else value


def _report_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "status": row.get("status", row.get("primary_failure", "unscored")),
        "dimensions": {
            name: _dimension_status(value)
            for name, value in row.get("dimensions", {}).items()
        },
    }


def case_result(
    turn_scores: Iterable[Mapping[str, Any]], *, expected_turns: int
) -> dict[str, Any]:
    """Aggregate actual scorer turns; missing follow-ups never become case passes."""
    from .scoring import FAILURE_HIERARCHY

    turns = [_report_row(turn) for turn in turn_scores]
    indices = [turn["turn_index"] for turn in turns]
    if (
        not turns
        or expected_turns < 1
        or len({turn["case_id"] for turn in turns}) != 1
        or len(indices) != len(set(indices))
        or any(
            type(index) is not int or not 0 <= index < expected_turns
            for index in indices
        )
        or any(turn["status"] not in STATUSES for turn in turns)
    ):
        message = "invalid case turn scores"
        raise ValueError(message)
    missing = sorted(set(range(expected_turns)) - set(indices))
    statuses = {turn["status"] for turn in turns}
    dimensions = {}
    for name in DIMENSIONS:
        values = {turn["dimensions"].get(name, "unscored") for turn in turns}
        dimensions[name] = (
            "fail"
            if "fail" in values or False in values
            else "unscored"
            if missing or not values <= {"pass", "N/A", True}
            else "N/A"
            if values == {"N/A"}
            else "pass"
        )
    primary = next(
        (status for status in FAILURE_HIERARCHY if status in statuses),
        "unscored" if missing or statuses != {"passed"} else "passed",
    )
    return {
        "case_id": turns[0]["case_id"],
        "status": primary,
        "dimensions": dimensions,
        "attempted_turns": len(turns),
        "expected_turns": expected_turns,
        "missing_turns": missing,
        "turn_scores": turns,
    }


def summarize(
    rows: Iterable[Mapping[str, Any]], *, mode: str, catalog_size: int
) -> dict[str, Any]:
    """Keep attempted failures and missing adjudication in the denominator."""
    if mode not in {"offline", "live"}:
        message = "invalid report mode"
        raise ValueError(message)
    records = [_report_row(row) for row in rows]
    ids = [r["case_id"] for r in records]
    if len(ids) != len(set(ids)):
        message = "duplicate case IDs in a single-run report"
        raise ValueError(message)
    if len(records) > catalog_size:
        message = "attempted cases exceed catalog size"
        raise ValueError(message)
    if any(r["status"] not in STATUSES for r in records):
        message = "unknown evaluation status"
        raise ValueError(message)
    counts = Counter(r["status"] for r in records)
    dimensions: dict[str, dict[str, int]] = {}
    for name in DIMENSIONS:
        values = [r.get("dimensions", {}).get(name) for r in records]
        dimensions[name] = {
            "passed": sum(v is True or v == "pass" for v in values),
            "failed": sum(v is False or v == "fail" for v in values),
            "not_applicable": sum(v == "N/A" for v in values),
            "not_scored": sum(
                v not in (True, False, "pass", "fail", "N/A") for v in values
            ),
        }
    assessable = counts["passed"] + counts["model_task_failure"]
    return {
        "mode": mode,
        "model_performance_measured": mode == "live",
        "catalog_cases": catalog_size,
        "attempted_cases": len(records),
        "unattempted_cases": catalog_size - len(records),
        "status_counts": dict(sorted(counts.items())),
        "end_to_end_success_rate": (
            counts["passed"] / len(records) if mode == "live" and records else None
        ),
        "conditional_accuracy": (
            counts["passed"] / assessable if mode == "live" and assessable else None
        ),
        "assessable_cases": assessable if mode == "live" else 0,
        "dimensions": dimensions,
        "interpretation": (
            "Offline infrastructure and oracle checks; no real-model performance measured."
            if mode == "offline"
            else "End-to-end includes every attempted case; conditional accuracy is supplemental."
        ),
    }


def aggregate_calls(
    calls: Iterable[Mapping[str, Any]],
    *,
    strong_models: set[str] | frozenset[str] = frozenset(),
    prices: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Unknown usage/cost stays unknown; reasoning is not billed a second time."""
    records = list(calls)
    eligible = [r for r in records if r.get("kind") in {"provider", "judge"}]
    totals: dict[str, Any] = {
        f"{kind}_calls": sum(r.get("kind") == kind for r in records)
        for kind in ("provider", "tool", "judge")
    }
    reported = sum(isinstance(r.get("usage"), Mapping) for r in eligible)
    totals["usage_coverage"] = {"reported": reported, "eligible": len(eligible)}
    for key in ("input_tokens", "output_tokens", "reasoning_tokens"):
        values = [
            r["usage"].get(key) if isinstance(r.get("usage"), Mapping) else None
            for r in eligible
        ]
        known = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
        if any(v < 0 for v in known):
            message = "negative token usage"
            raise ValueError(message)
        totals[f"reported_{key}"] = sum(known)
        totals[key] = sum(known) if len(known) == len(values) else None
    strong = [r for r in eligible if r.get("model") in strong_models]
    totals["strong_model_calls"] = len(strong)
    totals["estimated_cost_usd"] = _cost(eligible, prices)
    totals["estimated_strong_model_cost_usd"] = _cost(strong, prices)
    totals["price_sources"] = dict(prices or {})
    totals["cost_is_estimate"] = True
    return totals


def _cost(
    calls: list[Mapping[str, Any]],
    prices: Mapping[str, Mapping[str, str]] | None,
) -> str | None:
    if not prices:
        return None
    total = Decimal(0)
    for call in calls:
        price = prices.get(str(call.get("model")))
        usage = call.get("usage")
        if not price or not price.get("source") or not price.get("as_of"):
            return None
        if not isinstance(usage, Mapping):
            return None
        for kind in ("input", "output"):
            tokens = usage.get(f"{kind}_tokens")
            rate = price.get(f"{kind}_per_million_usd")
            if not isinstance(tokens, int) or isinstance(tokens, bool) or rate is None:
                return None
            amount = Decimal(rate)
            if not amount.is_finite() or amount < 0 or tokens < 0:
                message = "invalid price or usage"
                raise ValueError(message)
            total += Decimal(tokens) * amount / Decimal(1000000)
    return format(total.quantize(Decimal(".000001")), "f")
