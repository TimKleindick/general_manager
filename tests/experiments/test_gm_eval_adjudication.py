"""Offline judge contracts; these controls never measure model performance."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING, Any, Self, cast

import pytest

if TYPE_CHECKING:
    from general_manager.chat.providers.base import ChatEvent, Message, ToolDefinition


def api() -> Any:
    name = "experiments.gm_eval.adjudication"
    assert importlib.util.find_spec(name) is not None, (
        "The offline adjudication bridge is missing"
    )
    return importlib.import_module(name)


def expectation(identifier: str = "E004", turn: int = 0) -> dict[str, Any]:
    from experiments.gm_eval.catalog import load_catalog
    from experiments.gm_eval.oracle import expected_turn

    case = next(row for row in load_catalog() if row["id"] == identifier)
    return expected_turn(case, turn)


def saved_run(*, two_turns: bool = False) -> dict[str, Any]:
    """An explicit synthetic trace fixture, not a fabricated benchmark result."""
    output = {
        "data": [{"code": "S01", "quantity": "120", "project": {"code": "P01"}}],
        "total_count": 1,
        "has_more": False,
    }
    args = {
        "manager": "Shipment",
        "fields": ["code", "quantity", {"project": ["code"]}],
    }
    answer = "Aurora (P01) shipped 120 pieces during calendar year 2025, according to Shipment S01."
    events = [
        {
            "type": "tool_call",
            "task_id": "read",
            "id": "query-1",
            "name": "query",
            "args": args,
        },
        {
            "type": "tool_result",
            "task_id": "read",
            "id": "query-1",
            "name": "query",
            "result": output,
        },
        {"type": "text_chunk", "content": answer},
        {"type": "done"},
    ]
    turn = {
        "turn": 1,
        "user": "How many pieces did Aurora ship in calendar year 2025?",
        "answer": answer,
        "events": events,
        "tool_calls": events[:1],
        "tool_results": events[1:2],
        "history": [],
        "history_source": "production_prepare_conversation_messages",
        "durable_messages": [
            {
                "role": "tool",
                "content": json.dumps(output),
                "tool_name": "query",
                "tool_args": args,
                "tool_result": output,
            }
        ],
        "persistence_verified": True,
    }
    reference = {
        "task": {"task_id": "read"},
        "manager_candidates": [{"manager": "Shipment"}, {"manager": "Project"}],
        "task_evidence": [],
    }
    result: dict[str, Any] = {
        "case_id": "E004",
        "profile": {"name": "secret-candidate-model"},
        "old_scores": {"passed": True},
        "model_performance_measured": False,
        "turns": [turn],
        "trace": {
            "provider_calls": [
                {
                    "turn": 1,
                    "model": "secret-candidate-model",
                    "messages": [
                        {
                            "role": "user",
                            "content": "REFERENCE_DATA=" + json.dumps(reference),
                        }
                    ],
                    "text": "ignored model response",
                }
            ]
        },
        "schema_index": {
            "Shipment": {"relations": [{"name": "project", "target": "Project"}]},
            "Project": {"relations": []},
        },
    }
    if two_turns:
        result["turns"].append(
            {
                "turn": 2,
                "user": "Use kilograms instead, please.",
                "answer": "The answer in kilograms is unavailable.",
                "events": [
                    {
                        "type": "text_chunk",
                        "content": "The answer in kilograms is unavailable.",
                    },
                    {"type": "done"},
                ],
                "tool_calls": [],
                "tool_results": [],
                "history": [
                    {"role": "tool", "content": json.dumps(output)},
                    {"role": "assistant", "content": answer},
                ],
                "history_source": "production_prepare_conversation_messages",
                "durable_messages": [],
                "persistence_verified": True,
            }
        )
    return result


def request() -> dict[str, Any]:
    return cast(
        dict[str, Any], api().build_adjudication_request(expectation(), saved_run(), 0)
    )


def response(packet: dict[str, Any], *, wrong: bool = False) -> str:
    facts = deepcopy(expectation()["facts"])
    if wrong:
        facts["values"]["P01"] = "999"
    evidence = [row["id"] for row in packet["observation"]["trace"]["evidence"]]
    answer = packet["packet"]["answer"]
    return json.dumps(
        {
            "schema_version": packet["schema_version"],
            "status": "completed",
            "request_sha256": packet["request_sha256"],
            "answer_sha256": packet["packet"]["answer_sha256"],
            "facts": facts,
            "fact_support": {
                field: {
                    "status": "present",
                    "answer_quotes": [answer],
                    "evidence_ids": evidence,
                }
                for field in facts
            },
            "citations": [
                {"evidence_id": item, "answer_quote": "according to Shipment S01"}
                for item in evidence
            ],
            "semantic_checks": {
                item["id"]: {
                    "passed": True,
                    "reason": "The answer is supported by the cited saved source.",
                    "evidence_ids": ["answer", *evidence],
                }
                for item in packet["packet"]["semantic_checks"]
            },
        }
    )


def test_request_is_blind_to_identity_scores_gold_and_future_turns() -> None:
    run = saved_run(two_turns=True)
    contract = expectation()
    contract["facts"]["values"] = {"DO_NOT_LEAK_GOLD_ID": "918273.45"}
    packet = api().build_adjudication_request(contract, run, 0)
    serialized = json.dumps(packet)
    for absent in (
        "secret-candidate-model",
        "old_scores",
        "918273.45",
        "DO_NOT_LEAK_GOLD_ID",
        "kilograms",
    ):
        assert absent not in serialized
    assert packet["packet"]["questions"] == [run["turns"][0]["user"]]
    assert packet["packet"]["visible_history"] == []
    assert "120" in serialized  # Actual observed output remains available.
    assert packet["model_performance_measured"] is False


@pytest.mark.parametrize(
    "references",
    [[], ["invented"], ["turn-99/call-1/Shipment"], ["answer", "answer"]],
)
def test_semantic_response_contract_rejects_unusable_references(
    references: list[str],
) -> None:
    packet = request()
    value = json.loads(response(packet))
    # The empty-list case reproduces the saved Sol E106 judge contract defect.
    check = next(iter(value["semantic_checks"].values()))
    check["evidence_ids"] = references
    assert not api()._matches_shape(value, packet["packet"]["response_schema"])
    result = api().parse_adjudication_response(
        packet, json.dumps(value), judge_id="offline-review"
    )
    assert result["status"] == "judge_failure"
    assert result["semantic_judgment"] is None
    assert result["observation"]["facts"] is None


def test_semantic_contract_scopes_references_to_current_and_visible_history() -> None:
    run = saved_run(two_turns=True)
    contract = expectation()
    contract["turn_index"] = 1
    packet = api().build_adjudication_request(contract, run, 1)
    eligible = [row["id"] for row in packet["packet"]["evidence"]]
    assert eligible
    assert all(row["origin"] == "history" for row in packet["packet"]["evidence"])
    original_ids = {row["id"] for row in packet["packet"]["history_evidence"]}
    checks = packet["packet"]["response_schema"]["oneOf"][0]["properties"][
        "semantic_checks"
    ]["properties"]
    for check in checks.values():
        refs = check["properties"]["evidence_ids"]
        assert refs.get("minItems") == 1
        assert set(refs["items"].get("enum", [])) == {"answer", *eligible}
        assert not original_ids.intersection(refs["items"]["enum"])
        assert '"answer"' in refs["description"]
        assert "current answer" in refs["description"]


def test_answer_only_semantic_reference_is_documented_without_tool_evidence() -> None:
    run = saved_run()
    turn = run["turns"][0]
    turn.update(
        events=turn["events"][2:],
        tool_calls=[],
        tool_results=[],
        durable_messages=[],
    )
    packet = api().build_adjudication_request(expectation(), run, 0)
    assert packet["packet"]["evidence"] == []
    schema = packet["packet"]["response_schema"]["oneOf"][0]["properties"]
    for check in schema["semantic_checks"]["properties"].values():
        assert check["properties"]["evidence_ids"]["items"].get("enum") == ["answer"]
    assert '"answer"' in api().JUDGE_INSTRUCTION
    value = json.loads(response(packet))
    assert all(item["evidence_ids"] == [] for item in value["fact_support"].values())
    assert all(
        item["evidence_ids"] == ["answer"] for item in value["semantic_checks"].values()
    )
    for name in packet["packet"]["grounding_contract"]["data_fields"]:
        value["fact_support"][name]["status"] = "unsupported"
    assert api()._matches_shape(value, packet["packet"]["response_schema"])
    result = api().parse_adjudication_response(
        packet, json.dumps(value), judge_id="offline-review"
    )
    assert result["status"] == "completed"
    assert result["observation"]["extraction"]["evidence_ids"] == []


@pytest.mark.parametrize("section", ["fact_support", "citations"])
def test_answer_sentinel_never_becomes_fact_or_citation_evidence(section: str) -> None:
    packet = request()
    value = json.loads(response(packet))
    if section == "fact_support":
        field = next(iter(value["fact_support"]))
        value["fact_support"][field]["evidence_ids"] = ["answer"]
    else:
        value["citations"][0]["evidence_id"] = "answer"
    result = api().parse_adjudication_response(
        packet, json.dumps(value), judge_id="offline-review"
    )
    assert result["status"] == "judge_failure"
    assert result["observation"]["facts"] is None


def test_legacy_schema_still_fails_closed_on_empty_semantic_references() -> None:
    packet = request()
    checks = packet["packet"]["response_schema"]["oneOf"][0]["properties"][
        "semantic_checks"
    ]["properties"]
    for check in checks.values():
        check["properties"]["evidence_ids"] = {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        }
    packet["request_sha256"] = api()._request_digest(packet)
    value = json.loads(response(packet))
    next(iter(value["semantic_checks"].values()))["evidence_ids"] = []
    assert api()._matches_shape(value, packet["packet"]["response_schema"])
    result = api().parse_adjudication_response(
        packet, json.dumps(value), judge_id="offline-review"
    )
    assert result["status"] == "judge_failure"
    assert result["failure_flags"][0]["reason"] == "unsupported_semantic_judgment"


def test_every_contract_has_a_value_free_extraction_shape() -> None:
    from experiments.gm_eval.catalog import load_catalog
    from experiments.gm_eval.oracle import expected_turn

    for case in load_catalog():
        for index in range(len(case["turns"])):
            contract = expected_turn(case, index)
            shapes = api().fact_schemas(contract)
            assert set(shapes) == set(contract["facts"])
            text = json.dumps(shapes)
            assert '"2027"' not in text
            assert '"P01"' not in text
            assert '"C01"' not in text
            assert "minItems" not in text and "maxItems" not in text


def test_nested_sources_come_from_actual_rows_and_saved_schema() -> None:
    packet = request()
    trace = packet["observation"]["trace"]
    assert {item["manager"] for item in trace["evidence"]} == {"Shipment", "Project"}
    assert (
        trace["tool_calls"][0]["output"]
        == saved_run()["turns"][0]["tool_results"][0]["result"]
    )
    assert trace["observation_complete"] is True
    run = saved_run()
    run["turns"][0]["events"][1]["result"]["data"][0].pop("project")
    packet = api().build_adjudication_request(expectation(), run, 0)
    assert {item["manager"] for item in packet["observation"]["trace"]["evidence"]} == {
        "Shipment"
    }


def test_query_root_discovery_credits_only_an_actually_returned_nested_manager() -> (
    None
):
    from experiments.gm_eval.scoring import score_turn

    run = saved_run()
    run["case_id"] = "E002"
    provider_message = run["trace"]["provider_calls"][0]["messages"][0]
    reference = json.loads(provider_message["content"].split("=", 1)[1])
    reference["manager_candidates"] = [{"manager": "Shipment"}]
    provider_message["content"] = "REFERENCE_DATA=" + json.dumps(reference)
    contract = expectation("E002")
    packet = api().build_adjudication_request(contract, run, 0)
    scored = score_turn(
        contract,
        packet["observation"],
        registry={"Shipment": "Database", "Project": "Database"},
    )
    assert scored["dimensions"]["D"]["status"] == "pass"
    assert packet["observation"]["trace"]["discovery"][0]["candidates"] == ["Shipment"]
    assert packet["observation"]["trace"]["tool_calls"][0]["root_manager"] == "Shipment"
    run["turns"][0]["events"][1]["result"]["data"][0].pop("project")
    packet = api().build_adjudication_request(contract, run, 0)
    scored = score_turn(
        contract,
        packet["observation"],
        registry={"Shipment": "Database", "Project": "Database"},
    )
    assert scored["dimensions"]["D"]["status"] == "fail"


def test_error_outputs_and_schema_selection_are_not_row_evidence() -> None:
    run = saved_run()
    run["turns"][0]["events"][1]["result"] = {
        "status": "error",
        "code": "invalid_query",
    }
    run["turns"][0]["tool_results"] = run["turns"][0]["events"][1:2]
    packet = api().build_adjudication_request(expectation(), run, 0)
    assert packet["observation"]["trace"]["evidence"] == []
    assert packet["observation"]["trace"]["tool_calls"][0]["error"] is True


def test_empty_successful_query_is_valid_absence_evidence() -> None:
    run = saved_run()
    run["turns"][0]["events"][1]["result"].update(data=[], total_count=0)
    packet = api().build_adjudication_request(expectation(), run, 0)
    assert {item["manager"] for item in packet["observation"]["trace"]["evidence"]} == {
        "Shipment"
    }


def test_history_reuse_is_grounded_in_visible_persisted_prior_results() -> None:
    run = saved_run(two_turns=True)
    contract = expectation()
    contract["turn_index"] = 1
    packet = api().build_adjudication_request(contract, run, 1)
    trace = packet["observation"]["trace"]
    assert trace["evidence"]
    assert all(
        row["origin"] == "history" and row["source_turn"] == 0
        for row in trace["evidence"]
    )
    assert all(
        row["source_evidence_id"] in {item["id"] for item in trace["history_evidence"]}
        for row in trace["evidence"]
    )
    assert trace["discovery"] == []
    assert trace["tool_calls"][0]["root_manager"] == "Shipment"
    run["turns"][1]["history"] = [
        {"role": "assistant", "content": run["turns"][0]["answer"]}
    ]
    hidden = api().build_adjudication_request(contract, run, 1)
    assert hidden["observation"]["trace"]["evidence"] == []


def test_modified_or_fabricated_history_does_not_gain_provenance() -> None:
    run = saved_run(two_turns=True)
    run["turns"][1]["history"][0]["content"] = (
        '{"data":[{"quantity":"999"}],"total_count":1,"has_more":false}'
    )
    contract = expectation()
    contract["turn_index"] = 1
    packet = api().build_adjudication_request(contract, run, 1)
    assert packet["observation"]["trace"]["evidence"] == []


def test_parse_binds_facts_to_answer_trace_and_separate_semantic_judgment() -> None:
    packet = request()
    result = api().parse_adjudication_response(
        packet, response(packet), judge_id="offline-review"
    )
    assert result["status"] == "completed"
    assert result["observation"]["facts"] == expectation()["facts"]
    assert result["observation"]["extraction"]["origin"] == "external"
    assert result["semantic_judgment"]["judge_id"] == "offline-review"
    assert result["judge_calls"] == 0
    assert result["judge_usage"] is None
    assert result["model_performance_measured"] is False


def test_judge_cannot_override_independent_numeric_failure() -> None:
    from experiments.gm_eval.scoring import score_turn

    packet = request()
    result = api().parse_adjudication_response(
        packet, response(packet, wrong=True), judge_id="offline-review"
    )
    assert result["status"] == "completed"
    scored = score_turn(
        expectation(),
        result["observation"],
        registry={"Shipment": "Database", "Project": "Database"},
        semantic_judgment=result["semantic_judgment"],
    )
    assert scored["dimensions"]["C"]["status"] == "fail"
    assert scored["primary_failure"] == "model_task_failure"


@pytest.mark.parametrize(
    "mutation",
    [
        "stale_answer",
        "stale_request",
        "unknown_evidence",
        "foreign_turn",
        "unsupported_quote",
        "missing_fact",
        "missing_check",
        "boolean_as_string",
        "extra_key",
        "unsupported_present",
        "absent_with_value",
    ],
)
def test_malformed_or_inconsistent_judgment_fails_closed(mutation: str) -> None:
    packet = request()
    value = json.loads(response(packet))
    field = next(iter(value["facts"]))
    check = next(iter(value["semantic_checks"]))
    if mutation == "stale_answer":
        value["answer_sha256"] = "0" * 64
    elif mutation == "stale_request":
        value["request_sha256"] = "0" * 64
    elif mutation == "unknown_evidence":
        value["fact_support"][field]["evidence_ids"] = ["invented"]
    elif mutation == "foreign_turn":
        value["citations"][0]["evidence_id"] = "turn-99/query-1"
    elif mutation == "unsupported_quote":
        value["fact_support"][field]["answer_quotes"] = ["invented wording"]
    elif mutation == "missing_fact":
        value["facts"].pop(field)
    elif mutation == "missing_check":
        value["semantic_checks"].pop(check)
    elif mutation == "boolean_as_string":
        value["semantic_checks"][check]["passed"] = "true"
    elif mutation == "extra_key":
        value["score_override"] = "passed"
    elif mutation == "unsupported_present":
        value["fact_support"][field]["answer_quotes"] = []
    else:
        value["fact_support"][field]["status"] = "absent"
    result = api().parse_adjudication_response(
        packet, json.dumps(value), judge_id="offline-review"
    )
    assert result["status"] == "judge_failure"
    assert result["semantic_judgment"] is None
    assert result["observation"]["facts"] is None
    assert result["failure_flags"][0]["category"] == "judge_failure"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{}",
        "null",
        "[]",
        '{"status":"completed","status":"completed"}',
        '{"facts":NaN}',
        "```json\n{}\n```",
    ],
)
def test_strict_json_never_repairs_invalid_judge_output(raw: str) -> None:
    result = api().parse_adjudication_response(
        request(), raw, judge_id="offline-review"
    )
    assert result["status"] == "judge_failure"


def test_absent_answer_facts_are_model_failures_after_valid_extraction() -> None:
    from experiments.gm_eval.scoring import score_turn

    packet = request()
    value = json.loads(response(packet))
    value["facts"] = {name: None for name in value["facts"]}
    value["fact_support"] = {
        name: {"status": "absent", "answer_quotes": [], "evidence_ids": []}
        for name in value["facts"]
    }
    value["citations"] = []
    for check in value["semantic_checks"].values():
        check.update(
            passed=False, reason="Required content is absent.", evidence_ids=["answer"]
        )
    result = api().parse_adjudication_response(
        packet, json.dumps(value), judge_id="offline-review"
    )
    assert result["status"] == "completed"
    scored = score_turn(
        expectation(),
        result["observation"],
        registry={"Shipment": "Database", "Project": "Database"},
        semantic_judgment=result["semantic_judgment"],
    )
    assert scored["primary_failure"] == "model_task_failure"


def test_request_tampering_and_inconsistent_saved_event_copies_are_rejected() -> None:
    packet = request()
    raw = response(packet)
    packet["packet"]["answer"] = "A different candidate answer"
    assert (
        api().parse_adjudication_response(packet, raw, judge_id="offline-review")[
            "status"
        ]
        == "judge_failure"
    )
    run = saved_run()
    run["turns"][0]["tool_results"] = []
    with pytest.raises(ValueError, match="saved_trace"):
        api().build_adjudication_request(expectation(), run, 0)


class OfflineJudge:
    """Explicit provider test double; no provider selection or auth side effects."""

    def __init__(self, events: list[ChatEvent], *, failure: bool = False) -> None:
        self.events = events
        self.failure = failure
        self.seen: list[Message] = []

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> Self:
        message = "Adjudication must never select or configure a provider"
        raise AssertionError(message)

    @property
    def provider_config(self) -> Mapping[str, Any]:
        message = "Candidate blindness does not require judge credentials/config"
        raise AssertionError(message)

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[ChatEvent]:
        assert tools == []
        self.seen = messages
        for event in self.events:
            yield event
        if self.failure:
            message = "secret provider diagnostic must not leak"
            raise RuntimeError(message)


def test_injected_judge_usage_is_separate_and_request_is_neutral() -> None:
    from general_manager.chat.providers.base import (
        DoneEvent,
        TextChunkEvent,
        TokenUsage,
    )

    packet = request()
    raw = response(packet)
    provider = OfflineJudge(
        [
            TextChunkEvent(raw[:100]),
            TextChunkEvent(raw[100:]),
            DoneEvent(TokenUsage(123, 45)),
        ]
    )
    result = asyncio.run(
        api().adjudicate_turn(packet, judge_id="offline-judge", judge=provider)
    )
    assert result["status"] == "completed"
    assert result["judge_calls"] == 1
    assert result["judge_usage"] == {
        "input_tokens": 123,
        "output_tokens": 45,
        "reasoning_tokens": None,
    }
    assert result["judge_reported_cost"] is None
    assert [message.role for message in provider.seen] == ["system", "user"]
    assert "untrusted" in provider.seen[0].content
    assert "secret-candidate-model" not in provider.seen[1].content
    assert result["model_performance_measured"] is False


@pytest.mark.parametrize(
    "bad_stream", ["no_done", "two_done", "after_done", "tool", "exception"]
)
def test_judge_stream_contract_failures_are_counted_once(bad_stream: str) -> None:
    from general_manager.chat.providers.base import (
        DoneEvent,
        TextChunkEvent,
        TokenUsage,
        ToolCallEvent,
    )

    packet = request()
    events: list[ChatEvent] = [TextChunkEvent(response(packet))]
    if bad_stream != "no_done":
        events.append(DoneEvent(TokenUsage(10, 20)))
    if bad_stream == "two_done":
        events.append(DoneEvent(TokenUsage(10, 20)))
    elif bad_stream == "after_done":
        events.append(TextChunkEvent("extra"))
    elif bad_stream == "tool":
        events = [ToolCallEvent("x", "query", {})]
    provider = OfflineJudge(events, failure=bad_stream == "exception")
    result = asyncio.run(
        api().adjudicate_turn(packet, judge_id="offline-judge", judge=provider)
    )
    assert result["status"] == "judge_failure"
    assert result["judge_calls"] == 1
    assert "secret provider diagnostic" not in json.dumps(result)


def test_missing_response_and_unknown_usage_do_not_become_success_or_zero() -> None:
    from general_manager.chat.providers.base import (
        DoneEvent,
        TextChunkEvent,
        TokenUsage,
    )

    packet = request()
    result = asyncio.run(api().adjudicate_turn(packet, judge_id="offline-review"))
    assert result["status"] == "judge_failure" and result["judge_calls"] == 0
    assert result["judge_usage"] is None

    class UnknownUsageJudge(OfflineJudge):
        reported_usage = None

    provider = UnknownUsageJudge(
        [TextChunkEvent(response(packet)), DoneEvent(TokenUsage())]
    )
    result = asyncio.run(
        api().adjudicate_turn(packet, judge_id="offline-judge", judge=provider)
    )
    assert result["status"] == "completed" and result["judge_usage"] is None


def test_production_history_wrapper_links_only_to_a_matching_durable_tool() -> None:
    run = saved_run(two_turns=True)
    message = run["turns"][1]["history"][0]
    message.update(
        role="assistant", content="Historical tool data (query): " + message["content"]
    )
    contract = expectation()
    contract["turn_index"] = 1
    packet = api().build_adjudication_request(contract, run, 1)
    assert packet["observation"]["trace"]["history_evidence"]
    run["turns"][0]["durable_messages"] = []
    packet = api().build_adjudication_request(contract, run, 1)
    assert packet["observation"]["trace"]["history_evidence"] == []


def test_scoring_convenience_preserves_judge_failure_and_numeric_authority() -> None:
    packet = request()
    valid = api().parse_adjudication_response(
        packet, response(packet, wrong=True), judge_id="offline-review"
    )
    registry = {"Shipment": "Database", "Project": "Database"}
    assert (
        api().score_adjudicated(expectation(), valid, registry=registry)["dimensions"][
            "C"
        ]["status"]
        == "fail"
    )
    invalid = api().parse_adjudication_response(packet, "{}", judge_id="offline-review")
    assert (
        api().score_adjudicated(expectation(), invalid, registry=registry)[
            "primary_failure"
        ]
        == "judge_failure"
    )


def test_request_integrity_covers_discovery_and_scoring_scope() -> None:
    packet = request()
    raw = response(packet)
    packet["observation"]["trace"]["discovery"][0]["candidates"] = ["InventedManager"]
    assert (
        api().parse_adjudication_response(packet, raw, judge_id="offline-review")[
            "status"
        ]
        == "judge_failure"
    )
    packet = request()
    parsed = api().parse_adjudication_response(
        packet, response(packet), judge_id="offline-review"
    )
    wrong_case = expectation()
    wrong_case["case_id"] = "E999"
    scored = api().score_adjudicated(
        wrong_case, parsed, registry={"Shipment": "Database", "Project": "Database"}
    )
    assert scored["primary_failure"] == "judge_failure"


@pytest.mark.parametrize(
    "category", ["transport_failure", "budget_exhausted", "interface_capability_gap"]
)
def test_saved_candidate_failures_remain_primary_after_valid_adjudication(
    category: str,
) -> None:
    run = saved_run()
    reason = "Observed candidate failure; private model diagnostics withheld."
    run["turns"][0]["failure_flags"] = [
        {"category": category, "phase": "candidate", "reason": reason}
    ]
    packet = api().build_adjudication_request(expectation(), run, 0)
    assert reason not in json.dumps(packet["packet"])
    parsed = api().parse_adjudication_response(
        packet, response(packet), judge_id="offline-review"
    )
    assert parsed["status"] == "completed"
    assert parsed["failure_flags"] == run["turns"][0]["failure_flags"]
    scored = api().score_adjudicated(
        expectation(), parsed, registry={"Shipment": "Database", "Project": "Database"}
    )
    assert scored["primary_failure"] == category
    packet["candidate_failure_flags"] = []
    assert (
        api().parse_adjudication_response(
            packet, response(packet), judge_id="offline-review"
        )["status"]
        == "judge_failure"
    )


def test_harness_string_flags_are_scoped_and_terminal_failures_are_retained() -> None:
    run = saved_run(two_turns=True)
    run["status"] = "transport_failure"
    run["failure_flags"] = ["transport_failure"]
    run["turns"][0]["failure_flags"] = []
    first = api().build_adjudication_request(expectation(), run, 0)
    assert first["candidate_failure_flags"] == []
    run["turns"][0].pop("failure_flags")
    root_failure = api().build_adjudication_request(expectation(), run, 0)
    assert root_failure["candidate_failure_flags"][0]["category"] == "transport_failure"
    failed_judge = api().parse_adjudication_response(
        root_failure, "{}", judge_id="offline-review"
    )
    assert {row["category"] for row in failed_judge["failure_flags"]} == {
        "transport_failure",
        "judge_failure",
    }
    run.pop("failure_flags")
    run.pop("status")
    run["turns"][0]["terminal"] = {"type": "error", "code": "budget_exhausted"}
    run["turns"][0]["events"][-1] = run["turns"][0]["terminal"]
    terminal_failure = api().build_adjudication_request(expectation(), run, 0)
    assert (
        terminal_failure["candidate_failure_flags"][0]["category"] == "budget_exhausted"
    )


def test_unknown_candidate_failure_category_is_rejected() -> None:
    run = saved_run()
    run["failure_flags"] = ["invented_failure"]
    with pytest.raises(ValueError, match="candidate_failure"):
        api().build_adjudication_request(expectation(), run, 0)


def test_provider_close_failure_is_a_counted_judge_failure() -> None:
    class BrokenCloseStream:
        def __aiter__(self) -> Self:
            return self

        async def __anext__(self) -> ChatEvent:
            raise StopAsyncIteration

        async def aclose(self) -> None:
            message = "private close diagnostic"
            raise RuntimeError(message)

    class BrokenCloseJudge(OfflineJudge):
        def complete(
            self, messages: list[Message], tools: list[ToolDefinition]
        ) -> AsyncIterator[ChatEvent]:
            return BrokenCloseStream()

    result = asyncio.run(
        api().adjudicate_turn(
            request(), judge_id="offline-review", judge=BrokenCloseJudge([])
        )
    )
    assert result["status"] == "judge_failure" and result["judge_calls"] == 1
    assert "private close diagnostic" not in json.dumps(result)


def test_actual_harness_trace_round_trip_is_offline_contract_evidence() -> None:
    script = """
