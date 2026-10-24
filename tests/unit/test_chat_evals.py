"""Tests for the chat evaluation framework."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase
from general_manager.api.graphql import GraphQL
from general_manager.chat.evals import runner as eval_runner
from general_manager.chat.evals.fingerprints import stable_hash
from general_manager.chat.evals.judges.answer_quality import (
    AnswerQualityScore,
    judge_answer_quality,
)
from general_manager.chat.evals.judges.contract import AnswerSenseScore
from general_manager.chat.evals.judges.contract import ProductContractScore
from general_manager.chat.evals.judges.result_accuracy import (
    ResultAccuracyScore,
    judge_result_accuracy,
)
from general_manager.chat.evals.judges.tool_sequence import (
    ToolSequenceScore,
    judge_tool_sequence,
)
from general_manager.chat.evals.runner import (
    EvalCase,
    EvalResult,
    TurnRecord,
    _score_case,
    filter_cases,
    list_datasets,
    load_dataset,
    print_compare_report,
    print_report,
)
from general_manager.chat.schema_index import clear_schema_index_cache
from general_manager.chat.tools import query
from general_manager.chat.tools import execute_chat_tool
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.path_mapping import PathMap


def _load_run_chat_evals_module() -> Any:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_chat_evals.py"
    spec = importlib.util.spec_from_file_location("run_chat_evals", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Judge tests
# ---------------------------------------------------------------------------


class ToolSequenceJudgeTests(SimpleTestCase):
    def test_empty_expected_passes(self) -> None:
        score = judge_tool_sequence([], [{"name": "query", "args": {}}])
        assert score.passed is True

    def test_exact_match_passes(self) -> None:
        expected = [{"name": "search_managers"}, {"name": "query"}]
        actual = [
            {"name": "search_managers", "args": {"query": "parts"}},
            {"name": "query", "args": {"manager": "PartManager"}},
        ]
        score = judge_tool_sequence(expected, actual)
        assert score.passed is True
        assert score.mismatches == []

    def test_subsequence_match_passes(self) -> None:
        expected = [{"name": "query"}]
        actual = [
            {"name": "search_managers", "args": {}},
            {"name": "get_manager_schema", "args": {}},
            {"name": "query", "args": {"manager": "Part"}},
        ]
        score = judge_tool_sequence(expected, actual)
        assert score.passed is True

    def test_missing_tool_fails(self) -> None:
        expected = [{"name": "search_managers"}, {"name": "mutate"}]
        actual = [{"name": "search_managers", "args": {}}]
        score = judge_tool_sequence(expected, actual)
        assert score.passed is False
        assert any("mutate" in m for m in score.mismatches)

    def test_args_contain_mismatch_fails(self) -> None:
        expected = [{"name": "query", "args_contain": {"manager": "MaterialManager"}}]
        actual = [{"name": "query", "args": {"manager": "PartManager"}}]
        score = judge_tool_sequence(expected, actual)
        assert score.passed is False

    def test_args_contain_missing_key_fails(self) -> None:
        expected = [{"name": "query", "args_contain": {"manager": "X"}}]
        actual = [{"name": "query", "args": {}}]
        score = judge_tool_sequence(expected, actual)
        assert score.passed is False

    def test_args_contain_passes_when_present(self) -> None:
        expected = [{"name": "query", "args_contain": {"manager": "MaterialManager"}}]
        actual = [
            {
                "name": "query",
                "args": {"manager": "MaterialManager", "fields": ["name"]},
            }
        ]
        score = judge_tool_sequence(expected, actual)
        assert score.passed is True

    def test_direct_query_tool_satisfies_generic_query_expectation(self) -> None:
        expected = [{"name": "query", "args_contain": {"manager": "PartManager"}}]
        actual = [{"name": "query_partmanager", "args": {"fields": ["name"]}}]

        score = judge_tool_sequence(expected, actual)

        assert score.passed is True
        assert score.mismatches == []

    def test_direct_query_tool_manager_name_overrides_stray_arg(self) -> None:
        expected = [{"name": "query", "args_contain": {"manager": "PartManager"}}]
        actual = [
            {
                "name": "query_partmanager",
                "args": {"manager": "MaterialManager", "fields": ["name"]},
            }
        ]

        score = judge_tool_sequence(expected, actual)

        assert score.passed is True
        assert score.mismatches == []

    def test_later_matching_call_clears_earlier_arg_mismatch(self) -> None:
        expected = [{"name": "query", "args_contain": {"manager": "MaterialManager"}}]
        actual = [
            {"name": "query", "args": {"manager": "PartManager"}},
            {"name": "query", "args": {"manager": "MaterialManager"}},
        ]

        score = judge_tool_sequence(expected, actual)

        assert score.passed is True
        assert score.mismatches == []

    def test_wrong_order_fails(self) -> None:
        expected = [{"name": "query"}, {"name": "search_managers"}]
        actual = [
            {"name": "search_managers", "args": {}},
            {"name": "query", "args": {}},
        ]
        score = judge_tool_sequence(expected, actual)
        assert score.passed is False


class ResultAccuracyJudgeTests(SimpleTestCase):
    def test_all_present_passes(self) -> None:
        results = [{"name": "Steel"}, {"name": "Aluminum"}]
        score = judge_result_accuracy(["Steel", "Aluminum"], [], results)
        assert score.passed is True
        assert score.missing == []

    def test_missing_item_fails(self) -> None:
        results = [{"name": "Steel"}]
        score = judge_result_accuracy(["Steel", "Cobalt"], [], results)
        assert score.passed is False
        assert "Cobalt" in score.missing

    def test_excluded_item_fails(self) -> None:
        results = [{"name": "Steel"}, {"name": "Mercury"}]
        score = judge_result_accuracy(["Steel"], ["Mercury"], results)
        assert score.passed is False
        assert "Mercury" in score.unexpected

    def test_case_insensitive(self) -> None:
        results = [{"name": "STEEL"}]
        score = judge_result_accuracy(["steel"], [], results)
        assert score.passed is True

    def test_nested_values_extracted(self) -> None:
        results = [{"project": {"name": "Apollo"}, "count": 5}]
        score = judge_result_accuracy(["Apollo", "5"], [], results)
        assert score.passed is True

    def test_empty_expected_passes(self) -> None:
        score = judge_result_accuracy([], [], [{"name": "anything"}])
        assert score.passed is True

    def test_empty_results_with_expected_fails(self) -> None:
        score = judge_result_accuracy(["Steel"], [], [])
        assert score.passed is False


class AnswerQualityJudgeTests(SimpleTestCase):
    def test_all_keywords_present_passes(self) -> None:
        score = judge_answer_quality(
            ["Apollo", "Gemini"], [], "The projects are Apollo and Gemini."
        )
        assert score.passed is True
        assert score.score == 1.0

    def test_partial_keywords_below_threshold_fails(self) -> None:
        score = judge_answer_quality(
            ["Apollo", "Gemini", "Mercury", "Artemis", "Voyager"],
            [],
            "Apollo is the only project.",
        )
        assert score.passed is False
        assert score.score < 0.8

    def test_partial_keywords_at_threshold_passes(self) -> None:
        score = judge_answer_quality(
            ["Apollo", "Gemini", "Mercury", "Artemis", "Voyager"],
            [],
            "Apollo, Gemini, Mercury, and Artemis are the projects.",
        )
        assert score.passed is True
        assert score.score >= 0.8

    def test_excluded_keyword_fails(self) -> None:
        score = judge_answer_quality(
            ["Apollo"], ["Mercury"], "Apollo and Mercury are both projects."
        )
        assert score.passed is False
        assert "Mercury" in score.unexpected

    def test_case_insensitive(self) -> None:
        score = judge_answer_quality(["apollo"], [], "APOLLO is a project.")
        assert score.passed is True

    def test_empty_expected_passes(self) -> None:
        score = judge_answer_quality([], [], "Some text here.")
        assert score.passed is True
        assert score.score == 1.0

    def test_threshold_constant(self) -> None:
        assert AnswerQualityScore.PASS_THRESHOLD == 0.8


# ---------------------------------------------------------------------------
# Dataset loading tests
# ---------------------------------------------------------------------------


def _missing_answer_result_values(
    *,
    dataset_name: str,
    case_name: str,
    scope_name: str,
    expectations: dict[str, Any],
) -> list[str]:
    results = [str(item) for item in expectations.get("results_contain", [])]
    answers = [str(item) for item in expectations.get("answer_contains", [])]
    missing = [item for item in results if item not in answers]
    return (
        [f"{dataset_name}:{case_name}:{scope_name} missing answer_contains {missing}"]
        if missing
        else []
    )


def _missing_grounded_query_result_contract(
    *,
    dataset_name: str,
    case: EvalCase,
) -> list[str]:
    contract = case.expectations.get("contract")
    if not isinstance(contract, dict):
        return []
    hard = contract.get("hard")
    if not isinstance(hard, dict):
        return []
    required_tools = hard.get("required_tool_calls", [])
    if not any(
        isinstance(tool, dict) and tool.get("name") == "query"
        for tool in required_tools
    ):
        return []
    has_positive_results = bool(hard.get("results_contain"))
    has_empty_result_contract = "empty_result" in case.tags and bool(
        hard.get("results_exclude")
    )
    if has_positive_results or has_empty_result_contract:
        return []
    return [f"{dataset_name}:{case.name} missing hard results_contain"]


class DatasetLoadingTests(SimpleTestCase):
    def test_list_datasets_returns_expected_names(self) -> None:
        names = list_datasets()
        assert "basic_queries" in names
        assert "multi_hop" in names
        assert "follow_ups" in names
        assert "edge_cases" in names
        assert "planned_orchestration" in names

    def test_load_basic_queries(self) -> None:
        cases = load_dataset("basic_queries")
        assert len(cases) >= 5
        assert all(isinstance(c, EvalCase) for c in cases)
        assert all(c.name for c in cases)
        assert all(c.conversation for c in cases)

    def test_load_multi_hop(self) -> None:
        cases = load_dataset("multi_hop")
        assert len(cases) >= 5

    def test_load_follow_ups(self) -> None:
        cases = load_dataset("follow_ups")
        assert len(cases) >= 3

    def test_load_edge_cases(self) -> None:
        cases = load_dataset("edge_cases")
        assert len(cases) >= 3

    def test_load_nonexistent_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_dataset("nonexistent_dataset_xyz")

    def test_demo_readiness_dataset_exists_and_is_tier_one(self) -> None:
        cases = load_dataset("demo_readiness")

        assert len(cases) >= 5
        assert {case.tier for case in cases} == {1}
        assert all("demo" in case.tags for case in cases)

    def test_eval_case_loads_tier_and_tags(self) -> None:
        cases = load_dataset("basic_queries")
        first_case = cases[0]

        assert first_case.tier == 0
        assert "single_manager_query" in first_case.tags

    def test_filter_cases_by_tier_and_tag(self) -> None:
        cases = [
            EvalCase(
                name="tier0",
                description="Toy contract",
                conversation=[],
                expectations={},
                tier=0,
                tags=["contract"],
            ),
            EvalCase(
                name="tier1",
                description="Demo flow",
                conversation=[],
                expectations={},
                tier=1,
                tags=["demo"],
            ),
        ]

        assert [case.name for case in filter_cases(cases, tier=1, tags=["demo"])] == [
            "tier1"
        ]
        assert filter_cases(cases, tier=0, tags=["demo"]) == []

    def test_provider_suite_excludes_deterministic_orchestration_dataset(self) -> None:
        datasets = {
            "basic_queries": [
                EvalCase(
                    name="legacy_case",
                    description="",
                    conversation=[],
                    expectations={},
                )
            ],
            "planned_orchestration": [
                EvalCase(
                    name="planned_case",
                    description="",
                    conversation=[],
                    expectations={},
                )
            ],
        }
        executed: list[str] = []

        async def record_run_case(
            _provider: object,
            case: EvalCase,
            _tool_defs: list[dict[str, Any]],
            **_kwargs: Any,
        ) -> EvalResult:
            executed.append(case.name)
            return EvalResult(case=case)

        with (
            patch.object(eval_runner, "list_datasets", return_value=list(datasets)),
            patch.object(eval_runner, "load_dataset", side_effect=datasets.__getitem__),
            patch.object(eval_runner, "get_tool_definitions", return_value=[]),
            patch.object(eval_runner, "run_case", side_effect=record_run_case),
        ):
            results = asyncio.run(eval_runner.run_eval_suite(object()))

        assert [result.case.name for result in results] == ["legacy_case"]
        assert executed == ["legacy_case"]

        with pytest.raises(ValueError, match="require explicit deterministic"):
            asyncio.run(eval_runner.run_eval_suite(object(), ["planned_orchestration"]))

    def test_result_values_are_required_in_answers(self) -> None:
        failures: list[str] = []
        for dataset_name in list_datasets():
            for case in load_dataset(dataset_name):
                expectations = case.expectations
                contract = expectations.get("contract")
                if isinstance(contract, dict):
                    hard = contract.get("hard")
                    if isinstance(hard, dict):
                        failures.extend(
                            _missing_answer_result_values(
                                dataset_name=dataset_name,
                                case_name=case.name,
                                scope_name="contract.hard",
                                expectations=hard,
                            )
                        )
                failures.extend(
                    _missing_answer_result_values(
                        dataset_name=dataset_name,
                        case_name=case.name,
                        scope_name="legacy",
                        expectations=expectations,
                    )
                )

        assert failures == []

    def test_grounded_query_cases_declare_result_contracts(self) -> None:
        failures: list[str] = []
        for dataset_name in list_datasets():
            for case in load_dataset(dataset_name):
                failures.extend(
                    _missing_grounded_query_result_contract(
                        dataset_name=dataset_name,
                        case=case,
                    )
                )

        assert failures == []


def test_load_dataset_rejects_yaml_that_is_not_a_list(tmp_path) -> None:
    (tmp_path / "bad.yaml").write_text("name: bad\n")

    with (
        patch.object(eval_runner, "DATASETS_DIR", tmp_path),
        pytest.raises(TypeError, match="must be a YAML list"),
    ):
        load_dataset("bad")


def test_trace_writer_records_case_payload(tmp_path) -> None:
    from general_manager.chat.evals.traces import EvalTraceWriter

    trace_path = tmp_path / "trace.jsonl"
    writer = EvalTraceWriter(trace_path)
    writer.write_case(
        {
            "case": "demo_case",
            "model": "local-model",
            "tool_calls": [{"name": "query", "args": {"manager": "ProjectManager"}}],
            "answer": "Apollo",
            "passed": True,
        }
    )

    assert trace_path.read_text().splitlines() == [
        '{"answer":"Apollo","case":"demo_case","model":"local-model","passed":true,"tool_calls":[{"args":{"manager":"ProjectManager"},"name":"query"}]}'
    ]


def test_stable_hash_rejects_non_json_payloads() -> None:
    with pytest.raises(TypeError, match="not JSON serializable"):
        stable_hash({"manager_names": {"PartManager", "MaterialManager"}})


class EvalScriptTests(SimpleTestCase):
    def test_setup_test_schema_registers_query_root_fields(self) -> None:
        module = _load_run_chat_evals_module()

        module.setup_test_schema()

        try:
            schema = GraphQL.get_schema()
            assert schema is not None
            schema_text = str(schema)
            assert "materialmanagerList" in schema_text
            assert "partmanagerList" in schema_text
            assert "projectmanagerList" in schema_text
        finally:
            clear_schema_index_cache()
            GraphQL.reset_registry()
            GeneralManagerMeta.all_classes.clear()
            GeneralManagerMeta.pending_graphql_interfaces.clear()
            GeneralManagerMeta.pending_attribute_initialization.clear()
            PathMap.mapping.clear()
            if hasattr(PathMap, "instance"):
                delattr(PathMap, "instance")

    def test_setup_test_schema_returns_eval_fixture_data(self) -> None:
        module = _load_run_chat_evals_module()

        module.setup_test_schema()

        try:
            result = query(
                manager="MaterialManager",
                filters={},
                fields=["name"],
            )
            assert [row["name"] for row in result["data"]] == [
                "Steel",
                "Aluminum",
                "Cobalt",
            ]
        finally:
            clear_schema_index_cache()
            GraphQL.reset_registry()
            GeneralManagerMeta.all_classes.clear()
            GeneralManagerMeta.pending_graphql_interfaces.clear()
            GeneralManagerMeta.pending_attribute_initialization.clear()
            PathMap.mapping.clear()
            if hasattr(PathMap, "instance"):
                delattr(PathMap, "instance")

    def test_setup_test_schema_supports_find_path_tool(self) -> None:
        module = _load_run_chat_evals_module()

        module.setup_test_schema()

        try:
            result = execute_chat_tool(
                "find_path",
                {
                    "from_manager": "ProjectManager",
                    "to_manager": "MaterialManager",
                },
                None,
            )
            assert result == ["parts", "material"]
            old_seed = PathMap.mapping.get(("MaterialManager", "ProjectManager"))
            assert not (
                isinstance(old_seed, SimpleNamespace)
                and old_seed.path == ["material", "parts"]
            )
            assert PathMap.mapping[("ProjectManager", "MaterialManager")].path == [
                "parts",
                "material",
            ]
        finally:
            clear_schema_index_cache()
            GraphQL.reset_registry()
            GeneralManagerMeta.all_classes.clear()
            GeneralManagerMeta.pending_graphql_interfaces.clear()
            GeneralManagerMeta.pending_attribute_initialization.clear()
            PathMap.mapping.clear()
            if hasattr(PathMap, "instance"):
                delattr(PathMap, "instance")

    def test_large_schema_fixture_registers_many_managers(self) -> None:
        from general_manager.chat.evals.fixtures import setup_large_schema

        setup_large_schema(manager_count=60, chain_length=6)

        try:
            assert len(GraphQL.manager_registry) == 60
            assert "SyntheticManager01" in GraphQL.manager_registry
            assert "SyntheticManager60" in GraphQL.manager_registry
        finally:
            clear_schema_index_cache()
            GraphQL.reset_registry()
            GeneralManagerMeta.all_classes.clear()
            GeneralManagerMeta.pending_graphql_interfaces.clear()
            GeneralManagerMeta.pending_attribute_initialization.clear()
            PathMap.mapping.clear()
            if hasattr(PathMap, "instance"):
                delattr(PathMap, "instance")

    def test_setup_test_schema_preserves_seeded_paths_after_missing_lookup(
        self,
    ) -> None:
        module = _load_run_chat_evals_module()

        module.setup_test_schema()

        try:
            assert execute_chat_tool(
                "find_path",
                {
                    "from_manager": "ProjectManager",
                    "to_manager": "MaterialManager",
                },
                None,
            ) == ["parts", "material"]
            old_seed = PathMap.mapping.get(("MaterialManager", "ProjectManager"))
            assert not (
                isinstance(old_seed, SimpleNamespace)
                and old_seed.path == ["material", "parts"]
            )
            assert execute_chat_tool(
                "find_path",
                {
                    "from_manager": "PartManager",
                    "to_manager": "MaterialManager",
                },
                None,
            ) == ["material"]
        finally:
            clear_schema_index_cache()
            GraphQL.reset_registry()
            GeneralManagerMeta.all_classes.clear()
            GeneralManagerMeta.pending_graphql_interfaces.clear()
            GeneralManagerMeta.pending_attribute_initialization.clear()
            PathMap.mapping.clear()
            if hasattr(PathMap, "instance"):
                delattr(PathMap, "instance")

    def test_resolve_ollama_base_url_remaps_localhost_to_docker_host(self) -> None:
        module = _load_run_chat_evals_module()

        with patch.object(
            module,
            "_host_port_reachable",
            side_effect=lambda host, _port: host == "host.docker.internal",
        ):
            resolved, remapped = module._resolve_ollama_base_url(
                "http://127.0.0.1:11434"
            )

        assert resolved == "http://host.docker.internal:11434"
        assert remapped is True

    def test_resolve_ollama_base_url_keeps_reachable_localhost(self) -> None:
        module = _load_run_chat_evals_module()

        with patch.object(module, "_host_port_reachable", return_value=True):
            resolved, remapped = module._resolve_ollama_base_url(
                "http://127.0.0.1:11434"
            )

        assert resolved == "http://127.0.0.1:11434"
        assert remapped is False

    def test_all_datasets_parse_without_error(self) -> None:
        for name in list_datasets():
            cases = load_dataset(name)
            assert len(cases) > 0, f"Dataset {name} is empty"

    def test_tier0_cases_define_product_contracts(self) -> None:
        missing = []
        for dataset in list_datasets():
            for case in load_dataset(dataset):
                if case.tier == 0 and "contract" not in case.expectations:
                    missing.append(f"{dataset}:{case.name}")

        assert missing == []


# ---------------------------------------------------------------------------
# Scoring / EvalResult tests
# ---------------------------------------------------------------------------


class ScoringTests(SimpleTestCase):
    def test_score_case_with_all_expectations(self) -> None:
        case = EvalCase(
            name="test",
            description="test case",
            conversation=[{"user": "hello"}],
            expectations={
                "tool_calls": [{"name": "query"}],
                "results_contain": ["Steel"],
                "results_exclude": ["Mercury"],
                "answer_contains": ["Steel"],
                "answer_excludes": ["Mercury"],
            },
        )
        records = [
            TurnRecord(
                tool_calls=[{"name": "query", "args": {"manager": "M"}}],
                tool_results=[{"data": [{"name": "Steel"}]}],
                answer_chunks=["Steel is a material."],
            )
        ]
        result = _score_case(case, records)
        assert result.passed is True
        assert result.tool_score is not None and result.tool_score.passed
        assert result.result_score is not None and result.result_score.passed
        assert result.answer_score is not None and result.answer_score.passed

    def test_score_case_tool_failure(self) -> None:
        case = EvalCase(
            name="fail_tool",
            description="",
            conversation=[{"user": "x"}],
            expectations={"tool_calls": [{"name": "mutate"}]},
        )
        records = [TurnRecord(tool_calls=[{"name": "query", "args": {}}])]
        result = _score_case(case, records)
        assert result.passed is False

    def test_eval_result_error_means_not_passed(self) -> None:
        case = EvalCase(name="err", description="", conversation=[], expectations={})
        result = EvalResult(case=case, error="boom")
        assert result.passed is False

    def test_eval_result_no_scores_passes(self) -> None:
        case = EvalCase(name="ok", description="", conversation=[], expectations={})
        result = EvalResult(case=case)
        assert result.passed is True

    def test_score_case_aggregates_multi_turn(self) -> None:
        case = EvalCase(
            name="multi",
            description="",
            conversation=[{"user": "a"}, {"user": "b"}],
            expectations={
                "tool_calls": [{"name": "search_managers"}, {"name": "query"}],
                "answer_contains": ["hello", "world"],
            },
        )
        records = [
            TurnRecord(
                tool_calls=[{"name": "search_managers", "args": {}}],
                answer_chunks=["hello"],
            ),
            TurnRecord(
                tool_calls=[{"name": "query", "args": {}}],
                answer_chunks=["world"],
            ),
        ]
        result = _score_case(case, records)
        assert result.passed is True

    def test_score_case_uses_product_contract_for_hard_failures(self) -> None:
        case = EvalCase(
            name="read_only_safety",
            description="Read-only prompt must not mutate",
            conversation=[{"user": "Which projects would be affected?"}],
            expectations={
                "contract": {
                    "category": "read_only_safety",
                    "hard": {"forbidden_tools": ["mutate"]},
                }
            },
        )
        record = TurnRecord(
            tool_calls=[{"name": "mutate", "args": {"mutation": "updateProject"}}],
            answer_chunks=["Apollo would be affected."],
        )

        result = _score_case(case, [record])

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.violations == ["Forbidden tool called: mutate"]

    def test_score_case_does_not_fail_for_strategy_deviation(self) -> None:
        case = EvalCase(
            name="demo_query",
            description="Correct answer with skipped recommended discovery",
            conversation=[{"user": "Show Apollo projects"}],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            }
                        ],
                        "answer_contains": ["Apollo"],
                    },
                    "strategy": {
                        "recommended_tool_calls": [{"name": "search_managers"}],
                    },
                }
            },
        )
        record = TurnRecord(
            tool_calls=[{"name": "query", "args": {"manager": "ProjectManager"}}],
            answer_chunks=["Apollo"],
        )

        result = _score_case(case, [record])

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.strategy_deviations == [
            "Recommended tool call missing: search_managers"
        ]

    def test_product_contract_pass_is_not_failed_by_legacy_tool_sequence(
        self,
    ) -> None:
        result = EvalResult(
            case=EvalCase(
                name="legacy_tool_score",
                description="",
                conversation=[],
                expectations={},
            ),
            contract_score=ProductContractScore(
                passed=True,
                category="single_manager_query",
            ),
            tool_score=ToolSequenceScore(
                passed=False,
                expected=[{"name": "search_managers"}],
                actual=[{"name": "query", "args": {"manager": "PartManager"}}],
                mismatches=["Expected tool 'search_managers' not found in sequence"],
            ),
            result_score=ResultAccuracyScore(passed=True),
            answer_score=AnswerQualityScore(passed=True, score=1.0),
        )

        assert result.passed is True


# ---------------------------------------------------------------------------
# Runner helper tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Runner integration tests (with scripted provider)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Reporting tests
# ---------------------------------------------------------------------------


class ReportingTests(SimpleTestCase):
    def _make_result(
        self,
        name: str,
        tool_passed: bool = True,
        result_passed: bool = True,
        answer_passed: bool = True,
        answer_score_val: float = 1.0,
        sense_passed: bool = True,
    ) -> EvalResult:
        case = EvalCase(name=name, description="", conversation=[], expectations={})
        contract_passed = tool_passed and sense_passed
        violations = []
        if not tool_passed:
            violations.append("Required tool call missing")
        if not sense_passed:
            violations.append("Answer defers after a successful query")
        return EvalResult(
            case=case,
            contract_score=ProductContractScore(
                passed=contract_passed,
                category="reporting",
                violations=violations,
                answer_sense=AnswerSenseScore(
                    passed=sense_passed,
                    score=1.0 if sense_passed else 0.5,
                    checks={
                        "no_contradiction": True,
                        "no_unnecessary_deferral": sense_passed,
                    },
                    issues=[]
                    if sense_passed
                    else ["Answer defers after a successful query"],
                ),
            ),
            tool_score=ToolSequenceScore(passed=tool_passed, expected=[], actual=[]),
            result_score=ResultAccuracyScore(passed=result_passed),
            answer_score=AnswerQualityScore(
                passed=answer_passed, score=answer_score_val
            ),
        )

    def test_print_report_summary(self) -> None:
        results = [
            self._make_result("a"),
            self._make_result("b", tool_passed=False),
        ]
        report = print_report(results)
        assert "Tool selection" in report
        assert "Query correctness" in report
        assert "Answer quality" in report
        assert "Answer sense" in report
        assert "Overall" in report
        assert "50%" in report

    def test_print_report_verbose_shows_answer_sense_issues(self) -> None:
        report = print_report(
            [self._make_result("sense_fail", sense_passed=False)],
            verbose=True,
        )

        assert "answer sense: 50%" in report
        assert "Answer defers after a successful query" in report

    def test_print_report_verbose_shows_failures(self) -> None:
        results = [
            self._make_result("fail_case", tool_passed=False),
        ]
        report = print_report(results, verbose=True)
        assert "Failures:" in report
        assert "fail_case" in report

    def test_print_report_verbose_includes_all_failure_details(self) -> None:
        result = EvalResult(
            case=EvalCase(
                name="detailed_fail",
                description="Detailed failure",
                conversation=[],
                expectations={},
            ),
            error="provider offline",
            contract_score=ProductContractScore(
                passed=False,
                category="reporting",
                violations=["Required tool call missing: query"],
                strategy_deviations=["Recommended tool call missing: search_managers"],
                answer_sense=AnswerSenseScore(
                    passed=False,
                    score=0.25,
                    issues=["Answer defers after a successful query"],
                ),
            ),
            tool_score=ToolSequenceScore(
                passed=False,
                expected=[{"name": "query"}],
                actual=[],
                mismatches=["Expected tool 'query' not found in sequence"],
            ),
            result_score=ResultAccuracyScore(
                passed=False,
                missing=["Apollo"],
                unexpected=["Mercury"],
            ),
            answer_score=AnswerQualityScore(
                passed=False,
                score=0.5,
                missing=["Gear"],
                unexpected=["RawQuery"],
            ),
        )

        report = print_report([result], verbose=True)

        assert "error: provider offline" in report
        assert "contract: Required tool call missing: query" in report
        assert "strategy: Recommended tool call missing: search_managers" in report
        assert "answer sense: 25%" in report
        assert "sense: Answer defers after a successful query" in report
        assert "tool: Expected tool 'query' not found in sequence" in report
        assert "missing results: ['Apollo']" in report
        assert "unexpected results: ['Mercury']" in report
        assert "answer score: 50%" in report
        assert "missing in answer: ['Gear']" in report
        assert "unexpected in answer: ['RawQuery']" in report
        assert "Diagnostics:" in report
        assert "provider: provider_error=1" in report
        assert "Next actions:" in report
        assert "[provider/provider_error]" in report

    def test_print_compare_report(self) -> None:
        results_a = [self._make_result("x")]
        results_b = [self._make_result("x", answer_passed=False, answer_score_val=0.5)]
        report = print_compare_report({"ProviderA": results_a, "ProviderB": results_b})
        assert "ProviderA" in report
        assert "ProviderB" in report
        assert "Tool selection" in report
        assert "Overall" in report

    def test_print_report_no_results(self) -> None:
        report = print_report([])
        assert "N/A" in report
