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
        exp_name = exp.get("name", "")
        args_contain = exp.get("args_contain", {})
        matched = False
        while actual_idx < len(actual):
            act = actual[actual_idx]
            actual_idx += 1
            if act.get("name") != exp_name:
                continue
            # Name matches — check args_contain
            act_args = act.get("args", {})
            arg_ok = True
            for key, value in args_contain.items():
                if key not in act_args:
                    mismatches.append(f"Tool '{exp_name}': missing arg '{key}'")
                    arg_ok = False
                elif act_args[key] != value:
                    mismatches.append(
                        f"Tool '{exp_name}': arg '{key}' expected "
                        f"{value!r}, got {act_args[key]!r}"
                    )
                    arg_ok = False
            if not arg_ok:
                continue
            matched = True
            break
        if not matched:
            mismatches.append(f"Expected tool '{exp_name}' not found in sequence")

    passed = len(mismatches) == 0
    return ToolSequenceScore(
        passed=passed, expected=expected, actual=actual, mismatches=mismatches
    )
