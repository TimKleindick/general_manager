"""Judge: query result set accuracy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResultAccuracyScore:
    """Result of result-set accuracy judgment."""

    passed: bool
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)


def _flatten_result_values(results: list[dict[str, Any]]) -> set[str]:
    """Extract all string values from a list of result dicts, lowercased."""
    values: set[str] = set()
    for item in results:
        _collect_values(item, values)
    return values


def _collect_values(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_values(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_values(v, out)
    elif isinstance(obj, str):
        out.add(obj.lower())
    elif obj is not None:
        out.add(str(obj).lower())


def judge_result_accuracy(
    results_contain: list[str],
    results_exclude: list[str],
    actual_results: list[dict[str, Any]],
) -> ResultAccuracyScore:
    """Check that expected values appear (and excluded values do not) in query results.

    Comparison is case-insensitive. Values are extracted recursively from all
    result dict fields.

    Returns a binary pass/fail score.
    """
    flat = _flatten_result_values(actual_results)

    missing = [v for v in results_contain if v.lower() not in flat]
    unexpected = [v for v in results_exclude if v.lower() in flat]

    return ResultAccuracyScore(
        passed=len(missing) == 0 and len(unexpected) == 0,
        missing=missing,
        unexpected=unexpected,
    )
