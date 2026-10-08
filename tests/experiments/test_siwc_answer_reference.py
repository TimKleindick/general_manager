"""Tests for fixture-backed final-answer references."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

import pytest

from experiments.siwc_eval.answer_reference import (
    AnswerReferenceError,
    build_answer_references,
)
from experiments.siwc_eval.datasets import (
    DATASETS,
    load_experiment_dataset,
    oracle_queries,
    setup_experiment_dataset,
)


def _full_fields(manager: str) -> list[Any]:
    if manager == "MaterialManager":
        return ["name", "density"]
    if manager == "PartManager":
        return ["name", {"material": ["name", "density"]}]
    if manager == "ProjectManager":
        return [
            "name",
            {"parts": ["name", {"material": ["name", "density"]}]},
        ]
    return ["name", "code", "status"]


def _project(value: Any, fields: list[Any]) -> Any:
    if isinstance(value, list):
        return [_project(item, fields) for item in value]
    projected: dict[str, Any] = {}
    for item in fields:
        if isinstance(item, str):
            projected[item] = value[item]
        else:
            relation, nested = next(iter(item.items()))
            projected[relation] = _project(value[relation], nested)
    return projected


def _all_cases() -> list[tuple[str, Any]]:
    cases: list[tuple[str, Any]] = []
    for dataset in DATASETS:
        cases.extend((dataset, case) for case in load_experiment_dataset(dataset))
    return cases


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value))
    return set()


def test_every_case_turn_has_one_reference() -> None:
    cases = _all_cases()
    assert len(cases) == 60
    reference_count = 0
    for dataset, case in cases:
        setup_experiment_dataset(dataset)
        references = build_answer_references(dataset, case)
        turn_count = sum(bool(turn.get("user")) for turn in case.conversation)
        assert len(references) == turn_count
        assert all(reference.get("expected") is not None for reference in references)
        assert not any(
            _keys(reference)
            & {
                "answer_contains",
                "answer_excludes",
                "tool_calls",
                "required_tool_calls",
            }
            for reference in references
        )
        reference_count += len(references)
    assert reference_count == 71


@pytest.mark.parametrize(
    "dataset", ["expanded_queries", "expanded_relations", "expanded_follow_ups"]
)
def test_expanded_references_match_each_oracle_query(dataset: str) -> None:
    from general_manager.chat.tools import execute_chat_tool

    setup_experiment_dataset(dataset)
    for case in load_experiment_dataset(dataset):
        references = build_answer_references(dataset, case)
        for query_args, reference in zip(
            oracle_queries(case.name), references, strict=True
        ):
            trusted_args = deepcopy(query_args)
            manager = str(trusted_args["manager"])
            trusted_args["fields"] = _full_fields(manager)
            result = execute_chat_tool("query", trusted_args, None)
            assert reference["kind"] == "records"
            assert reference["expected"] == {
                "manager": manager,
                "rows": _project(result["data"], query_args.get("fields", ["name"])),
            }
            assert reference["supporting_facts"]["full_rows"] == result["data"]


def test_numeric_boundaries_and_duplicate_multiplicity_are_preserved() -> None:
    setup_experiment_dataset("expanded_queries")
    cases = {case.name: case for case in load_experiment_dataset("expanded_queries")}
    above = build_answer_references(
        "expanded_queries", cases["expanded_density_above_upper_boundary"]
    )[0]
    full_rows = above["supporting_facts"]["full_rows"]
    assert [row["density"] for row in full_rows] == [8.96, 8.96]
    assert Counter(row["name"] for row in full_rows) == {"Kupfer": 2}

    duplicate_parts = build_answer_references(
        "expanded_queries", cases["expanded_duplicate_part_name"]
    )[0]
    assert [row["name"] for row in duplicate_parts["expected"]["rows"]] == [
        "Bolt",
        "Bolt",
    ]


def test_nested_relations_and_follow_up_turn_two_are_complete() -> None:
    setup_experiment_dataset("expanded_relations")
    nested_case = next(
        case
        for case in load_experiment_dataset("expanded_relations")
        if case.name == "expanded_project_parts_nested_fields"
    )
    nested = build_answer_references("expanded_relations", nested_case)[0]
    parts = nested["expected"]["rows"][0]["parts"]
    assert [part["name"] for part in parts] == [
        "Bolt",
        "Gear",
        "Flansch",
        "Messing clip",
        "Brush block",
        "Zinc washer",
    ]
    assert nested["supporting_facts"]["full_rows"][0]["parts"][1]["material"] == {
        "name": "Cobalt",
        "density": 8.9,
    }

    setup_experiment_dataset("follow_ups")
    follow_case = next(
        case
        for case in load_experiment_dataset("follow_ups")
        if case.name == "refine_query"
    )
    follow_refs = build_answer_references("follow_ups", follow_case)
    assert follow_refs[1]["expected"]["rows"] == [{"name": "Bolt"}]
    assert follow_refs[1]["supporting_facts"]["full_rows"] == [
        {"name": "Bolt", "material": {"name": "Steel", "density": 7.8}}
    ]


def test_explicit_material_binding_is_required_for_part_listing() -> None:
    setup_experiment_dataset("basic_queries")
    case = next(
        case
        for case in load_experiment_dataset("basic_queries")
        if case.name == "exact_manager_needs_schema_for_fields"
    )
    reference = build_answer_references("basic_queries", case)[0]
    assert reference["expected"]["rows"] == [
        {"name": "Bolt", "material": {"name": "Steel"}},
        {"name": "Bearing", "material": {"name": "Aluminum"}},
        {"name": "Gear", "material": {"name": "Cobalt"}},
    ]


def test_schema_discovery_and_unavailable_manager_references_are_factual() -> None:
    setup_experiment_dataset("edge_cases")
    cases = {case.name: case for case in load_experiment_dataset("edge_cases")}
    unavailable = build_answer_references("edge_cases", cases["unknown_manager"])[0]
    assert unavailable["expected"] == {"manager": "VehicleManager", "available": False}
    assert unavailable["supporting_facts"]["available_managers"] == [
        "MaterialManager",
        "PartManager",
        "ProjectManager",
    ]

    setup_experiment_dataset("large_schema")
    cases = {case.name: case for case in load_experiment_dataset("large_schema")}
    schema = build_answer_references(
        "large_schema", cases["large_schema_find_exact_manager"]
    )[0]
    assert schema["expected"]["manager"] == "SyntheticManager42"
    assert "fields" not in schema["expected"]
    assert schema["supporting_facts"]["full_schema"]["fields"] == [
        "code",
        "name",
        "status",
    ]
    no_path = build_answer_references(
        "large_schema", cases["large_schema_no_hallucinated_path"]
    )[0]
    assert no_path["expected"]["path"] is None
    assert no_path["expected"]["connected"] is False


def test_chain_reference_supports_optional_source_record_details() -> None:
    setup_experiment_dataset("large_schema")
    cases = {case.name: case for case in load_experiment_dataset("large_schema")}
    reference = build_answer_references(
        "large_schema", cases["large_schema_long_chain_path"]
    )[0]
    # The requested answer remains the target; accurate anchor details are optional.
    assert reference["expected"] == {
        "manager": "SyntheticManager08",
        "rows": [{"name": "SyntheticManager08 record"}],
    }
    assert reference["supporting_facts"]["from_manager"] == "SyntheticManager01"
    assert reference["supporting_facts"]["source_rows"] == [
        {
            "code": "SM01-001",
            "name": "SyntheticManager01 record",
            "status": "active",
        }
    ]


def test_strategy_metadata_is_not_required_answer_content() -> None:
    setup_experiment_dataset("basic_queries")
    cases = {case.name: case for case in load_experiment_dataset("basic_queries")}
    relation = build_answer_references(
        "basic_queries", cases["schema_before_relation_filter"]
    )[0]
    assert relation["kind"] == "records"
    assert "schema" not in relation["expected"]

    schema_case = cases["get_schema_before_query"]
    schema_reference = build_answer_references("basic_queries", schema_case)[0]
    assert schema_reference["expected"]["schema"]["fields"] == ["density", "name"]

    legacy_keys = {
        "answer_contains",
        "answer_excludes",
        "tool_calls",
        "required_tool_calls",
    }

    def walk(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(walk(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(walk(item) for item in value))
        return set()

    assert not (walk(schema_reference) & legacy_keys)


def test_unknown_case_fails_closed_without_keyword_fallback() -> None:
    from general_manager.chat.evals.runner import EvalCase

    case = EvalCase(
        name="not_a_real_case",
        description="",
        conversation=[{"user": "anything"}],
        expectations={"answer_contains": ["anything"]},
    )
    with pytest.raises(AnswerReferenceError):
        build_answer_references("basic_queries", case)
