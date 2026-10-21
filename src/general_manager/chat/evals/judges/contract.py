"""Judge: product behavior contract for chat evals."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from general_manager.chat.evals.judges.result_accuracy import judge_result_accuracy


@dataclass(frozen=True)
class AnswerSenseScore:
    """Deterministic sense check for whether a final answer uses tool evidence."""

    passed: bool
    score: float
    checks: dict[str, bool] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


def _passing_answer_sense() -> AnswerSenseScore:
    return AnswerSenseScore(passed=True, score=1.0)


@dataclass(frozen=True)
class ProductContractScore:
    """Product-contract judgment with hard failures and soft strategy deviations."""

    passed: bool
    category: str
    violations: list[str] = field(default_factory=list)
    strategy_deviations: list[str] = field(default_factory=list)
    answer_sense: AnswerSenseScore = field(default_factory=_passing_answer_sense)


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
    answer_sense = judge_answer_sense(
        expected_result_values=_as_list(hard.get("results_contain")),
        expected_answer_values=_as_list(hard.get("answer_contains")),
        tool_calls=tool_calls,
        tool_results=tool_results,
        answer_text=answer_text,
    )
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
    violations.extend(answer_sense.issues)
    violations.extend(
        _unavailable_manager_messages(
            _as_list(hard.get("unavailable_managers")), answer_text
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
        answer_sense=answer_sense,
    )


def judge_answer_sense(
    *,
    expected_result_values: list[Any],
    tool_calls: list[dict[str, Any]],
    tool_results: list[Any],
    answer_text: str,
    expected_answer_values: list[Any] | None = None,
) -> AnswerSenseScore:
    """Rate whether the answer makes sense against the observed tool evidence."""
    expected_values = [str(item) for item in expected_result_values]
    checks: dict[str, bool] = {}
    issues: list[str] = []

    contradiction_messages = _answer_sense_messages(
        expected_values,
        tool_results,
        answer_text,
    )
    checks["no_contradiction"] = len(contradiction_messages) == 0
    issues.extend(contradiction_messages)

    has_successful_query = _has_successful_query_result(
        tool_calls,
        tool_results,
    )
    defers_after_query = has_successful_query and _answer_defers_after_query(
        answer_text
    )
    checks["no_unnecessary_deferral"] = not defers_after_query
    if defers_after_query:
        issues.append("Answer defers after a successful query")

    if has_successful_query:
        includes_raw_query_syntax = _answer_includes_raw_query_syntax(answer_text)
        checks["no_raw_query_syntax"] = not includes_raw_query_syntax
        if includes_raw_query_syntax:
            issues.append("Answer includes raw query syntax after a successful query")

    path_contradiction = _answer_contradicts_successful_path(
        tool_calls,
        tool_results,
        answer_text,
    )
    checks["no_path_contradiction"] = not path_contradiction
    if path_contradiction:
        issues.append("Answer contradicts successful path result")

    if expected_values:
        omitted_values = [
            value
            for value in expected_values
            if value.casefold() not in answer_text.casefold()
        ]
        checks["includes_required_result_values"] = len(omitted_values) == 0
        issues.extend(
            f"Answer omits required result value: {value}" for value in omitted_values
        )

    result_terms = {value.casefold() for value in expected_values}
    # Mixed schema/data contracts can legitimately list field names beside rows.
    if result_terms and all(
        str(value).casefold() in result_terms
        for value in (expected_answer_values or [])
    ):
        list_messages = _answer_list_messages(tool_calls, tool_results, answer_text)
        if list_messages is not None:
            checks["no_unsupported_list_values"] = not list_messages
            issues.extend(list_messages)

    passed_count = sum(1 for passed in checks.values() if passed)
    score = passed_count / len(checks) if checks else 1.0
    return AnswerSenseScore(
        passed=len(issues) == 0,
        score=score,
        checks=checks,
        issues=issues,
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
        normalized = _normalized_tool_call(call)
        if normalized.get("name") != name:
            continue
        args = _as_dict(normalized.get("args"))
        if all(_arg_matches(args, key, value) for key, value in args_contain.items()):
            return True
    return False


def _forbidden_tool_messages(
    forbidden_tools: list[Any],
    actual: list[dict[str, Any]],
) -> list[str]:
    forbidden_names = {item for item in forbidden_tools if isinstance(item, str)}
    called = {_tool_kind(str(call.get("name", ""))) for call in actual}
    return [
        f"Forbidden tool called: {name}"
        for name in sorted(forbidden_names.intersection(called))
    ]


def _result_messages(
    results_contain: list[Any],
    results_exclude: list[Any],
    tool_results: list[Any],
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


def _unavailable_manager_messages(managers: list[Any], answer: str) -> list[str]:
    """Reject explicit availability claims, permitting truthful named denials.

    These bounded legacy checks do not replace SIWC's independent semantic Judge.
    Naming a manager is not by itself an assertion that it is available.
    """
    messages = []
    denial = r"(?:not\s+(?:available|exposed|accessible|supported|present)|unavailable|not\s+exist)"
    for item in managers:
        manager = re.escape(str(item))
        target = rf"\b{manager}\b"
        subject = rf"(?:{target}|that requested manager|the requested manager)"
        patterns = (
            rf"{subject}\s+(?:(?:is|was)\s+{denial}|does\s+not\s+exist)",
            rf"\bno\s+(?:manager\s+(?:named|called)\s+)?{target}(?:\s+is\s+(?:available|exposed|accessible))?",
            rf"{target},?\s+which\s+(?:does\s+not\s+exist|is\s+{denial})",
            rf"\b(?:cannot|can't|do not|don't)\s+(?:access|query|find)\s+{target}",
        )
        remaining = answer
        for pattern in patterns:
            remaining = re.sub(pattern, "", remaining, flags=re.IGNORECASE)
        assertions = (
            rf"{target}\s+(?:(?:is|was)\s+(?:available|exposed|accessible|supported|present|not\s+unavailable)|exists?\b|contains?\b)",
            rf"\b(?:I|we)\s+(?:found|can\s+(?:access|query))\s+{target}",
            rf"\b(?:available|exposed)\s+managers?\s*:[^.!?\n]*{target}",
        )
        if any(
            re.search(pattern, remaining, flags=re.IGNORECASE) for pattern in assertions
        ):
            messages.append(f"Answer must truthfully deny availability: {item}")
    return messages


def _answer_sense_messages(
    results_contain: list[Any],
    tool_results: list[Any],
    answer_text: str,
) -> list[str]:
    """Fail answers that mention expected results while negating their meaning."""
    if not results_contain or not answer_text.strip():
        return []

    result_text = _tool_results_text(tool_results).lower()
    answer_sentences = _answer_sentences(answer_text)
    messages: list[str] = []
    for item in results_contain:
        text = str(item)
        if text.lower() not in result_text:
            continue
        if _answer_negates_value(answer_sentences, text):
            messages.append(f"Answer contradicts required result value: {text}")
    return messages


def _has_successful_query_result(
    tool_calls: list[dict[str, Any]],
    tool_results: list[Any],
) -> bool:
    return any(
        _tool_kind(str(call.get("name", ""))) == "query"
        and isinstance(result, dict)
        and "error" not in result
        and isinstance(result.get("data"), list)
        and len(result["data"]) > 0
        for call, result in zip(tool_calls, tool_results, strict=False)
    )


def _answer_list_messages(
    tool_calls: list[dict[str, Any]],
    tool_results: list[Any],
    answer_text: str,
) -> list[str] | None:
    """Check initial bare lists, not arbitrary prose or semantic claim support.

    Only aligned successful query data can support listed values. A list must
    include an observed value, and every item must be a bare label or an exact
    observed scalar. Unrecognized syntax is outside this bounded check.
    """
    if len(tool_calls) != len(tool_results):
        return None
    values = {
        value
        for call, result in zip(tool_calls, tool_results, strict=True)
        if _tool_kind(str(call.get("name", ""))) == "query"
        and isinstance(result, dict)
        and result.get("status", "success") == "success"
        and not result.get("error")
        and not result.get("errors")
        and isinstance(result.get("data"), list)
        for value in _scalar_values(result["data"])
        if value.strip()
    }
    if not values:
        return None

    # Protect observed labels containing commas, conjunctions, or periods before
    # parsing presentation. The marker cannot occur in the candidate answer.
    marker = "\x00"
    while marker in answer_text:
        marker += "\x00"
    pattern = "|".join(
        re.escape(value) for value in sorted(values, key=len, reverse=True)
    )
    observed = re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.IGNORECASE)
    # Try the initial inline sentence first. Numbered-looking later prose must
    # not suppress a list whose full observed labels are already recognized.
    candidates: list[tuple[bool, list[str]]] = []
    masked = observed.sub(lambda _match: marker, answer_text).strip()
    sentences = _answer_sentences(masked)
    if sentences and not sentences[0].endswith("?"):
        first = sentences[0]
        conjunction = re.search(r"\s+(?:and|und)\s+", first, re.IGNORECASE)
        items = re.split(
            r",\s*(?:(?:and|und)\s+)?|\s+(?:and|und)\s+",
            first,
            flags=re.IGNORECASE,
        )
        # Two comma-separated fragments can be ordinary prose ("Steel, too.").
        if conjunction or len(items) >= 3:
            candidates.append((False, items))

    bullet = re.compile(r"^\s*(?:[-*+] |\d+[.)] )\s*(.+)$")
    items = []
    for line in answer_text.strip().splitlines():
        match = bullet.match(line)
        if match is None:
            break
        # Preserve numbered-list prefixes even when their numbers are also
        # observed labels; only the item body represents a result value.
        items.append(observed.sub(lambda _match: marker, match.group(1)))
    candidates.append((True, items))

    for is_bullet_list, items in candidates:
        items = [item.strip(" \t*_`") for item in items]
        # Unpunctuated bullet labels may contain multiple words. Sentence-like
        # bullets and inline explanatory clauses remain outside this grammar.
        items = [
            item.strip(".")
            if not is_bullet_list or not re.search(r"\s", item)
            else item
            for item in items
        ]
        if len(items) < 2 or marker not in items:
            continue
        label = r"\w+(?:[-']\w+)*"
        if is_bullet_list:
            label = rf"{label}(?:[ \t]+{label})*"
        if any(item != marker and re.fullmatch(label, item) is None for item in items):
            continue
        return [
            f"Answer lists unsupported result value: {item}"
            for item in dict.fromkeys(items)
            if item != marker
        ]
    return None


def _answer_contradicts_successful_path(
    tool_calls: list[dict[str, Any]],
    tool_results: list[Any],
    answer_text: str,
) -> bool:
    if not any(
        str(call.get("name", "")) == "find_path"
        and isinstance(result, list)
        and len(result) > 0
        for call, result in zip(tool_calls, tool_results, strict=False)
    ):
        return False

    return any(
        _answer_sentence_denies_successful_path(sentence.casefold())
        for sentence in _answer_sentences(answer_text)
    )


def _answer_sentence_denies_successful_path(normalized_sentence: str) -> bool:
    denial_markers = (
        "no path",
        "don't have a path",
        "do not have a path",
        "can't find a path",
        "cannot find a path",
        "no relationship",
    )
    if any(marker in normalized_sentence for marker in denial_markers):
        return True
    if "not connected" not in normalized_sentence:
        return False
    return "not connected directly" not in normalized_sentence


def _normalized_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    name = str(call.get("name", ""))
    normalized_name = _tool_kind(name)
    args = _as_dict(call.get("args")).copy()
    if normalized_name == "query" and name.startswith("query_"):
        args.setdefault("manager", name.removeprefix("query_"))
    return {"name": normalized_name, "args": args}


def _tool_kind(name: str) -> str:
    if name.startswith("query_"):
        return "query"
    return name


def _arg_matches(args: dict[str, Any], key: str, expected: Any) -> bool:
    actual = args.get(key)
    if key == "manager" and isinstance(actual, str) and isinstance(expected, str):
        return actual.casefold() == expected.casefold()
    return bool(actual == expected)


def _answer_defers_after_query(answer_text: str) -> bool:
    normalized = answer_text.casefold()
    return any(
        marker in normalized
        for marker in (
            "do you want me to run",
            "i can run a query",
            "i can run this query",
            "if you want, i can run",
            "let me know if you want me to run",
            "should i run",
            "would you like me to query",
            "would you like me to run",
        )
    )


def _answer_includes_raw_query_syntax(answer_text: str) -> bool:
    normalized = answer_text.casefold()
    return any(
        marker in normalized
        for marker in (
            "```graphql",
            "query {",
            "query\n",
            "mutation {",
            "mutation\n",
        )
    )


def _tool_results_text(tool_results: list[Any]) -> str:
    return " ".join(_scalar_values(tool_results))


def _scalar_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        output: list[str] = []
        for child in value.values():
            output.extend(_scalar_values(child))
        return output
    if isinstance(value, list):
        output = []
        for child in value:
            output.extend(_scalar_values(child))
        return output
    if isinstance(value, str | int | float | bool):
        return [str(value)]
    return []


def _answer_sentences(answer_text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer_text)
        if sentence.strip()
    ]


def _answer_negates_value(sentences: list[str], value: str) -> bool:
    value_pattern = re.escape(value.lower())
    negation_patterns = (
        rf"\b{value_pattern}\b[^.!?;]*\bnot\s+(?:affected|flagged|found|included|listed|matched|present|returned|related)\b",
        rf"\b{value_pattern}\b[^.!?;]*\b(?:does|do|did)\s+not\s+(?:appear|contain|have|include|match|use)\b",
        rf"\b{value_pattern}\b[^.!?;]*\bcannot\s+(?:be\s+)?(?:confirm|verify|find)\b",
        rf"\bcannot\s+(?:be\s+)?(?:confirm|verify|find)\b[^.!?;]*\b{value_pattern}\b",
    )
    for sentence in sentences:
        normalized = sentence.lower()
        if value.lower() not in normalized:
            continue
        if any(re.search(pattern, normalized) for pattern in negation_patterns):
            return True
    return False
