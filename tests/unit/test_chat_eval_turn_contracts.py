"""Regression coverage for strict per-turn evaluation and exact result sets."""

import asyncio
import json

import pytest

from general_manager.chat.evals.runner import (
    EvalCase,
    TurnRecord,
    _score_case,
    _write_trace,
    load_dataset,
    print_report,
    run_case,
)
from general_manager.chat.evals.diagnostics import classify_result
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from general_manager.chat.evals.fixtures import setup_toy_schema
from general_manager.chat.tools import get_tool_definitions


def record(names, answer):
    return TurnRecord(
        tool_calls=[{"name": "query", "args": {"manager": "PartManager"}}],
        tool_results=[{"data": [{"name": name} for name in names]}],
        answer_chunks=[answer],
    )


def test_refine_query_cannot_pass_on_evidence_from_first_turn():
    case = load_dataset("follow_ups")[0]
    result = _score_case(
        case,
        [
            record(["Bolt", "Bearing", "Gear"], "Bolt, Bearing, Gear"),
            TurnRecord(answer_chunks=["Bolt, Bearing, Gear"]),
        ],
    )
    assert not result.passed


def strict_case():
    return EvalCase(
        "strict",
        "",
        [{"user": "only Bolt"}],
        {
            "turns": [
                {
                    "result_set": {
                        "manager": "PartManager",
                        "rows": [{"name": "Bolt"}],
                    },
                    "answer_contains": ["Bolt"],
                    "answer_excludes": ["Bearing", "Gear"],
                }
            ]
        },
    )


def test_exact_result_set_rejects_superset_and_duplicate_rows():
    for names in (["Bolt", "Bearing"], ["Bolt", "Bolt"]):
        assert not _score_case(strict_case(), [record(names, "Bolt")]).passed
    assert _score_case(strict_case(), [record(["Bolt"], "Bolt")]).passed


def test_turn_failure_is_explained_in_report_and_diagnostics():
    result = _score_case(strict_case(), [record(["Bearing"], "Bolt")])
    report = print_report([result], verbose=True)
    assert "turn 1" in report
    assert "result_set_mismatch" in report
    answer_line = next(
        line for line in report.splitlines() if line.startswith("Answer quality")
    )
    assert answer_line.split()[-3:] == ["1", "1", "100%"]
    diagnostic = classify_result(result)
    assert diagnostic is not None
    assert diagnostic.case == result.case.name
    assert diagnostic.category == "wrong_query_result"


def test_trace_keeps_turn_boundaries_and_exact_failure_reason(tmp_path):
    from general_manager.chat.evals.traces import EvalTraceWriter

    case = strict_case()
    item = record(["Bearing"], "Bolt")
    item.requests = 2
    result = _score_case(case, [item])
    path = tmp_path / "trace.jsonl"
    _write_trace(EvalTraceWriter(path), case=case, records=[item], result=result)
    trace = json.loads(path.read_text())
    assert trace["requests"] == 2
    assert trace["turns"][0]["answer"] == "Bolt"
    assert trace["turns"][0]["passed"] is False
    assert trace["turns"][0]["result_set_reason"] == "result_set_mismatch"


@pytest.mark.parametrize(
    "bad_result",
    [
        {"data": [{"other_field": "Bolt"}]},
        {"data": "Bolt"},
        {"data": [{"name": "Bolt"}], "error": "failed"},
        {"data": [{"name": "Bolt"}], "errors": ["failed"]},
    ],
)
def test_incomplete_or_failed_result_cannot_pass(bad_result):
    item = record(["Bolt"], "Bolt")
    item.tool_results = [bad_result]
    assert not _score_case(strict_case(), [item]).passed


def test_nested_result_projection_accepts_extra_fields_but_preserves_rows():
    from general_manager.chat.evals.judges.result_set import judge_result_set

    expected = {
        "manager": "ProjectManager",
        "rows": [{"name": "Apollo", "parts": [{"name": "Bolt"}, {"name": "Gear"}]}],
    }
    calls = [{"name": "query", "args": {"manager": "ProjectManager"}}]
    rows = [
        {
            "name": "Apollo",
            "id": 1,
            "parts": [
                {"name": "Gear", "material": {"name": "Cobalt"}},
                {"name": "Bolt", "material": {"name": "Steel"}},
            ],
        }
    ]
    assert judge_result_set(expected, calls, [{"data": rows}]).passed
    rows[0]["parts"].append({"name": "Bolt"})
    assert not judge_result_set(expected, calls, [{"data": rows}]).passed
    rows[0]["parts"] = [{"name": "Gear"}, {"id": 2}]
    assert not judge_result_set(expected, calls, [{"data": rows}]).passed


def test_exact_result_set_uses_last_matching_query_in_turn():
    item = record(["Bolt"], "Bolt")
    item.tool_calls.append({"name": "query", "args": {"manager": "PartManager"}})
    item.tool_results.append({"data": [{"name": "Bearing"}]})
    assert not _score_case(strict_case(), [item]).passed


def test_missing_turn_or_empty_answer_cannot_pass():
    assert not _score_case(strict_case(), []).passed
    assert not _score_case(strict_case(), [record(["Bolt"], "")]).passed


def test_missing_final_answer_is_not_misreported_as_provider_connection_failure():
    result = _score_case(strict_case(), [record(["Bolt"], "")])
    diagnostic = classify_result(result)
    assert diagnostic is not None
    assert diagnostic.category == "missing_turn_answer"
    assert diagnostic.failure_class == "answer_grounding"


