"""Judge: product behavior contract for chat evals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from general_manager.chat.evals.judges.result_accuracy import judge_result_accuracy


@dataclass(frozen=True)
class ProductContractScore:
    """Product-contract judgment with hard failures and soft strategy deviations."""

    passed: bool
    category: str
    violations: list[str] = field(default_factory=list)
    strategy_deviations: list[str] = field(default_factory=list)


def judge_product_contract(
    contract: dict[str, Any],
    *,
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    answer_text: str,
) -> ProductContractScore:
    """Score hard product invariants and non-failing strategy guidance."""
    category = str(contract.get("category", "uncategorized"))
    hard = _as_dict(contract.get("hard"))
    strategy = _as_dict(contract.get("strategy"))
    violations: list[str] = []
    strategy_deviations: list[str] = []

    violations.extend(
        _missing_tool_call_messages(
            _as_list(hard.get("required_tool_calls")),
            tool_calls,
            prefix="Required",
        )
    )
    violations.extend(
        _forbidden_tool_messages(_as_list(hard.get("forbidden_tools")), tool_calls)
    )
    violations.extend(
        _result_messages(
            _as_list(hard.get("results_contain")),
            _as_list(hard.get("results_exclude")),
            tool_results,
        )
    )
    violations.extend(
        _answer_messages(
            _as_list(hard.get("answer_contains")),
            _as_list(hard.get("answer_excludes")),
            answer_text,
        )
    )

    strategy_deviations.extend(
        _missing_tool_call_messages(
            _as_list(strategy.get("recommended_tool_calls")),
            tool_calls,
            prefix="Recommended",
        )
    )
    if bool(strategy.get("fail_on_deviation", False)):
        violations.extend(strategy_deviations)

    return ProductContractScore(
        passed=len(violations) == 0,
        category=category,
        violations=violations,
        strategy_deviations=strategy_deviations,
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _missing_tool_call_messages(
    expected: list[Any],
    actual: list[dict[str, Any]],
    *,
    prefix: str,
) -> list[str]:
    messages: list[str] = []
    for item in expected:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        args_contain = _as_dict(item.get("args_contain"))
        if not isinstance(name, str) or not name:
            continue
        if not _has_matching_tool_call(name, args_contain, actual):
            messages.append(f"{prefix} tool call missing: {name}")
    return messages


def _has_matching_tool_call(
    name: str,
    args_contain: dict[str, Any],
    actual: list[dict[str, Any]],
) -> bool:
    for call in actual:
        if call.get("name") != name:
            continue
        args = _as_dict(call.get("args"))
        if all(args.get(key) == value for key, value in args_contain.items()):
            return True
    return False


def _forbidden_tool_messages(
    forbidden_tools: list[Any],
    actual: list[dict[str, Any]],
) -> list[str]:
    forbidden_names = {item for item in forbidden_tools if isinstance(item, str)}
    called = {call.get("name") for call in actual}
    return [
        f"Forbidden tool called: {name}"
        for name in sorted(forbidden_names.intersection(called))
    ]


def _result_messages(
    results_contain: list[Any],
    results_exclude: list[Any],
    tool_results: list[dict[str, Any]],
) -> list[str]:
    expected = [str(item) for item in results_contain]
    excluded = [str(item) for item in results_exclude]
    if not expected and not excluded:
        return []

    flat_results: list[dict[str, Any]] = []
    for result in tool_results:
        if isinstance(result, dict) and isinstance(result.get("data"), list):
            flat_results.extend(result["data"])
        elif isinstance(result, dict):
            flat_results.append(result)

    score = judge_result_accuracy(expected, excluded, flat_results)
    messages = [f"Missing result value: {item}" for item in score.missing]
    messages.extend(f"Unexpected result value: {item}" for item in score.unexpected)
    return messages


def _answer_messages(
    answer_contains: list[Any],
    answer_excludes: list[Any],
    answer_text: str,
) -> list[str]:
    answer_lower = answer_text.lower()
    messages: list[str] = []
    for item in answer_contains:
        text = str(item)
        if text.lower() not in answer_lower:
            messages.append(f"Missing answer text: {text}")
    for item in answer_excludes:
        text = str(item)
        if text.lower() in answer_lower:
            messages.append(f"Unexpected answer text: {text}")
    return messages
