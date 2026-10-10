"""Exact row-set checks on the final query for a manager within one turn."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from typing import Any


@dataclass(frozen=True)
class ResultSetScore:
    """Only structured failure reasons, never raw returned data."""

    passed: bool
    reason: str | None = None


def _project_fields(value: Any, examples: list[Any]) -> Any:
    """Project nested objects; treat relation lists as unordered multisets."""
    objects = [example for example in examples if isinstance(example, dict)]
    if isinstance(value, dict) and objects:
        keys = {key for example in objects for key in example}
        return {
            key: _project_fields(
                value[key], [example[key] for example in objects if key in example]
            )
            for key in keys
        }
    lists = [example for example in examples if isinstance(example, list)]
    if isinstance(value, list) and lists:
        elements = [item for example in lists for item in example]
        return sorted(
            [_project_fields(item, elements) for item in value],
            key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
        )
    return value


def judge_result_set(
    expected: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> ResultSetScore:
    """Compare projected rows as a multiset, preserving types and multiplicity."""
    manager = expected.get("manager")
    rows = expected.get("rows")
    fields = expected.get("fields")
    if fields is None and isinstance(rows, list) and rows and isinstance(rows[0], dict):
        fields = list(rows[0])
    if (
        not isinstance(manager, str)
        or not manager
        or not isinstance(rows, list)
        or not isinstance(fields, list)
        or not fields
        or any(not isinstance(key, str) or not key for key in fields)
        or len(set(fields)) != len(fields)
        or any(not isinstance(row, dict) or set(row) != set(fields) for row in rows)
    ):
        return ResultSetScore(False, "invalid_result_set_expectation")
    try:
        expected_rows = Counter(
            json.dumps(_project_fields(row, rows), sort_keys=True, ensure_ascii=False)
            for row in rows
        )
    except KeyError:
        return ResultSetScore(False, "invalid_result_set_expectation")
    if len(tool_calls) != len(tool_results):
        return ResultSetScore(False, "unaligned_tool_results")
    result = None
    for call, candidate in zip(tool_calls, tool_results, strict=True):
        name = call.get("name")
        args = call.get("args", {})
        if (
            name == "query"
            and isinstance(args, dict)
            and args.get("manager") == manager
        ) or name == f"query_{manager}":
            result = candidate
    if result is None:
        return ResultSetScore(False, "missing_query_for_turn")
    if not isinstance(result, dict) or result.get("error") or result.get("errors"):
        return ResultSetScore(False, "query_failed")
    actual = result.get("data")
    if not isinstance(actual, list):
        return ResultSetScore(False, "missing_result_rows")
    if any(
        not isinstance(row, dict) or any(key not in row for key in fields)
        for row in actual
    ):
        return ResultSetScore(False, "missing_result_fields")
    try:
        actual_rows = Counter(
            json.dumps(
                _project_fields({key: row[key] for key in fields}, rows),
                sort_keys=True,
                ensure_ascii=False,
            )
            for row in actual
        )
    except KeyError:
        return ResultSetScore(False, "missing_result_fields")
    return ResultSetScore(
        actual_rows == expected_rows,
        None if actual_rows == expected_rows else "result_set_mismatch",
    )
