"""Judge: tool call sequence and argument validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSequenceScore:
    """Result of tool sequence judgment."""

    passed: bool
    expected: list[dict[str, Any]]
    actual: list[dict[str, Any]]
    mismatches: list[str] = field(default_factory=list)


def judge_tool_sequence(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> ToolSequenceScore:
    """Check that expected tool calls appear as an ordered subsequence of actual calls.

    Each expected entry must have a ``name`` key. Optional ``args_contain`` is a
    dict of key/value pairs that must appear in the matching actual call's args.

    Returns a binary pass/fail score.
    """
    if not expected:
        return ToolSequenceScore(passed=True, expected=expected, actual=actual)

    mismatches: list[str] = []
    actual_idx = 0
    for exp in expected:
        exp_name = _tool_kind(str(exp.get("name", "")))
        args_contain = _as_dict(exp.get("args_contain"))
        matched = False
        candidate_mismatches: list[str] = []
        while actual_idx < len(actual):
            act = _normalized_tool_call(actual[actual_idx])
            actual_idx += 1
            if act.get("name") != exp_name:
                continue
            act_args = _as_dict(act.get("args"))
            candidate_mismatches = _arg_mismatches(exp_name, args_contain, act_args)
            if candidate_mismatches:
                continue
            matched = True
            break
        if not matched:
            if candidate_mismatches:
                mismatches.extend(candidate_mismatches)
            else:
                mismatches.append(f"Expected tool '{exp_name}' not found in sequence")

    passed = len(mismatches) == 0
    return ToolSequenceScore(
        passed=passed, expected=expected, actual=actual, mismatches=mismatches
    )


def _normalized_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    name = str(call.get("name", ""))
    normalized_name = _tool_kind(name)
    args = _as_dict(call.get("args")).copy()
    if normalized_name == "query" and name.startswith("query_"):
        args["manager"] = name.removeprefix("query_")
    return {"name": normalized_name, "args": args}


def _tool_kind(name: str) -> str:
    if name.startswith("query_"):
        return "query"
    return name


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arg_mismatches(
    tool_name: str,
    args_contain: dict[str, Any],
    actual_args: dict[str, Any],
) -> list[str]:
    mismatches: list[str] = []
    for key, expected in args_contain.items():
        if key not in actual_args:
            mismatches.append(f"Tool '{tool_name}': missing arg '{key}'")
            continue
        actual = actual_args[key]
        if not _arg_matches(actual, expected, key=key):
            mismatches.append(
                f"Tool '{tool_name}': arg '{key}' expected {expected!r}, got {actual!r}"
            )
    return mismatches


def _arg_matches(actual: Any, expected: Any, *, key: str) -> bool:
    if key == "manager" and isinstance(actual, str) and isinstance(expected, str):
        return actual.casefold() == expected.casefold()
    return bool(actual == expected)