import json
from experiments.gm_eval.adjudication import build_adjudication_request, parse_adjudication_response, score_adjudicated
from experiments.gm_eval.catalog import load_catalog
from experiments.gm_eval.harness import offline_check
from experiments.gm_eval.oracle import expected_turn
from experiments.gm_eval.runtime import bootstrap
runtime = bootstrap(5)
try:
 run = offline_check(runtime, "E022")
 case = next(row for row in load_catalog() if row["id"] == "E022")
 expected = expected_turn(case, 1)
 request = build_adjudication_request(expected, run, 1)
 packet = request["packet"]
 raw = json.dumps({"schema_version":request["schema_version"],"status":"completed","request_sha256":request["request_sha256"],"answer_sha256":packet["answer_sha256"],"facts":{name:None for name in packet["fact_fields"]},"fact_support":{name:{"status":"absent","answer_quotes":[],"evidence_ids":[]} for name in packet["fact_fields"]},"citations":[],"semantic_checks":{item["id"]:{"passed":False,"reason":"The infrastructure probe does not answer the task.","evidence_ids":["answer"]} for item in packet["semantic_checks"]}})
 control = json.loads(raw)
 control["semantic_checks"]["no_repeated_clarification"].update(passed=True,reason="The probe asks no repeated question.",context_sha256=packet["context_sha256"],repetitions=[])
 raw = json.dumps(control)
 parsed = parse_adjudication_response(request, raw, judge_id="offline-test-control")
 scored = score_adjudicated(expected, parsed, registry=run["registry_interfaces"])
 print(json.dumps({"history_sources":len(request["observation"]["trace"]["history_evidence"]),"status":parsed["status"],"primary_failure":scored["primary_failure"],"model_performance_measured":parsed["model_performance_measured"],"judge_calls":parsed["judge_calls"]}))
