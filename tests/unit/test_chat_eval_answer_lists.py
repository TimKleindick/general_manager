"""Bounded list-grounding checks, independent of fixture names and gold wording."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from general_manager.chat.evals.runner import (
    EvalCase,
    TurnRecord,
    _score_case,
    load_dataset,
)


def _record(names: list[str], answer: str) -> TurnRecord:
    return TurnRecord(
        tool_calls=[{"name": "query", "args": {"manager": "AnyManager"}}],
        tool_results=[{"data": [{"label": name} for name in names]}],
        answer_chunks=[answer],
    )


def _case(names: list[str]) -> EvalCase:
    return EvalCase(
        name="unseen_labels",
        description="List returned labels",
        conversation=[{"user": "List the returned labels."}],
        expectations={
            "contract": {"hard": {"results_contain": names}},
            "answer_contains": names,
        },
    )


def test_packaged_gate_rejects_invented_extra_entity() -> None:
    case = load_dataset("basic_queries")[0]
    record = _record(
        ["Steel", "Aluminum", "Cobalt"],
        "Steel, Aluminum, Cobalt, and Uranium. "
        "Uranium is available with stock 1,000 kg.",
    )
    record.tool_calls[0]["args"]["manager"] = "MaterialManager"

    result = _score_case(case, [record])

    assert not result.passed
    assert result.contract_score is not None
    assert "Answer lists unsupported result value: Uranium" in (
        result.contract_score.violations
    )
    # This metric is keyword coverage, independently of the hard grounding gate.
    assert result.answer_score is not None and result.answer_score.score == 1.0


@pytest.mark.parametrize(
    ("names", "extra"),
    [
        (["Steel", "Aluminum", "Cobalt"], "Uranium"),
        (["Oak", "Elm", "Pine"], "Birch"),
        (["Ticket-A", "Ticket-B", "Ticket-C"], "Ticket-X"),
    ],
)
@pytest.mark.parametrize("formatting", ["inline", "bullets", "numbered"])
def test_added_unseen_list_member_fails_after_fixture_renaming(
    names: list[str], extra: str, formatting: str
) -> None:
    labels = [*names, extra]
    if formatting == "inline":
        answer = ", ".join(labels[:-1]) + " and " + labels[-1] + "."
    elif formatting == "bullets":
        answer = "\n".join(f"- {label}" for label in labels)
    else:
        answer = "\n".join(f"{index}. {label}" for index, label in enumerate(labels, 1))
    result = _score_case(_case(names), [_record(names, answer)])
    assert not result.passed


@pytest.mark.parametrize(
    "answer",
    [
        "Cobalt, Steel and Aluminum.",
        "steel, aluminum, cobalt.",
        "- **Steel**\n- `Aluminum`\n- Cobalt",
        "1. Steel\n2. Aluminum\n3. Cobalt",
        "Steel, which is returned here, is available. Aluminum and Cobalt appear too.",
        "Steel, too. Aluminum and Cobalt are returned.",
        "Steel, Aluminum, and Cobalt are the returned materials.",
        "Steel is returned, and I can inspect the schema. Aluminum and Cobalt are returned.",
    ],
)
def test_supported_lists_and_explanatory_commas_remain_valid(answer: str) -> None:
    names = ["Steel", "Aluminum", "Cobalt"]
    assert _score_case(_case(names), [_record(names, answer)]).passed


def test_supported_multiword_and_delimited_labels_are_not_split() -> None:
    names = ["Acme, Inc.", "Rock and Roll", "Red Oak"]
    result = _score_case(
        _case(names), [_record(names, "Acme, Inc., Rock and Roll and Red Oak.")]
    )
    assert result.passed


@pytest.mark.parametrize("value", ["42", 42, "0", 0, "-7", -7, "3.25", 3.25])
@pytest.mark.parametrize("formatting", ["inline", "bullets", "numbered"])
@pytest.mark.parametrize("extra", [None, "Uranium"])
def test_observed_numeric_labels_are_supported_without_hiding_extra_labels(
    value: str | int | float, formatting: str, extra: str | None
) -> None:
    names = ["Steel", str(value), "Cobalt"]
    labels = [*names, *([extra] if extra is not None else [])]
    if formatting == "inline":
        answer = ", ".join(labels[:-1]) + " and " + labels[-1] + "."
    elif formatting == "bullets":
        answer = "\n".join(f"- {label}" for label in labels)
    else:
        answer = "\n".join(f"{index}. {label}" for index, label in enumerate(labels, 1))
    record = _record(names, answer)
    record.tool_results[0]["data"][1]["label"] = value

    result = _score_case(_case(names), [record])

    assert result.passed is (extra is None)
    assert result.contract_score is not None
    assert result.contract_score.answer_sense.checks["no_unsupported_list_values"] is (
        extra is None
    )
    assert result.contract_score.violations == (
        [f"Answer lists unsupported result value: {extra}"] if extra else []
    )


@pytest.mark.parametrize("extra", [None, "Unseen"])
def test_numeric_labels_do_not_mask_numbered_list_prefixes(extra: str | None) -> None:
    names = ["1", "2", "3"]
    labels = [*names, *([extra] if extra is not None else [])]
    answer = "\n".join(f"{index}. {label}" for index, label in enumerate(labels, 1))
    result = _score_case(_case(names), [_record(names, answer)])

    assert result.passed is (extra is None)
    assert result.contract_score is not None
    assert result.contract_score.answer_sense.checks["no_unsupported_list_values"] is (
        extra is None
    )
    assert result.contract_score.violations == (
        [f"Answer lists unsupported result value: {extra}"] if extra else []
    )


@pytest.mark.parametrize("prefix", ["1. ", "1) ", "- ", "+ "])
@pytest.mark.parametrize("extra", [None, "Uranium"])
@pytest.mark.parametrize(
    ("formatting", "suffix"),
    [
        ("inline", ""),
        ("inline", "\nThe query has completed."),
        ("inline", "\n2. The query has completed."),
        ("numbered", ""),
    ],
)
def test_observed_label_prefix_does_not_hide_inline_list_members(
    prefix: str, formatting: str, extra: str | None, suffix: str
) -> None:
    names = [prefix + "Steel", "Cobalt"]
    labels = [*names, *([extra] if extra is not None else [])]
    if formatting == "inline":
        answer = ", ".join(labels[:-1]) + " and " + labels[-1] + "."
    else:
        answer = "\n".join(f"{index}. {label}" for index, label in enumerate(labels, 1))

    result = _score_case(_case(names), [_record(names, answer + suffix)])

    assert result.passed is (extra is None)
    assert result.contract_score is not None
    assert result.contract_score.answer_sense.checks["no_unsupported_list_values"] is (
        extra is None
    )
    assert result.contract_score.violations == (
        [f"Answer lists unsupported result value: {extra}"] if extra else []
    )


@pytest.mark.parametrize("extra", [None, "Uranium"])
def test_observed_prefix_label_does_not_disable_actual_numbered_list(
    extra: str | None,
) -> None:
    names = ["1. Steel", "Steel", "Cobalt"]
    answer = "1. Steel\n2. Cobalt" + ("\n3. " + extra if extra else "")
    result = _score_case(_case(names), [_record(names, answer)])

    assert result.passed is (extra is None)
    assert result.contract_score is not None
    assert result.contract_score.answer_sense.checks["no_unsupported_list_values"] is (
        extra is None
    )
    assert result.contract_score.violations == (
        [f"Answer lists unsupported result value: {extra}"] if extra else []
    )


def test_numbered_explanatory_sentences_remain_outside_bare_label_grammar() -> None:
    names = ["Steel", "Cobalt"]
    answer = "1. Steel is returned.\n2. Cobalt is returned."
    result = _score_case(_case(names), [_record(names, answer)])

    assert result.passed
    assert result.contract_score is not None
    assert "no_unsupported_list_values" not in result.contract_score.answer_sense.checks


@pytest.mark.parametrize("blank", ["", " ", "\t", "\n"])
def test_blank_observed_values_do_not_hide_unseen_list_members(blank: str) -> None:
    names = ["Steel", "Cobalt"]
    record = _record([*names, blank], "Steel, Cobalt and Uranium.")

    result = _score_case(_case(names), [record])

    assert not result.passed
    assert result.contract_score is not None
    assert result.contract_score.violations == [
        "Answer lists unsupported result value: Uranium"
    ]


def test_observed_numeric_label_does_not_support_a_different_number() -> None:
    names = ["Steel", "42", "Cobalt"]
    result = _score_case(_case(names), [_record(names, "Steel, 42, Cobalt and 142.")])

    assert not result.passed
    assert result.contract_score is not None
    assert result.contract_score.violations == [
        "Answer lists unsupported result value: 142"
    ]


def test_new_list_member_passes_when_it_is_actually_in_query_evidence() -> None:
    names = ["Steel", "Aluminum", "Cobalt"]
    record = _record([*names, "Uranium"], "Steel, Aluminum, Cobalt and Uranium.")
    assert _score_case(_case(names), [record]).passed


@pytest.mark.parametrize(
    ("tool", "result"),
    [
        ("search_managers", {"data": [{"label": "Uranium"}]}),
        ("get_manager_schema", {"data": [{"label": "Uranium"}]}),
        ("query", {"data": [{"label": "Uranium"}], "error": "failed"}),
        ("query", {"data": [{"label": "Uranium"}], "errors": ["failed"]}),
        ("query", {"data": [{"label": "Uranium"}], "status": "error"}),
    ],
)
def test_non_query_and_failed_results_do_not_ground_an_extra_list_member(
    tool: str, result: dict[str, Any]
) -> None:
    names = ["Steel", "Aluminum", "Cobalt"]
    record = _record(names, "Steel, Aluminum, Cobalt and Uranium.")
    record.tool_calls.append({"name": tool, "args": {"manager": "OtherManager"}})
    record.tool_results.append(result)
    assert not _score_case(_case(names), [record]).passed


@pytest.mark.parametrize("extra", ["Uranium Oxide", "Purple Pine", "new label"])
def test_initial_bullet_list_rejects_unseen_multiword_label(extra: str) -> None:
    names = ["Steel", "Aluminum", "Cobalt"]
    record = _record(names, "- Steel\n- Aluminum\n- Cobalt\n- " + extra)
    result = _score_case(_case(names), [record])
    assert not result.passed
    assert result.contract_score is not None
    assert f"Answer lists unsupported result value: {extra}" in (
        result.contract_score.violations
    )


def test_bullet_explanation_sentences_are_outside_label_list_check() -> None:
    names = ["Steel", "Aluminum", "Cobalt"]
    record = _record(
        names, "- Steel\n- Aluminum\n- Cobalt\n- The schema is also available."
    )
    result = _score_case(_case(names), [record])
    assert result.passed
    assert result.contract_score is not None
    assert "no_unsupported_list_values" not in result.contract_score.answer_sense.checks


def test_direct_query_tool_and_nested_values_are_eligible() -> None:
    record = _record(["Steel"], "Steel and Cobalt.")
    record.tool_calls[0]["name"] = "query_AnyManager"
    record.tool_results = [
        {"data": [{"label": "Steel", "related": {"label": "Cobalt"}}]}
    ]
    assert _score_case(_case(["Steel"]), [record]).passed


def test_contract_requiring_schema_facts_does_not_treat_them_as_extra_rows() -> None:
    case = load_dataset("basic_queries")[3]
    record = _record(
        ["Steel", "Aluminum", "Cobalt"],
        "name, density, Steel, Aluminum and Cobalt.",
    )
    record.tool_calls[0]["args"]["manager"] = "MaterialManager"
    record.tool_calls.insert(
        0, {"name": "get_manager_schema", "args": {"manager": "MaterialManager"}}
    )
    record.tool_results.insert(0, {"fields": ["name", "density"]})
    result = _score_case(case, [record])
    assert result.passed
    assert result.contract_score is not None
    assert "no_unsupported_list_values" not in result.contract_score.answer_sense.checks


def test_free_prose_invented_quantity_needs_independent_claim_adjudication() -> None:
    case = load_dataset("basic_queries")[0]
    record = _record(
        ["Steel", "Aluminum", "Cobalt"],
        "Steel, Aluminum and Cobalt. Steel is available with stock 1,000 kg.",
    )
    record.tool_calls[0]["args"]["manager"] = "MaterialManager"
    result = _score_case(case, [record])
    assert not result.passed
    assert result.status == "unscored"
    assert result.abstentions
    assert result.error is None
    assert result.contract_score is not None and result.contract_score.passed


@pytest.mark.parametrize("answer", ["The total is 42 kg.", "42"])
def test_required_numeric_main_result_is_not_an_additional_list_claim(
    answer: str,
) -> None:
    assert _score_case(_case(["42"]), [_record(["42"], answer)]).passed


def test_german_article_is_not_a_quantity_claim() -> None:
    assert _score_case(
        _case(["Oak", "Elm"]),
        [_record(["Oak", "Elm"], "Oak and Elm. Eine Übersicht ist beigefügt.")],
    ).passed


@pytest.mark.parametrize(
    "quantity",
    [
        "1,000 kg",
        "2.5 litres",
        "12%",
        "€45",
        "100 units",
        "7kg",
        "7e3kg",
        "7E+3 units",
        ".5kg",
    ],
)
@pytest.mark.parametrize("names", [["Oak", "Elm"], ["Item-X", "Item-Y"]])
def test_quantity_prose_cannot_be_grounded_by_a_matching_scalar(
    names: list[str], quantity: str
) -> None:
    record = _record(
        names, " and ".join(names) + f". {names[0]} has {quantity} in stock."
    )
    record.tool_results[0]["data"].append({"unrelated": quantity})
    result = _score_case(_case(names), [record])
    assert result.status == "unscored"
    assert not result.passed


@pytest.mark.parametrize("reverse", [False, True])
def test_quantity_abstention_never_hides_an_existing_failure(reverse: bool) -> None:
    names = ["Oak", "Elm"]
    fail = _record(names, "Oak, Elm and Birch. Oak has 7 kg in stock.")
    unknown = _record(names, "Oak and Elm. Oak has 7 kg in stock.")
    assert _score_case(_case(names), [fail]).status == "fail"
    case = _case(names)
    case.conversation *= 2
    case.expectations = {"turns": [case.expectations, case.expectations]}
    records = [fail, unknown]
    if reverse:
        records.reverse()
    result = _score_case(case, records)
    assert result.status == "fail"
    assert not result.passed


def test_quantity_abstention_is_reported_and_kept_in_readiness_denominator(
    tmp_path: Path,
) -> None:
    from general_manager.chat.evals.readiness import build_summary
    from general_manager.chat.evals.runner import _write_trace, print_report
    from general_manager.chat.evals.traces import EvalTraceWriter

    names = ["Oak", "Elm"]
    case = _case(names)
    record = _record(names, "Oak and Elm. Oak has 7 kg in stock.")
    result = _score_case(case, [record])
    summary = build_summary(
        gate="offline",
        run_metadata={
            "run_hash": "synthetic",
            "provider": "none",
            "model": "none",
            "fixture": "synthetic",
            "datasets": ["synthetic"],
        },
        results=[result],
    )
    assert (summary.total, summary.passed, summary.failed, summary.unscored) == (
        1,
        0,
        0,
        1,
    )
    assert summary.pass_rate == 0
    report = print_report([result], verbose=True)
    assert "0 PASS, 0 FAIL, 1 UNSCORED" in report
    assert result.abstentions[0] in report
    path = tmp_path / "trace.jsonl"
    _write_trace(EvalTraceWriter(path), case=case, records=[record], result=result)
    trace = json.loads(path.read_text())
    assert trace["status"] == "unscored"
    assert trace["abstentions"] == result.abstentions
    assert trace["contract"]["violations"] == []
