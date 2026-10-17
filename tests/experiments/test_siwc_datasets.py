"""Offline coverage for the expanded SIWC fixture and dataset contract."""

from __future__ import annotations

from collections import Counter
import json

import experiments.siwc_eval.datasets as datasets_module
import pytest
from experiments.siwc_eval.datasets import (
    DATASETS,
    RICH_FIXTURE_DATA,
    fixture_fingerprint,
    load_experiment_dataset,
    oracle_queries,
    setup_experiment_dataset,
)
from general_manager.chat.evals.runner import TurnRecord, _score_case
from general_manager.chat.tools import execute_chat_tool


def _project_rows(rows: list[object], fields: list[str]) -> list[dict[str, object]]:
    return [
        {field: row[field] for field in fields} for row in rows if isinstance(row, dict)
    ]


def _row_counter(rows: list[dict[str, object]]) -> Counter[str]:
    return Counter(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)


def test_public_dataset_order_and_legacy_case_count() -> None:
    assert DATASETS[:6] == (
        "basic_queries",
        "edge_cases",
        "follow_ups",
        "multi_hop",
        "demo_readiness",
        "large_schema",
    )
    assert sum(len(load_experiment_dataset(name)) for name in DATASETS[:6]) == 28
    assert sum(len(load_experiment_dataset(name)) for name in DATASETS[6:]) == 32


def test_rich_fixture_has_required_cardinality_and_language_coverage() -> None:
    assert len(RICH_FIXTURE_DATA["materials"]) >= 24
    assert len(RICH_FIXTURE_DATA["parts"]) >= 72
    assert len(RICH_FIXTURE_DATA["projects"]) >= 12

    material_names = [str(item["name"]) for item in RICH_FIXTURE_DATA["materials"]]
    project_names = [str(item["name"]) for item in RICH_FIXTURE_DATA["projects"]]
    assert len({str(item["id"]) for item in RICH_FIXTURE_DATA["materials"]}) == 24
    assert len(set(material_names)) < len(material_names)
    assert len(set(project_names)) < len(project_names)
    assert any(any(char in name for char in "äöü") for name in project_names)
    assert any(name == "Steel" for name in material_names)


def test_expanded_cases_use_per_turn_oracles_and_unique_case_names() -> None:
    cases = [
        case for dataset in DATASETS[6:] for case in load_experiment_dataset(dataset)
    ]
    names = [case.name for case in cases]
    assert len(names) == len(set(names)) == 32
    for case in cases:
        turns = case.expectations["turns"]
        assert len(turns) == len(case.conversation)
        assert len(turns) == len(oracle_queries(case.name))
        assert all("result_set" in turn for turn in turns)
        assert all(set(turn["result_set"]) >= {"manager", "rows"} for turn in turns)
    all_turns = [turn for case in cases for turn in case.expectations["turns"]]
    assert any(not turn["result_set"]["rows"] for turn in all_turns)
    assert any(len(turn["result_set"]["rows"]) > 1 for turn in all_turns)


def test_expanded_result_set_oracles_match_execute_chat_tool() -> None:
    setup_experiment_dataset("expanded_queries")
    for dataset in DATASETS[6:]:
        for case in load_experiment_dataset(dataset):
            for turn, query_args in zip(
                case.expectations["turns"], oracle_queries(case.name), strict=True
            ):
                expected = turn["result_set"]
                fields = expected.get("fields")
                if fields is None:
                    fields = list(expected["rows"][0])
                result = execute_chat_tool("query", query_args, None)
                actual_rows = _project_rows(result["data"], fields)
                expected_rows = _project_rows(expected["rows"], fields)
                assert result["total_count"] >= len(actual_rows)
                assert _row_counter(actual_rows) == _row_counter(expected_rows), (
                    case.name,
                    query_args,
                )


def test_nested_listing_rejects_name_only_rows_and_incomplete_answers() -> None:
    setup_experiment_dataset("expanded_queries")
    case = next(
        case
        for case in load_experiment_dataset("expanded_relations")
        if case.name == "expanded_project_parts_nested_fields"
    )
    query_args = oracle_queries(case.name)[0]
    query_call = {"name": "query", "args": query_args}

    name_only = _score_case(
        case,
        [
            TurnRecord(
                tool_calls=[query_call],
                tool_results=[{"data": [{"name": "Apollo Lab"}]}],
                answer_chunks=[
                    "Apollo Lab: Bolt, Gear, Flansch, Messing clip, Brush block, Zinc washer."
                ],
            )
        ],
    )
    assert not name_only.passed
    assert name_only.turn_results[0].answer_score is not None
    assert name_only.turn_results[0].answer_score.passed
    assert name_only.turn_results[0].result_set_score is not None
    assert name_only.turn_results[0].result_set_score.reason == "missing_result_fields"

    complete_rows = execute_chat_tool("query", query_args, None)
    incomplete_answer = _score_case(
        case,
        [
            TurnRecord(
                tool_calls=[query_call],
                tool_results=[complete_rows],
                answer_chunks=[
                    "Apollo Lab: Bolt, Gear, Flansch, Messing clip, Brush block."
                ],
            )
        ],
    )
    assert not incomplete_answer.passed
    assert incomplete_answer.turn_results[0].result_set_score is not None
    assert incomplete_answer.turn_results[0].result_set_score.passed
    assert incomplete_answer.turn_results[0].answer_score is not None
    assert not incomplete_answer.turn_results[0].answer_score.passed
    assert _score_case(
        case,
        [
            TurnRecord(
                tool_calls=[query_call],
                tool_results=[complete_rows],
                answer_chunks=[
                    "Apollo Lab: Bolt, Gear, Flansch, Messing clip, Brush block, Zinc washer."
                ],
            )
        ],
    ).passed


def test_fixture_fingerprints_are_deterministic_and_partitioned() -> None:
    for name in DATASETS:
        first = fixture_fingerprint(name)
        assert first == fixture_fingerprint(name)
        assert len(first) == 64
        assert all(character in "0123456789abcdef" for character in first)
    assert fixture_fingerprint("expanded_queries") != fixture_fingerprint(
        "basic_queries"
    )
    assert fixture_fingerprint("expanded_queries") == fixture_fingerprint(
        "expanded_relations"
    )


def test_rich_fixture_data_changes_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    original = fixture_fingerprint("expanded_queries")
    changed = {
        str(key): [dict(row) for row in rows]
        for key, rows in datasets_module.RICH_FIXTURE_DATA.items()
    }
    changed["materials"][0]["density"] = 7.81
    monkeypatch.setattr(datasets_module, "RICH_FIXTURE_DATA", changed)
    assert fixture_fingerprint("expanded_queries") != original


def test_default_toy_fixture_remains_unchanged() -> None:
    setup_experiment_dataset("basic_queries")
    result = execute_chat_tool(
        "query", {"manager": "MaterialManager", "fields": ["name"]}, None
    )
    assert result == {
        "data": [{"name": "Steel"}, {"name": "Aluminum"}, {"name": "Cobalt"}],
        "total_count": 3,
        "has_more": False,
        "complete": True,
    }