finally:
 runtime.close()
"""
    completed = subprocess.run(  # noqa: S603 -- fixed local script, no external endpoint
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["history_sources"] > 0
    assert result["status"] == "completed"
    assert result["primary_failure"] == "model_task_failure"
    assert result["model_performance_measured"] is False
    assert result["judge_calls"] == 0


@pytest.mark.parametrize(
    "damage",
    [
        "quantity",
        "empty_rows",
        "history",
        "arguments",
        "extraction_rebound",
        "judgment_rebound",
    ],
)
def test_scoring_rejects_trace_content_changed_after_validated_judgment(
    damage: str,
) -> None:
    contract = expectation()
    contract["citation_policy"] = "internal_grounding"
    prepared = request()
    result = api().parse_adjudication_response(
        prepared, response(prepared), judge_id="offline-review"
    )
    registry = {"Shipment": "Database", "Project": "Database"}
    assert api().score_adjudicated(contract, result, registry=registry)["passed"]
    changed = deepcopy(result)
    trace = changed["observation"]["trace"]
    call = next(item for item in trace["tool_calls"] if item.get("name") == "query")
    if damage in {"quantity", "extraction_rebound", "judgment_rebound"}:
        call["output"]["data"][0]["quantity"] = "999"
        if damage != "quantity":
            from experiments.gm_eval.scoring import evidence_trace_digest

            target = (
                changed["observation"]["extraction"]
                if damage == "extraction_rebound"
                else changed["semantic_judgment"]
            )
            target["evidence_trace_sha256"] = evidence_trace_digest(trace)
    elif damage == "empty_rows":
        call["output"].update(data=[], total_count=0)
    elif damage == "history":
        trace["history_evidence"].append({"id": "new-history-content"})
    else:
        call["arguments"]["filters"] = {"code": "different"}
    scored = api().score_adjudicated(contract, changed, registry=registry)
    assert not scored["passed"]
    assert scored["dimensions"]["A"]["status"] != "pass"
    assert result["semantic_judgment"]["checks"]["answer_supported"]["passed"] is True