def test_wrong_first_turn_cannot_be_masked_by_correct_follow_up():
    case = strict_case()
    case.conversation.append({"user": "only Bolt again"})
    case.expectations["turns"] *= 2
    result = _score_case(
        case, [record(["Bearing"], "Bearing"), record(["Bolt"], "Bolt")]
    )
    assert not result.passed
    assert not result.turn_results[0].passed
    assert result.turn_results[1].passed


@pytest.mark.parametrize(
    "expectations",
    [
        {"turns": []},
        {"turns": [{}]},
        {"turns": [{"turns": [{"answer_contains": ["Bolt"]}]}]},
        {"turns": [{"result_set": {"manager": "PartManager", "rows": []}}]},
    ],
)
def test_invalid_turn_contract_is_rejected_before_provider_call(expectations):
    class NeverCalled:
        def complete(self, *args):
            pytest.fail("invalid contracts must not spend model requests")

    case = strict_case()
    case.expectations = expectations
    result = asyncio.run(run_case(NeverCalled(), case, []))
    assert not result.passed and result.error


def test_failed_or_missing_query_is_not_an_empty_result():
    case = EvalCase(
        "empty",
        "",
        [{"user": "missing"}],
        {
            "turns": [
                {
                    "result_set": {
                        "manager": "PartManager",
                        "fields": ["name"],
                        "rows": [],
                    },
                }
            ]
        },
    )
    assert _score_case(case, [record([], "No matches")]).passed
    assert not _score_case(case, [TurnRecord(answer_chunks=["No matches"])]).passed
    bad = record([], "No matches")
    bad.tool_results = [{"error": "query failed"}]
    assert not _score_case(case, [bad]).passed


def test_larger_budget_applies_per_turn_and_retains_tool_history():
    class Provider:
        def __init__(self):
            self.count = 0
            self.histories = []

        async def complete(self, messages, tools):
            self.histories.append(list(messages))
            self.count += 1
            if self.count % 8:
                yield ToolCallEvent(
                    f"call-{self.count}",
                    "query",
                    {"manager": "PartManager", "fields": ["name"]},
                )
            else:
                yield TextChunkEvent("Bolt, Bearing, Gear")
            yield DoneEvent(TokenUsage())

    setup_toy_schema()
    provider = Provider()
    case = EvalCase(
        "two",
        "",
        [{"user": "first"}, {"user": "second"}],
        {
            "turns": [
                {"answer_contains": ["Bolt"]},
                {"answer_contains": ["Gear"]},
            ]
        },
    )
    result = asyncio.run(
        run_case(provider, case, get_tool_definitions(), max_tool_iterations=16)
    )
    assert result.passed and provider.count == 16
    assert any(message.role == "tool" for message in provider.histories[8])


def test_budget_exhaustion_is_an_explicit_harness_outcome():
    class Endless:
        async def complete(self, messages, tools):
            yield ToolCallEvent(
                "call", "query", {"manager": "PartManager", "fields": ["name"]}
            )
            yield DoneEvent(TokenUsage())

    setup_toy_schema()
    result = asyncio.run(
        run_case(
            Endless(), strict_case(), get_tool_definitions(), max_tool_iterations=2
        )
    )
    assert not result.passed
    assert result.error == "turn_request_budget_exhausted"
    diagnostic = classify_result(result)
    assert diagnostic is not None and diagnostic.owner == "harness"


def test_explicit_request_budget_also_bounds_optional_recovery():
    class Endless:
        count = 0

        async def complete(self, messages, tools):
            self.count += 1
            yield ToolCallEvent(
                "call", "query", {"manager": "PartManager", "fields": ["name"]}
            )
            yield DoneEvent(TokenUsage())

    setup_toy_schema()
    provider = Endless()
    result = asyncio.run(
        run_case(
            provider,
            strict_case(),
            get_tool_definitions(),
            max_tool_iterations=1,
            recover_missing_tools=True,
        )
    )
    assert provider.count == 1
    assert result.error == "turn_request_budget_exhausted"


def test_budget_failure_retains_completed_turn_diagnostics():
    case = strict_case()
    case.conversation.append({"user": "only Bolt again"})
    case.expectations["turns"] *= 2
    failed = record(["Bolt"], "[max tool iterations reached]")
    failed.error = "turn_request_budget_exhausted"
    failed.requests = 16
    result = _score_case(case, [failed])
    assert not result.passed
    assert result.error == "turn_request_budget_exhausted"
    assert len(result.turn_results) == 1
    assert result.turn_results[0].requests == 16


def test_discovery_case_accepts_grounded_answer_without_one_mandatory_route():
    case = next(
        item
        for item in load_dataset("multi_hop")
        if item.name == "discover_then_traverse"
    )
    result = _score_case(
        case,
        [
            TurnRecord(
                tool_calls=[{"name": "query", "args": {"manager": "ProjectManager"}}],
                tool_results=[
                    {
                        "data": [
                            {
                                "name": "Mercury",
                                "parts": [
                                    {
                                        "name": "Bearing",
                                        "material": {"name": "Aluminum"},
                                    }
                                ],
                            }
                        ]
                    }
                ],
                answer_chunks=["Mercury: Bearing (Aluminum)."],
            )
        ],
    )
    assert result.passed
    assert not _score_case(
        case,
        [TurnRecord(answer_chunks=["ProjectManager has related PartManager records."])],
    ).passed
