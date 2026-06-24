"""Tests for the chat evaluation framework."""

from __future__ import annotations

import asyncio
import importlib.util
import io
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import graphene
from django.test import SimpleTestCase
from general_manager.api.graphql import GraphQL
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
    is_forbidden_recovery_event,
    list_datasets,
    load_dataset,
    print_compare_report,
    print_report,
    run_case,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
    ToolDefinition,
    TokenUsage,
)
from general_manager.chat.schema_index import clear_schema_index_cache
from general_manager.chat.tools import query
from general_manager.chat.tools import execute_chat_tool
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.path_mapping import PathMap
from tests.utils.simple_manager_interface import BaseTestInterface


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
                    "from_manager": "MaterialManager",
                    "to_manager": "ProjectManager",
                },
                None,
            )
            assert result == ["material", "parts"]
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
                    "from_manager": "MaterialManager",
                    "to_manager": "PartManager",
                },
                None,
            ) == ["material"]
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
# Runner integration tests (with scripted provider)
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    """Provider that returns a pre-defined sequence of events per call."""

    def __init__(self, scripts: list[list]) -> None:
        self._scripts = list(scripts)
        self._call_index = 0
        self.calls: list[dict[str, object]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator:
        self.calls.append({"messages": messages, "tools": tools})
        idx = min(self._call_index, len(self._scripts) - 1)
        self._call_index += 1
        for event in self._scripts[idx]:
            yield event


class RunnerIntegrationTests(SimpleTestCase):
    def test_recovery_policy_forbids_answer_rewrites_and_demo_injections(self) -> None:
        assert is_forbidden_recovery_event("repair_contradictory_answer") is True
        assert is_forbidden_recovery_event("repair_raw_query_syntax_answer") is True
        assert (
            is_forbidden_recovery_event("synthesize_answer_from_tool_results") is True
        )
        assert is_forbidden_recovery_event("inject_project_material_query") is True
        assert is_forbidden_recovery_event("inject_target_project_query") is True
        assert is_forbidden_recovery_event("inject_cross_manager_path") is True
        assert is_forbidden_recovery_event("raw_query_syntax_answer") is True
        assert is_forbidden_recovery_event("answer_defers_after_query") is True
        assert is_forbidden_recovery_event("answer_contradicts_non_empty_query") is True
        assert is_forbidden_recovery_event("answer_omits_filter_value") is True
        assert is_forbidden_recovery_event("answer_omits_requested_row_type") is True
        assert is_forbidden_recovery_event("missing_tool_call") is False
        assert is_forbidden_recovery_event("inject_anchor_relation_query") is False
        assert is_forbidden_recovery_event("inject_relation_query_fields") is False

    """Test the runner with a scripted provider and mocked tools."""

    def setUp(self) -> None:
        GraphQL.reset_registry()
        GeneralManagerMeta.all_classes.clear()
        GeneralManagerMeta.pending_graphql_interfaces.clear()
        GeneralManagerMeta.pending_attribute_initialization.clear()
        PathMap.mapping.clear()
        if hasattr(PathMap, "instance"):
            delattr(PathMap, "instance")

        class MatInterface(BaseTestInterface):
            @staticmethod
            def get_attribute_types() -> dict[str, dict[str, object]]:
                return {"name": {"type": str}, "density": {"type": float}}

        class MatManager(GeneralManager):
            Interface = MatInterface

        class PartInterface(BaseTestInterface):
            @staticmethod
            def get_attribute_types() -> dict[str, dict[str, object]]:
                return {"name": {"type": str}}

        class PartManager(GeneralManager):
            Interface = PartInterface

        self.MatManager = MatManager
        self.PartManager = PartManager

        class MaterialType(graphene.ObjectType):
            """Materials catalog."""

            name = graphene.String()
            density = graphene.Float()

        class PartType(graphene.ObjectType):
            """Parts catalog."""

            name = graphene.String()

        GraphQL.graphql_type_registry = {
            "MatManager": MaterialType,
            "PartManager": PartType,
        }
        GraphQL.graphql_filter_type_registry = {}
        GraphQL.manager_registry = {
            "MatManager": MatManager,
            "PartManager": PartManager,
        }
        GraphQL._schema = graphene.Schema(
            query=type("Query", (graphene.ObjectType,), {})
        )

        PathMap.mapping[("PartManager", "MatManager")] = SimpleNamespace(
            path=["material"]
        )

    def tearDown(self) -> None:
        clear_schema_index_cache()
        GraphQL.reset_registry()
        GeneralManagerMeta.all_classes.clear()
        GeneralManagerMeta.pending_graphql_interfaces.clear()
        GeneralManagerMeta.pending_attribute_initialization.clear()
        PathMap.mapping.clear()
        if hasattr(PathMap, "instance"):
            delattr(PathMap, "instance")
        super().tearDown()

    def test_run_case_with_scripted_provider(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1", name="search_managers", args={"query": "parts"}
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="We have Bolt and Gear parts."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="test_scripted",
            description="Scripted provider test",
            conversation=[{"user": "What parts do we have?"}],
            expectations={
                "tool_calls": [{"name": "search_managers"}],
                "answer_contains": ["Bolt", "Gear"],
            },
        )
        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value=[{"manager": "PartManager", "description": "Parts catalog."}],
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "search_managers", "description": "Search"}],
                )
            )

        assert result.tool_score is not None
        assert result.tool_score.passed is True
        assert result.answer_score is not None
        assert result.answer_score.passed is True
        assert result.passed is True

    def test_run_case_streams_chat_transcript(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1", name="search_managers", args={"query": "parts"}
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="We have Bolt and Gear parts."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="streamed_case",
            description="Scripted provider test",
            conversation=[{"user": "What parts do we have?"}],
            expectations={},
        )
        output = io.StringIO()

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value=[{"manager": "PartManager", "description": "Parts catalog."}],
        ):
            asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "search_managers", "description": "Search"}],
                    stream=output,
                )
            )

        transcript = output.getvalue()
        assert "=== streamed_case ===" in transcript
        assert "user: What parts do we have?" in transcript
        assert 'tool_call search_managers: {"query": "parts"}' in transcript
        assert "tool_result search_managers:" in transcript
        assert "assistant: We have Bolt and Gear parts." in transcript

    def test_run_case_resumes_with_tool_result_without_assistant_placeholder(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1", name="search_managers", args={"query": "parts"}
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="We have Bolt and Gear parts."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="tool_history_case",
            description="Tool history shape test",
            conversation=[{"user": "What parts do we have?"}],
            expectations={},
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value=[{"manager": "PartManager", "description": "Parts catalog."}],
        ):
            asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "search_managers", "description": "Search"}],
                )
            )

        second_call_messages = provider.calls[1]["messages"]
        assert second_call_messages[-1].role == "tool"
        assert "PartManager" in second_call_messages[-1].content
        assert second_call_messages[-2].role == "assistant"
        assert (
            second_call_messages[-2].content
            == "Called tool search_managers. The next message is the tool result; answer from it exactly."
        )
        assert all(
            message.content != "[tool:search_managers]"
            for message in second_call_messages
        )

    def test_run_case_catches_provider_exception(self) -> None:
        class _FailProvider:
            async def complete(self, messages: Any, tools: Any) -> AsyncIterator:
                raise ValueError("provider exploded")  # noqa: TRY003
                yield  # type: ignore[misc]

        case = EvalCase(
            name="error_case",
            description="",
            conversation=[{"user": "hello"}],
            expectations={},
        )
        result = asyncio.run(run_case(_FailProvider(), case, []))
        assert result.passed is False
        assert result.error is not None
        assert "provider exploded" in result.error

    def test_run_case_can_recover_missing_tool_call_before_answer(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    TextChunkEvent(content="Steel and Cobalt."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    ToolCallEvent(
                        id="0",
                        name="search_managers",
                        args={"query": "all managers"},
                    ),
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={"manager": "MaterialManager", "fields": ["name"]},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Steel and Cobalt are the dense materials."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="recover",
            description="Recover missing tool",
            conversation=[{"user": "Which materials have density above 7?"}],
            expectations={
                "tool_calls": [{"name": "query"}],
                "answer_contains": ["Steel"],
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value={"data": [{"name": "Steel"}, {"name": "Cobalt"}]},
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True

    def test_run_case_can_recover_empty_response_after_tool_result(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="find_path",
                        args={
                            "from_manager": "ProjectManager",
                            "to_manager": "PartManager",
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [DoneEvent(usage=TokenUsage())],
                [
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={"manager": "ProjectManager", "fields": ["name"]},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo contains cobalt parts."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="recover_empty_after_tool",
            description="Recover empty response after path lookup",
            conversation=[{"user": "What projects contain parts with cobalt?"}],
            expectations={
                "tool_calls": [{"name": "query"}],
                "answer_contains": ["Apollo"],
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "find_path":
                return ["parts"]
            if name == "query":
                return {"data": [{"name": "Apollo"}]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "find_path", "description": "Find path"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        recovery_messages = provider.calls[2]["messages"]
        assert any(
            message.role == "system"
            and "previous tool result is not a final answer" in message.content
            for message in recovery_messages
        )

    def test_run_case_recovers_relationship_answer_without_find_path(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "MatManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="get_manager_schema",
                        args={"manager": "PartManager"},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool get_manager_schema. The next message is "
                            "the tool result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    ToolCallEvent(
                        id="3",
                        name="find_path",
                        args={
                            "from_manager": "PartManager",
                            "to_manager": "MatManager",
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="The path is part to material through material."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="recover_relationship_path",
            description="Recover missing relation path",
            conversation=[{"user": "How are parts related to materials?"}],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [{"name": "find_path"}],
                        "answer_contains": ["material", "part"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {"manager": args["manager"], "fields": ["name"]}
            if name == "find_path":
                return ["material"]
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "find_path", "description": "Find path"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        recovery_messages = provider.calls[2]["messages"]
        assert any(
            message.role == "system" and "relationship question" in message.content
            for message in recovery_messages
        )

    def test_run_case_recovers_failed_query_before_final_answer(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "fields": ["name", {"parts": ["name", "cost"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool query. The next message is the tool "
                            "result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    ToolCallEvent(
                        id="3",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "fields": ["name", {"parts": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo uses Gear."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="recover_failed_query",
            description="Recover invalid field query",
            conversation=[{"user": "What parts are used in Apollo?"}],
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
                        "results_contain": ["Gear"],
                        "answer_contains": ["Gear"],
                    },
                },
                "results_contain": ["Gear"],
                "answer_contains": ["Gear"],
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {"manager": "ProjectManager", "fields": ["name"]}
            if name == "query" and "cost" in str(args):
                return {"error": "Cannot query field 'cost' on type 'PartType'."}
            if name == "query":
                return {"data": [{"name": "Apollo", "parts": [{"name": "Gear"}]}]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert "inject_failed_query_fields_retry" in result.recovery_events

    def test_run_case_recovers_model_exploration_without_search(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="find_path",
                        args={
                            "from_manager": "ProjectManager",
                            "to_manager": "MatManager",
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="ProjectManager connects to MatManager through parts."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    ToolCallEvent(
                        id="3",
                        name="search_managers",
                        args={"query": "projects materials"},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="Project and Material managers are connected."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="recover_discovery",
            description="Recover missing model discovery",
            conversation=[
                {"user": "Help me explore the data model for projects and materials."}
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [{"name": "search_managers"}],
                        "answer_contains": ["Project", "Material"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {"manager": args["manager"], "fields": ["name"]}
            if name == "find_path":
                return ["parts", "material"]
            if name == "search_managers":
                return [{"manager": "ProjectManager"}, {"manager": "MatManager"}]
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "find_path", "description": "Find path"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True

    def test_run_case_recovers_unavailable_manager_name_echo(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    TextChunkEvent(content="I can look that up."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    ToolCallEvent(
                        id="1",
                        name="search_managers",
                        args={"query": "vehicle"},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="I do not see VehicleManager in the exposed managers."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="I still do not have access to VehicleManager."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="The VehicleManager is not available here."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "I do not have access to that requested manager "
                            "(VehicleManager)."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="I do not have access to that requested manager."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="recover_unknown_manager_echo",
            description="Recover invalid manager echo",
            conversation=[{"user": "Show me data from the VehicleManager"}],
            expectations={
                "contract": {
                    "category": "manager_discovery",
                    "hard": {
                        "required_tool_calls": [{"name": "search_managers"}],
                        "answer_excludes": ["VehicleManager"],
                    },
                }
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value=[],
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "search_managers", "description": "Search"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert "answer_without_query" not in result.recovery_events
        assert "unavailable_manager_echo_retry" in result.recovery_events
        assert "unavailable_manager_echo_final_retry" in result.recovery_events
        assert "unavailable_manager_echo_minimal_retry" in result.recovery_events
        recovery_prompts = [
            message.content
            for call in provider.calls
            for message in call["messages"]
            if isinstance(message, Message)
            and message.role == "system"
            and "unavailable manager" in message.content
        ]
        assert any(
            "user-provided token ending in Manager" in prompt
            for prompt in recovery_prompts
        )
        assert all("VehicleManager" not in prompt for prompt in recovery_prompts)
        recovery_messages = provider.calls[3]["messages"]
        assert any(
            message.role == "system"
            and "Do not repeat, spell, quote, or copy unavailable manager names"
            in message.content
            for message in recovery_messages
        )

    def test_run_case_gets_model_final_answer_after_tool_budget(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"name": "Apollo"},
                            "fields": ["name", {"parts": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo uses Gear."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="tool_budget_final_answer",
            description="Successful rows should get a model answer at tool budget",
            conversation=[{"user": "What parts are used in Apollo?"}],
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
                        "results_contain": ["Gear"],
                        "answer_contains": ["Gear"],
                    },
                }
            },
        )

        with (
            patch(
                "general_manager.chat.evals.runner.MAX_TOOL_ITERATIONS",
                1,
            ),
            patch(
                "general_manager.chat.evals.runner.execute_chat_tool",
                return_value={
                    "data": [{"name": "Apollo", "parts": [{"name": "Gear"}]}]
                },
            ),
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert result.recovery_events == ["final_answer_after_tool_budget"]

    def test_run_case_injects_schema_pruned_retry_after_failed_query_fields(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {
                                "name": "Apollo",
                                "parts__name": "existing",
                            },
                            "fields": [
                                "name",
                                {"parts": ["name", "cost", "supplier"]},
                            ],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="No matching records were found for Apollo parts."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo uses Gear."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="schema_pruned_failed_query_retry",
            description="Invalid query fields should be pruned and retried",
            conversation=[{"user": "What parts are used in the Apollo project?"}],
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
                        "results_contain": ["Gear"],
                        "answer_contains": ["Gear"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {
                    "manager": "ProjectManager",
                    "fields": ["name"],
                    "relations": [{"name": "parts", "target": "PartManager"}],
                    "filters": ["name", "parts__name"],
                }
            if name == "query" and "cost" in str(args):
                return {"error": "Cannot query field 'cost' on type 'PartType'."}
            if name == "query":
                assert args["filters"] == {"name": "Apollo"}
                assert args["fields"] == ["name", {"parts": ["name"]}]
                return {
                    "data": [
                        {
                            "name": "Apollo",
                            "parts": [{"name": "Gear"}],
                        }
                    ]
                }
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert result.recovery_events == ["inject_failed_query_fields_retry"]

    def test_run_case_retries_stringified_field_selection_after_syntax_error(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"name": "Apollo"},
                            "fields": (
                                '["name", {"parts": ["name", {"material": ["name"]}]}]'
                            ),
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo uses Gear."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="stringified_field_selection_retry",
            description="Stringified relation fields should be parsed and retried",
            conversation=[{"user": "What parts are used in the Apollo project?"}],
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
                        "results_contain": ["Gear"],
                        "answer_contains": ["Gear"],
                    },
                },
                "results_contain": ["Gear"],
                "answer_contains": ["Gear"],
            },
        )
        queries: list[dict[str, Any]] = []

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {
                    "manager": "ProjectManager",
                    "fields": ["name"],
                    "relations": [{"name": "parts", "target": "PartManager"}],
                    "filters": ["name", "parts__material__name", "parts__name"],
                }
            if name == "query":
                queries.append(args)
                if isinstance(args.get("fields"), str):
                    return {"error": "Syntax Error: Expected Name, found '['."}
                assert args["fields"] == ["name", {"parts": ["name"]}]
                assert args["filters"] == {"name": "Apollo"}
                return {
                    "data": [
                        {
                            "name": "Apollo",
                            "parts": [
                                {
                                    "name": "Gear",
                                    "material": {"name": "Cobalt"},
                                }
                            ],
                        }
                    ]
                }
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert result.recovery_events == ["inject_failed_query_fields_retry"]
        assert len(queries) == 2

    def test_run_case_injects_unfiltered_target_query_for_inventory_list(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "PartManager"},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="No matching parts were found."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="Inventory parts are Bolt, Gear, and Bearing."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="inventory_parts_unfiltered_query",
            description="Inventory list questions should not invent filters",
            conversation=[{"user": "What parts do we have in inventory?"}],
            expectations={
                "contract": {
                    "category": "single_manager_query",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {"manager": "PartManager"},
                            }
                        ],
                        "results_contain": ["Bolt", "Gear", "Bearing"],
                        "answer_contains": ["Bolt", "Gear", "Bearing"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {
                    "manager": "PartManager",
                    "fields": ["name"],
                    "relations": [{"name": "material", "target": "MaterialManager"}],
                    "filters": ["name", "material__name", "material__name__icontains"],
                }
            if name == "query":
                assert args["manager"] == "PartManager"
                assert "filters" not in args
                return {
                    "data": [
                        {"name": "Bolt"},
                        {"name": "Gear"},
                        {"name": "Bearing"},
                    ]
                }
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert result.recovery_events == ["inject_target_manager_list_query"]

    def test_run_case_injects_discovery_search_after_ignored_recovery(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="ProjectManager and MatManager are connected."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="Project and Material managers are connected."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="Project and Material managers are connected."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="inject_discovery",
            description="Inject ignored discovery search",
            conversation=[
                {"user": "Help me explore the data model for projects and materials."}
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [{"name": "search_managers"}],
                        "answer_contains": ["Project", "Material"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {"manager": args["manager"], "fields": ["name"]}
            if name == "search_managers":
                return [{"manager": "ProjectManager"}, {"manager": "MatManager"}]
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "get_manager_schema", "description": "Schema"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

    def test_run_case_injects_relation_query_when_scalar_query_omits_relation(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"name": "Apollo"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="I found Apollo but could not retrieve its parts."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo uses Gear."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="inject_relation_query",
            description="Repair scalar-only relation query",
            conversation=[{"user": "What parts are used in the Apollo project?"}],
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
                        "results_contain": ["Gear"],
                        "answer_contains": ["Gear"],
                    },
                },
                "results_contain": ["Gear"],
                "answer_contains": ["Gear"],
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {
                    "manager": "ProjectManager",
                    "fields": ["name"],
                    "relations": [{"name": "parts", "target": "PartManager"}],
                    "filters": ["name"],
                }
            if name == "query" and any(
                isinstance(field, dict) and "parts" in field for field in args["fields"]
            ):
                return {"data": [{"name": "Apollo", "parts": [{"name": "Gear"}]}]}
            if name == "query":
                return {"data": [{"name": "Apollo"}]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

    def test_run_case_does_not_inject_project_query_from_part_material_result(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="find_path",
                        args={
                            "from_manager": "MaterialManager",
                            "to_manager": "ProjectManager",
                        },
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "PartManager",
                            "filters": {"material__name": "Cobalt"},
                            "fields": ["name", {"material": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool find_path. The next message is the tool "
                            "result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo would be affected."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="inject_project_query",
            description="Recover target project query",
            conversation=[
                {"user": "Which projects would be affected if cobalt parts changed?"}
            ],
            expectations={
                "contract": {
                    "category": "read_only_safety",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            }
                        ],
                        "results_contain": ["Apollo"],
                        "answer_contains": ["Apollo"],
                        "forbidden_tools": ["mutate"],
                    },
                },
                "results_contain": ["Apollo"],
                "answer_contains": ["Apollo"],
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "find_path":
                return ["material", "parts"]
            if name == "get_manager_schema":
                return {"manager": args["manager"], "fields": ["name"]}
            if name == "query" and args["manager"] == "PartManager":
                return {"data": [{"name": "Gear", "material": {"name": "Cobalt"}}]}
            if name == "query" and args["manager"] == "ProjectManager":
                return {"data": [{"name": "Apollo"}]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "find_path", "description": "Find path"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "inject_target_project_query" not in result.recovery_events

    def test_run_case_injects_generic_target_manager_query_from_schema(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="search_managers",
                        args={"query": "cobalt material"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="find_path",
                        args={
                            "from_manager": "MaterialManager",
                            "to_manager": "ProjectManager",
                        },
                    ),
                    ToolCallEvent(
                        id="3",
                        name="query",
                        args={
                            "manager": "MaterialManager",
                            "filters": {"name": "Cobalt"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool query. The next message is the tool "
                            "result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo would be affected."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="generic_target_manager_query",
            description="Retry the target manager when only constraint rows were queried",
            conversation=[
                {"user": "Which projects would be affected if cobalt parts changed?"}
            ],
            expectations={
                "contract": {
                    "category": "read_only_safety",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            }
                        ],
                        "results_contain": ["Apollo"],
                        "answer_contains": ["Apollo"],
                        "forbidden_tools": ["mutate"],
                    },
                },
                "results_contain": ["Apollo"],
                "answer_contains": ["Apollo"],
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "search_managers":
                return [
                    {
                        "manager": "MaterialManager",
                        "description": "Materials catalog",
                        "fields": ["name"],
                        "relations": [],
                        "filters": ["name"],
                    },
                    {
                        "manager": "PartManager",
                        "description": "Parts catalog",
                        "fields": ["name"],
                        "relations": [
                            {"name": "material", "target": "MaterialManager"}
                        ],
                        "filters": ["material__name", "name"],
                    },
                    {
                        "manager": "ProjectManager",
                        "description": "Projects catalog",
                        "fields": ["name"],
                        "relations": [{"name": "parts", "target": "PartManager"}],
                        "filters": [
                            "name",
                            "parts__name",
                            "parts__material__name",
                            "parts__material__name__icontains",
                        ],
                    },
                ]
            if name == "find_path":
                return ["material", "parts"]
            if name == "query" and args["manager"] == "MaterialManager":
                return {"data": [{"name": "Cobalt"}]}
            if name == "query" and args["manager"] == "ProjectManager":
                assert args["filters"] == {"parts__material__name__icontains": "Cobalt"}
                assert args["fields"] == [
                    "name",
                    {"parts": ["name", {"material": ["name"]}]},
                ]
                return {"data": [{"name": "Apollo"}]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "find_path", "description": "Find path"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert "inject_target_manager_filter_query" in result.recovery_events

    def test_run_case_injects_target_schema_after_path_before_target_query(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="find_path",
                        args={
                            "from_manager": "PartManager",
                            "to_manager": "ProjectManager",
                        },
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "PartManager",
                            "filters": {"material__name": "cobalt"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="No matching records were found."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="No matching records were found."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo would be affected."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="target_schema_after_path",
            description="Fetch requested target schema after a path-only discovery",
            conversation=[
                {"user": "Which projects would be affected if cobalt parts changed?"}
            ],
            expectations={
                "contract": {
                    "category": "read_only_safety",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            }
                        ],
                        "results_contain": ["Apollo"],
                        "answer_contains": ["Apollo"],
                        "forbidden_tools": ["mutate"],
                    },
                },
                "results_contain": ["Apollo"],
                "answer_contains": ["Apollo"],
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "find_path":
                return ["parts"]
            if name == "get_manager_schema":
                assert args == {"manager": "ProjectManager"}
                return {
                    "manager": "ProjectManager",
                    "description": "Projects catalog",
                    "fields": ["name"],
                    "relations": [{"name": "parts", "target": "PartManager"}],
                    "filters": [
                        "name",
                        "parts__name",
                        "parts__material__name",
                        "parts__material__name__icontains",
                    ],
                }
            if name == "query" and args["manager"] == "PartManager":
                return {"data": [{"name": "Gear"}]}
            if name == "query" and args["manager"] == "ProjectManager":
                assert args["filters"] == {"parts__material__name__icontains": "cobalt"}
                assert args["fields"] == [
                    "name",
                    {"parts": ["name", {"material": ["name"]}]},
                ]
                return {
                    "data": [
                        {
                            "name": "Apollo",
                            "parts": [
                                {
                                    "name": "Gear",
                                    "material": {"name": "Cobalt"},
                                }
                            ],
                        }
                    ]
                }
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "find_path", "description": "Find path"},
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert result.recovery_events == [
            "inject_target_schema_after_path",
            "inject_target_manager_filter_query",
        ]

    def test_run_case_blocks_target_query_with_anchor_value(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="find_path",
                        args={
                            "from_manager": "ProjectManager",
                            "to_manager": "PartManager",
                        },
                    ),
                    ToolCallEvent(
                        id="2",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="3",
                        name="get_manager_schema",
                        args={"manager": "PartManager"},
                    ),
                    ToolCallEvent(
                        id="4",
                        name="query",
                        args={
                            "manager": "PartManager",
                            "filters": {"parts__material__name": "Apollo"},
                            "fields": ["name", {"material": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo uses Gear."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="anchor_relation_query",
            description="Anchor entity relation rows should query the anchor manager",
            conversation=[{"user": "What parts are used in the Apollo project?"}],
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
                        "results_contain": ["Gear"],
                        "answer_contains": ["Gear"],
                    },
                }
            },
        )
        query_managers: list[str] = []

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "find_path":
                return ["parts"]
            if name == "get_manager_schema" and args["manager"] == "ProjectManager":
                return {
                    "manager": "ProjectManager",
                    "description": "Projects catalog",
                    "fields": ["name"],
                    "relations": [{"name": "parts", "target": "PartManager"}],
                    "filters": [
                        "name",
                        "name__icontains",
                        "parts__name",
                        "parts__material__name",
                    ],
                }
            if name == "get_manager_schema" and args["manager"] == "PartManager":
                return {
                    "manager": "PartManager",
                    "description": "Parts catalog",
                    "fields": ["name"],
                    "relations": [{"name": "material", "target": "MaterialManager"}],
                    "filters": ["name", "material__name", "material__name__icontains"],
                }
            if name == "query":
                query_managers.append(str(args["manager"]))
                if args["manager"] == "ProjectManager":
                    assert args["filters"] == {"name": "Apollo"}
                    assert args["fields"] == ["name", {"parts": ["name"]}]
                    return {
                        "data": [
                            {
                                "name": "Apollo",
                                "parts": [{"name": "Gear"}],
                            }
                        ]
                    }
                return {"data": []}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "find_path", "description": "Find path"},
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert query_managers == ["ProjectManager"]
        assert result.recovery_events == ["inject_anchor_relation_query"]

    def test_run_case_refines_unfiltered_target_manager_query(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="search_managers",
                        args={"query": "project"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="3",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {},
                            "fields": [
                                "name",
                                {"parts": ["name", {"material": ["name"]}]},
                            ],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo would be affected."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo would be affected."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="refine_unfiltered_target_query",
            description="Target query should be refined with grounded relation filter",
            conversation=[
                {"user": "Which projects would be affected if cobalt parts changed?"}
            ],
            expectations={
                "contract": {
                    "category": "read_only_safety",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            }
                        ],
                        "results_contain": ["Apollo"],
                        "results_exclude": ["Mercury"],
                        "answer_contains": ["Apollo"],
                        "answer_excludes": ["Mercury"],
                        "forbidden_tools": ["mutate"],
                    },
                },
                "results_contain": ["Apollo"],
                "results_exclude": ["Mercury"],
                "answer_contains": ["Apollo"],
                "answer_excludes": ["Mercury"],
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "search_managers":
                return [
                    {
                        "manager": "ProjectManager",
                        "description": "Projects catalog",
                        "fields": ["name"],
                        "relations": [{"name": "parts", "target": "PartManager"}],
                        "filters": [
                            "name",
                            "parts__name",
                            "parts__material__name",
                            "parts__material__name__icontains",
                        ],
                    }
                ]
            if name == "get_manager_schema":
                return {
                    "manager": "ProjectManager",
                    "description": "Projects catalog",
                    "fields": ["name"],
                    "relations": [{"name": "parts", "target": "PartManager"}],
                    "filters": [
                        "name",
                        "parts__name",
                        "parts__material__name",
                        "parts__material__name__icontains",
                    ],
                }
            if name == "query" and not args.get("filters"):
                raise AssertionError
            if name == "query":
                assert args["filters"] == {"parts__material__name__icontains": "cobalt"}
                return {
                    "data": [
                        {
                            "name": "Apollo",
                            "parts": [
                                {
                                    "name": "Gear",
                                    "material": {"name": "Cobalt"},
                                }
                            ],
                        }
                    ]
                }
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert result.recovery_events == ["inject_target_manager_filter_query"]

    def test_run_case_does_not_treat_scalar_lookup_filter_as_relation_filter(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "MaterialManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "MaterialManager",
                            "filters": {"density__gt": "greater"},
                            "fields": ["name", "density"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    ToolCallEvent(
                        id="3",
                        name="query",
                        args={
                            "manager": "MaterialManager",
                            "filters": {"density__gt": 5},
                            "fields": ["name", "density"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Steel and Cobalt are above 5."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="scalar_lookup_not_target_filter",
            description="Scalar lookup filters should not trigger relation recovery",
            conversation=[{"user": "Which materials have density greater than 5?"}],
            expectations={
                "contract": {
                    "category": "single_manager_query",
                    "hard": {
                        "required_tool_calls": [{"name": "query"}],
                        "results_contain": ["Steel", "Cobalt"],
                        "results_exclude": ["Aluminum"],
                        "answer_contains": ["Steel", "Cobalt"],
                        "answer_excludes": ["Aluminum"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {
                    "manager": "MaterialManager",
                    "fields": ["name", "density"],
                    "filters": ["name", "density__gt"],
                    "relations": [],
                }
            if args.get("filters") == {"density__gt": "greater"}:
                return {"error": 'Float cannot represent non numeric value: "greater"'}
            if args.get("filters") == {"density__gt": 5}:
                return {
                    "data": [
                        {"name": "Steel", "density": 7.8},
                        {"name": "Cobalt", "density": 8.9},
                    ]
                }
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert "inject_target_manager_filter_query" not in result.recovery_events

    def test_run_case_does_not_invent_relation_filter_from_manager_word(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "PartManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "PartManager",
                            "fields": ["name", {"material": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Bolt uses Steel. Gear uses Cobalt."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="manager_word_not_relation_filter",
            description="Manager/schema wording should not become a relation filter",
            conversation=[
                {"user": "In PartManager, show each part with its material name."}
            ],
            expectations={
                "contract": {
                    "category": "schema_inspection",
                    "hard": {
                        "required_tool_calls": [{"name": "query"}],
                        "results_contain": ["Bolt", "Steel", "Gear", "Cobalt"],
                        "answer_contains": ["Bolt", "Steel", "Gear", "Cobalt"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {
                    "manager": "PartManager",
                    "fields": ["name"],
                    "filters": ["name", "material__name", "material__name__icontains"],
                    "relations": [{"name": "material", "target": "MaterialManager"}],
                }
            if name == "query":
                assert "filters" not in args
                return {
                    "data": [
                        {"name": "Bolt", "material": {"name": "Steel"}},
                        {"name": "Gear", "material": {"name": "Cobalt"}},
                    ]
                }
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert "inject_target_manager_filter_query" not in result.recovery_events

    def test_run_case_injects_generic_target_manager_query_after_discovery(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="search_managers",
                        args={"query": "project"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="search_managers",
                        args={"query": "part"},
                    ),
                    ToolCallEvent(
                        id="3",
                        name="search_managers",
                        args={"query": "material"},
                    ),
                    ToolCallEvent(
                        id="4",
                        name="get_manager_schema",
                        args={"manager": "PartManager"},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool get_manager_schema. The next message is "
                            "the tool result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "ProjectManager reaches MaterialManager through parts. "
                            "Mercury uses Bearing made from Aluminum."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="generic_target_query_after_discovery",
            description="Discovery should retry target manager data query",
            conversation=[
                {
                    "user": (
                        "I need to find which projects use aluminum parts. "
                        "Help me explore the data model first."
                    )
                }
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [
                            {"name": "search_managers"},
                            {"name": "find_path"},
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            },
                        ],
                        "results_contain": ["Mercury", "Bearing", "Aluminum"],
                        "answer_contains": [
                            "Project",
                            "Material",
                            "Mercury",
                            "Bearing",
                            "Aluminum",
                        ],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "search_managers":
                return [
                    {
                        "manager": "ProjectManager",
                        "description": "Projects catalog",
                        "fields": ["name"],
                        "relations": [{"name": "parts", "target": "PartManager"}],
                        "filters": [
                            "name",
                            "parts__name",
                            "parts__material__name",
                            "parts__material__name__icontains",
                        ],
                    },
                    {
                        "manager": "PartManager",
                        "description": "Parts catalog",
                        "fields": ["name"],
                        "relations": [
                            {"name": "material", "target": "MaterialManager"}
                        ],
                        "filters": ["material__name", "name"],
                    },
                    {
                        "manager": "MaterialManager",
                        "description": "Materials catalog",
                        "fields": ["name"],
                        "relations": [],
                        "filters": ["name"],
                    },
                ]
            if name == "get_manager_schema":
                return {
                    "manager": args["manager"],
                    "fields": ["name"],
                    "relations": [{"name": "material", "target": "MaterialManager"}],
                    "filters": ["material__name", "name"],
                }
            if name == "find_path":
                assert args == {
                    "from_manager": "ProjectManager",
                    "to_manager": "MaterialManager",
                }
                return ["parts", "material"]
            if name == "query":
                assert args["manager"] == "ProjectManager"
                assert args["filters"] == {
                    "parts__material__name__icontains": "aluminum"
                }
                return {
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
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "find_path", "description": "Find path"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert "inject_target_manager_filter_query" in result.recovery_events

    def test_run_case_injects_generic_path_for_relation_filter_query(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="search_managers",
                        args={"query": "project material"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"parts__material__name": "Aluminum"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool query. The next message is the tool "
                            "result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "ProjectManager reaches MaterialManager through parts. "
                            "Mercury uses Bearing made from Aluminum."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="generic_path_for_relation_filter_query",
            description="Relation-filter query should still discover path",
            conversation=[
                {
                    "user": (
                        "I need to find which projects use aluminum parts. "
                        "Help me explore the data model first."
                    )
                }
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [
                            {"name": "search_managers"},
                            {"name": "find_path"},
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            },
                        ],
                        "results_contain": ["Mercury"],
                        "answer_contains": ["Project", "Material", "Mercury"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "search_managers":
                return [
                    {
                        "manager": "ProjectManager",
                        "description": "Projects catalog",
                        "fields": ["name"],
                        "relations": [{"name": "parts", "target": "PartManager"}],
                        "filters": [
                            "name",
                            "parts__name",
                            "parts__material__name",
                            "parts__material__name__icontains",
                        ],
                    },
                    {
                        "manager": "PartManager",
                        "description": "Parts catalog",
                        "fields": ["name"],
                        "relations": [
                            {"name": "material", "target": "MaterialManager"}
                        ],
                        "filters": ["material__name", "name"],
                    },
                    {
                        "manager": "MaterialManager",
                        "description": "Materials catalog",
                        "fields": ["name"],
                        "relations": [],
                        "filters": ["name"],
                    },
                ]
            if name == "find_path":
                assert args == {
                    "from_manager": "ProjectManager",
                    "to_manager": "MaterialManager",
                }
                return ["parts", "material"]
            if name == "query":
                return {"data": [{"name": "Mercury"}]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "find_path", "description": "Find path"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert "inject_discovered_manager_path" in result.recovery_events

    def test_run_case_does_not_inject_cross_manager_path_after_discovery(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="get_manager_schema",
                        args={"manager": "MaterialManager"},
                    ),
                    ToolCallEvent(
                        id="3",
                        name="search_managers",
                        args={"query": "projects materials"},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool find_path. The next message is the tool "
                            "result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="ProjectManager connects to MaterialManager."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="inject_cross_manager_path",
            description="Recover missing path after discovery",
            conversation=[
                {"user": "Help me explore which projects use aluminum parts."}
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [{"name": "find_path"}],
                        "answer_contains": ["Project", "Material"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {"manager": args["manager"], "fields": ["name"]}
            if name == "search_managers":
                return [{"manager": "ProjectManager"}, {"manager": "MaterialManager"}]
            if name == "find_path":
                return ["parts", "material"]
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "find_path", "description": "Find path"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "inject_cross_manager_path" not in result.recovery_events

    def test_run_case_recovers_empty_manager_discovery_search(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="search_managers",
                        args={"query": ""},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool search_managers. The next message is "
                            "the tool result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "MaterialManager, PartManager, and ProjectManager "
                            "are available."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="recover_empty_discovery",
            description="Recover empty discovery search",
            conversation=[{"user": "What kinds of managers can I ask about?"}],
            expectations={
                "contract": {
                    "category": "manager_discovery",
                    "hard": {
                        "required_tool_calls": [{"name": "search_managers"}],
                        "answer_contains": ["Material", "Part", "Project"],
                        "forbidden_tools": ["query", "mutate"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "search_managers" and not args["query"]:
                return []
            if name == "search_managers":
                return [
                    {"manager": "MaterialManager"},
                    {"manager": "PartManager"},
                    {"manager": "ProjectManager"},
                ]
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "search_managers", "description": "Search"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

    def test_run_case_does_not_synthesize_bridge_answer_from_discovery_tools(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="get_manager_schema",
                        args={"manager": "MaterialManager"},
                    ),
                    ToolCallEvent(
                        id="3",
                        name="search_managers",
                        args={"query": "projects material"},
                    ),
                    ToolCallEvent(
                        id="4",
                        name="find_path",
                        args={
                            "from_manager": "ProjectManager",
                            "to_manager": "MaterialManager",
                        },
                    ),
                    ToolCallEvent(
                        id="5",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "fields": ["name"],
                            "filters": {"parts__material__name": "Aluminum"},
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool search_managers. The next message is "
                            "the tool result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="synthesize_discovery_bridge",
            description="Synthesize bridge answer from discovery tools",
            conversation=[
                {"user": "Help me explore which projects use aluminum materials."}
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [
                            {"name": "search_managers"},
                            {"name": "find_path"},
                        ],
                        "answer_contains": ["Project", "Material"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {"manager": args["manager"], "fields": ["name"]}
            if name == "search_managers":
                return [{"manager": "ProjectManager"}, {"manager": "MaterialManager"}]
            if name == "find_path":
                return ["parts", "material"]
            if name == "query":
                return {"data": [{"name": "Mercury"}]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "find_path", "description": "Find path"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert result.recovery_events == ["tool_bridge_answer"]
        assert not any(
            is_forbidden_recovery_event(event) for event in result.recovery_events
        )

    def test_run_case_injects_schema_after_relation_query_without_schema(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "PartManager",
                            "fields": ["name", {"material": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Bolt uses Steel."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Bolt uses Steel."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="inject_schema_after_query",
            description="Recover schema inspection after relation query",
            conversation=[
                {"user": "In PartManager, show each part with material name."}
            ],
            expectations={
                "contract": {
                    "category": "schema_inspection",
                    "hard": {
                        "required_tool_calls": [
                            {"name": "get_manager_schema"},
                            {
                                "name": "query",
                                "args_contain": {"manager": "PartManager"},
                            },
                        ],
                        "answer_contains": ["Bolt", "Steel"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "query":
                return {"data": [{"name": "Bolt", "material": {"name": "Steel"}}]}
            if name == "get_manager_schema":
                return {"manager": args["manager"], "fields": ["name"]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

    def test_run_case_does_not_inject_cross_manager_path_for_traversal_answer(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="get_manager_schema",
                        args={"manager": "MaterialManager"},
                    ),
                    ToolCallEvent(
                        id="3",
                        name="search_managers",
                        args={"query": "all managers"},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "ProjectManager can traverse through parts to "
                            "MaterialManager."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="ProjectManager connects to MaterialManager."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="inject_path_for_traversal_answer",
            description="Recover missing path from traversal prose",
            conversation=[
                {
                    "user": (
                        "I need to find which projects use aluminum parts. "
                        "Help me explore the data model first."
                    )
                }
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [
                            {"name": "search_managers"},
                            {"name": "find_path"},
                        ],
                        "answer_contains": ["Project", "Material"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {"manager": args["manager"], "fields": ["name"]}
            if name == "search_managers":
                return [{"manager": "ProjectManager"}, {"manager": "MaterialManager"}]
            if name == "find_path":
                return ["parts", "material"]
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "find_path", "description": "Find path"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "inject_cross_manager_path" not in result.recovery_events

    def test_run_case_retries_empty_exact_filter_with_icontains(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"parts__material__name": "cobalt"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="No cobalt projects were found."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo uses cobalt parts."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="retry_empty_icontains",
            description="Retry empty exact relation filter",
            conversation=[
                {"user": "Which projects would be affected if cobalt parts changed?"}
            ],
            expectations={
                "contract": {
                    "category": "read_only_safety",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            }
                        ],
                        "results_contain": ["Apollo"],
                        "answer_contains": ["Apollo"],
                        "forbidden_tools": ["mutate"],
                    },
                },
                "results_contain": ["Apollo"],
                "answer_contains": ["Apollo"],
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {
                    "manager": "ProjectManager",
                    "fields": ["name"],
                    "filters": [
                        "parts__material__name",
                        "parts__material__name__icontains",
                    ],
                }
            if name == "query" and "parts__material__name__icontains" in args.get(
                "filters", {}
            ):
                return {"data": [{"name": "Apollo"}]}
            if name == "query":
                return {"data": []}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

    def test_run_case_does_not_synthesize_bridge_answer_from_query_rows(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="0",
                        name="search_managers",
                        args={"query": "all managers"},
                    ),
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"name": "Apollo"},
                            "fields": ["name", {"parts": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool query. The next message is the tool "
                            "result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="synthesize_query_bridge",
            description="Synthesize bridge answer from query rows",
            conversation=[{"user": "What parts are used in the Apollo project?"}],
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
                        "results_contain": ["Gear"],
                        "answer_contains": ["Gear"],
                    },
                },
                "results_contain": ["Gear"],
                "answer_contains": ["Gear"],
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value={"data": [{"name": "Apollo", "parts": [{"name": "Gear"}]}]},
        ):
            stream = io.StringIO()
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    stream=stream,
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "synthesize_answer_from_tool_results" not in result.recovery_events
        assert "assistant: Returned rows:" not in stream.getvalue()

    def test_run_case_does_not_repair_contradictory_answer_from_query_rows(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"name": "Apollo"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="No matching project records were returned."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="contradictory_query_answer",
            description="Contradictory answer must fail instead of being repaired",
            conversation=[{"user": "Find the Apollo project."}],
            expectations={
                "contract": {
                    "category": "data_grounding",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            }
                        ],
                        "results_contain": ["Apollo"],
                        "answer_contains": ["Apollo"],
                    },
                }
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value={"data": [{"name": "Apollo"}]},
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "repair_contradictory_answer" not in result.recovery_events

    def test_run_case_does_not_repair_raw_tool_query_syntax(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "PartManager",
                            "fields": ["name"],
                        },
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "PartManager",
                            "filters": {"material__name": "Steel"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "All parts are Bolt, Bearing, Gear.\n"
                            "Tool: query\n"
                            "- manager: PartManager\n"
                            '- filters: {"material__name": "Steel"}'
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="repair_raw_query_syntax",
            description="Answer should not include tool syntax after query rows",
            conversation=[
                {"user": "Show me all parts, then just show the ones that use Steel"},
            ],
            expectations={
                "contract": {
                    "category": "follow_up",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {"manager": "PartManager"},
                            },
                        ],
                        "results_contain": ["Bolt"],
                        "answer_contains": ["Bolt"],
                        "answer_excludes": ["Tool: query", "Bearing", "Gear"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "query" and args.get("filters") == {"material__name": "Steel"}:
                return {"data": [{"name": "Bolt"}]}
            if name == "query":
                return {
                    "data": [
                        {"name": "Bolt"},
                        {"name": "Bearing"},
                        {"name": "Gear"},
                    ]
                }
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "repair_raw_query_syntax_answer" not in result.recovery_events
        assert any(
            "raw query syntax" in violation
            for violation in result.contract_score.violations
        )

    def test_run_case_fails_raw_query_syntax_without_retry(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "MaterialManager",
                            "filters": {"density__gt": 7},
                            "fields": ["name", "density"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "```graphql\n"
                            "query { materialmanagerList { items { name } } }\n"
                            "```\n"
                            "Steel and Cobalt match."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Steel and Cobalt match."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="fail_raw_query_syntax",
            description="Raw query syntax should fail instead of being retried",
            conversation=[{"user": "List materials with density above 7"}],
            expectations={
                "contract": {
                    "category": "data_grounding",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {"manager": "MaterialManager"},
                            }
                        ],
                        "results_contain": ["Steel", "Cobalt"],
                        "answer_contains": ["Steel", "Cobalt"],
                        "answer_excludes": ["```graphql"],
                    },
                }
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value={
                "data": [
                    {"name": "Steel", "density": 7.8},
                    {"name": "Cobalt", "density": 8.9},
                ]
            },
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "raw_query_syntax_answer" not in result.recovery_events

    def test_run_case_fails_example_query_answer_without_retry(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"parts__material__name": "Aluminum"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Example query used: ProjectManager with filters. "
                            "Mercury uses Aluminum."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Mercury uses Aluminum."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="fail_example_query_answer",
            description="Query scaffolding in the final answer should fail",
            conversation=[{"user": "Which projects use aluminum parts?"}],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [{"name": "query"}],
                        "results_contain": ["Mercury"],
                        "answer_contains": ["Mercury", "Aluminum"],
                        "answer_excludes": ["Example Query"],
                    },
                }
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value={"data": [{"name": "Mercury"}]},
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "raw_query_syntax_answer" not in result.recovery_events

    def test_run_case_fails_answer_omitting_filter_value(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "PartManager",
                            "filters": {"material__name": "Steel"},
                            "fields": [
                                "name",
                                {"material": ["name"]},
                            ],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="- Bolt"),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Bolt is made from Steel."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="fail_answer_omits_filter_value",
            description="Answer missing queried filter value should fail",
            conversation=[{"user": "Which parts are made from Steel?"}],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [{"name": "query"}],
                        "results_contain": ["Bolt", "Steel"],
                        "answer_contains": ["Bolt", "Steel"],
                    },
                }
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value={
                "data": [
                    {
                        "name": "Bolt",
                        "material": {"name": "Steel"},
                    }
                ]
            },
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "answer_omits_filter_value" not in result.recovery_events

    def test_run_case_fails_deferral_after_successful_query(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"parts__material__name": "Aluminum"},
                            "fields": [
                                "name",
                                {
                                    "parts": [
                                        "name",
                                        {"material": ["name"]},
                                    ]
                                },
                            ],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Mercury uses Bearing made from Aluminum. Would you "
                            "like me to run another query?"
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Mercury uses Bearing made from Aluminum."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="fail_deferral_after_query",
            description="Final answer should not defer after successful query",
            conversation=[{"user": "Which projects use aluminum parts?"}],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [{"name": "query"}],
                        "results_contain": ["Mercury", "Bearing", "Aluminum"],
                        "answer_contains": ["Mercury", "Bearing", "Aluminum"],
                    },
                }
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value={
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
            },
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "answer_defers_after_query" not in result.recovery_events

    def test_run_case_fails_answer_omitting_requested_row_type(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="0",
                        name="search_managers",
                        args={"query": "all managers"},
                    ),
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"parts__material__name": "Aluminum"},
                            "fields": [
                                "name",
                                {
                                    "parts": [
                                        "name",
                                        {"material": ["name"]},
                                    ]
                                },
                            ],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Mercury uses Bearing made from Aluminum."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=("The project Mercury uses Bearing made from Aluminum.")
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="fail_answer_omits_requested_row_type",
            description="Answer missing requested row type should fail",
            conversation=[
                {
                    "user": (
                        "I need to find which projects use aluminum parts. "
                        "Help me explore the data model first."
                    )
                }
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [{"name": "query"}],
                        "results_contain": ["Mercury", "Bearing", "Aluminum"],
                        "answer_contains": [
                            "Project",
                            "Mercury",
                            "Bearing",
                            "Aluminum",
                        ],
                    },
                }
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=lambda name, _args, _context: (
                [{"manager": "ProjectManager"}]
                if name == "search_managers"
                else {
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
            ),
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "answer_omits_requested_row_type" not in result.recovery_events

    def test_run_case_fails_empty_claim_after_non_empty_query(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"name": "Apollo"},
                            "fields": ["name", {"parts": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "No matching records were found for parts in the "
                            "Apollo project."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo uses Gear."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="fail_empty_claim_after_query",
            description="Non-empty query rows answered as empty should fail",
            conversation=[{"user": "What parts are used in the Apollo project?"}],
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
                        "results_contain": ["Gear"],
                        "answer_contains": ["Gear"],
                    },
                }
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value={"data": [{"name": "Apollo", "parts": [{"name": "Gear"}]}]},
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "answer_contradicts_non_empty_query" not in result.recovery_events

    def test_run_case_does_not_target_retry_when_existing_query_has_relation_rows(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="get_manager_schema",
                        args={"manager": "PartManager"},
                    ),
                    ToolCallEvent(
                        id="3",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"name": "Apollo"},
                            "fields": ["name", {"parts": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "No matching records were found for parts in the "
                            "Apollo project."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo uses Gear."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="no_target_retry_when_relation_rows_exist",
            description="Existing relation rows should be enough answer evidence",
            conversation=[{"user": "What parts are used in the Apollo project?"}],
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
                        "results_contain": ["Gear"],
                        "answer_contains": ["Gear"],
                    },
                }
            },
        )

        query_managers: list[str] = []

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema" and args["manager"] == "ProjectManager":
                return {
                    "manager": "ProjectManager",
                    "fields": ["name"],
                    "filters": ["name", "parts__name"],
                    "relations": [{"name": "parts", "target": "PartManager"}],
                }
            if name == "get_manager_schema" and args["manager"] == "PartManager":
                return {
                    "manager": "PartManager",
                    "fields": ["name"],
                    "filters": ["name"],
                    "relations": [],
                }
            if name == "query":
                query_managers.append(str(args["manager"]))
                assert args["manager"] == "ProjectManager"
                return {"data": [{"name": "Apollo", "parts": [{"name": "Gear"}]}]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "answer_contradicts_non_empty_query" not in result.recovery_events
        assert query_managers == ["ProjectManager"]

    def test_run_case_retries_structured_filter_from_string_args(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "MaterialManager",
                            "fields": "[name, density]",
                            "filters": "[name: Steel]",
                        },
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "MaterialManager",
                            "fields": ["name", "density"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Steel has density 7.8."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="retry_structured_filter_from_string_args",
            description="Stringified filters should be retried as structured args",
            conversation=[{"user": "Show me the material named Steel"}],
            expectations={
                "contract": {
                    "category": "data_grounding",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {
                                    "manager": "MaterialManager",
                                    "filters": {"name": "Steel"},
                                },
                            }
                        ],
                        "results_contain": ["Steel"],
                        "results_exclude": ["Aluminum", "Cobalt"],
                        "answer_contains": ["Steel"],
                        "answer_excludes": ["Aluminum", "Cobalt"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if isinstance(args.get("filters"), str):
                return {"error": "'str' object has no attribute 'items'"}
            if args.get("filters") == {"name": "Steel"}:
                return {"data": [{"name": "Steel", "density": 7.8}]}
            return {
                "data": [
                    {"name": "Steel", "density": 7.8},
                    {"name": "Aluminum", "density": 2.7},
                    {"name": "Cobalt", "density": 8.9},
                ]
            }

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert result.recovery_events == ["inject_structured_filter_retry"]

    def test_run_case_retries_empty_string_filter_by_removing_it(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "MaterialManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "MaterialManager",
                            "filters": {"name": ""},
                            "fields": ["name", "density"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="No materials were found."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="Steel, Aluminum, and Cobalt are available."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="retry_empty_string_filter",
            description="Empty text filters should be removed before answering",
            conversation=[{"user": "List all materials"}],
            expectations={
                "contract": {
                    "category": "data_grounding",
                    "hard": {
                        "required_tool_calls": [{"name": "query"}],
                        "results_contain": ["Steel", "Aluminum", "Cobalt"],
                        "answer_contains": ["Steel", "Aluminum", "Cobalt"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {
                    "manager": "MaterialManager",
                    "fields": ["name", "density"],
                    "filters": ["name", "density__gt"],
                    "relations": [],
                }
            if args.get("filters") == {"name": ""}:
                return {"data": [], "total_count": 0, "has_more": False}
            return {
                "data": [
                    {"name": "Steel", "density": 7.8},
                    {"name": "Aluminum", "density": 2.7},
                    {"name": "Cobalt", "density": 8.9},
                ]
            }

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert result.recovery_events == ["inject_empty_text_filter_retry"]

    def test_run_case_retries_tool_bridge_answer_without_synthesis(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"name": "Apollo"},
                            "fields": ["name", {"parts": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool query. The next message is the tool "
                            "result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo uses Gear."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="retry_tool_bridge_answer",
            description="Bridge text should get a prompt retry",
            conversation=[{"user": "What parts are used in the Apollo project?"}],
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
                        "results_contain": ["Gear"],
                        "answer_contains": ["Gear"],
                    },
                }
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value={"data": [{"name": "Apollo", "parts": [{"name": "Gear"}]}]},
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert result.recovery_events == ["tool_bridge_answer"]
        recovery_messages = provider.calls[2]["messages"]
        assert any(
            message.role == "system"
            and "Do not include code fences" in message.content
            and "Do not propose another query" in message.content
            for message in recovery_messages
        )

    def test_run_case_does_not_synthesize_bridge_answer_from_find_path(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="find_path",
                        args={
                            "from_manager": "MaterialManager",
                            "to_manager": "ProjectManager",
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Called tool find_path. The next message is the "
                            "tool result; answer from it exactly."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="synthesize_path_bridge",
            description="Synthesize bridge answer from relationship path",
            conversation=[{"user": "How are materials related to projects?"}],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [{"name": "find_path"}],
                        "answer_contains": ["material", "part"],
                    },
                }
            },
        )

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            return_value=["material", "parts"],
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "find_path", "description": "Find path"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "synthesize_answer_from_tool_results" not in result.recovery_events

    def test_run_case_blocks_query_for_relationship_only_question(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="find_path",
                        args={
                            "from_manager": "MaterialManager",
                            "to_manager": "ProjectManager",
                        },
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "MaterialManager",
                            "fields": ["name", "meltingPoint"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="Materials relate to projects through material and parts."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="relationship_query_block",
            description="Relationship-only questions should answer from find_path",
            conversation=[{"user": "How are materials related to projects?"}],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [{"name": "find_path"}],
                        "forbidden_tools": ["query"],
                        "answer_contains": ["material", "part"],
                    },
                }
            },
        )

        def _fake_tool(name: str, _args: dict[str, Any], _context: Any) -> Any:
            if name == "find_path":
                return ["material", "parts"]
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "find_path", "description": "Find path"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert result.recovery_events == ["block_relationship_data_query"]

    def test_run_case_repairs_relation_filter_query_without_schema(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "PartManager",
                            "filters": {"material__name": "Steel"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Returned rows: Bolt."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Returned rows: Bolt."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Bolt uses Steel."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="repair_relation_filter",
            description="Repair relation filter query without schema",
            conversation=[{"user": "Which parts use a material named Steel?"}],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "get_manager_schema",
                                "args_contain": {"manager": "PartManager"},
                            },
                            {
                                "name": "query",
                                "args_contain": {"manager": "PartManager"},
                            },
                        ],
                        "results_contain": ["Bolt"],
                        "answer_contains": ["Bolt", "Steel"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {
                    "manager": "PartManager",
                    "fields": ["name"],
                    "relations": [{"name": "material", "target": "MaterialManager"}],
                    "filters": ["material__name", "material__name__icontains"],
                }
            if name == "query" and any(
                isinstance(field, dict) and "material" in field
                for field in args["fields"]
            ):
                return {"data": [{"name": "Bolt", "material": {"name": "Steel"}}]}
            if name == "query":
                return {"data": [{"name": "Bolt"}]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

    def test_run_case_expands_nested_relation_filter_fields(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"parts__material__name__icontains": "aluminum"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Mercury uses Bearing made from Aluminum."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="expand_nested_relation_filter_fields",
            description="Relation filter retries should include nested values",
            conversation=[{"user": "Which projects use aluminum parts?"}],
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
                        "results_contain": ["Mercury", "Bearing", "Aluminum"],
                        "answer_contains": ["Mercury", "Bearing", "Aluminum"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {
                    "manager": "ProjectManager",
                    "fields": ["name"],
                    "relations": [{"name": "parts", "target": "PartManager"}],
                    "filters": [
                        "name",
                        "parts__name",
                        "parts__material__name",
                        "parts__material__name__icontains",
                    ],
                }
            if name == "query" and args["fields"] == [
                "name",
                {"parts": ["name", {"material": ["name"]}]},
            ]:
                return {
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
            if name == "query":
                return {
                    "data": [
                        {
                            "name": "Mercury",
                            "parts": [{"name": "Bearing"}],
                        }
                    ]
                }
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert result.recovery_events == ["inject_relation_query_fields"]

    def test_run_case_retries_zero_limit_query_with_positive_limit(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"parts__material__name": "Cobalt"},
                            "fields": ["name"],
                            "limit": 0,
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="No matching project records were returned."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo is affected."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="retry_zero_limit",
            description="Retry zero-limit query",
            conversation=[{"user": "What projects contain parts with cobalt?"}],
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
                        "results_contain": ["Apollo"],
                        "answer_contains": ["Apollo"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {"manager": args["manager"], "fields": ["name"]}
            if name == "query" and args.get("limit") == 0:
                return {"data": [], "has_more": True, "total_count": 1}
            if name == "query":
                return {"data": [{"name": "Apollo"}], "has_more": False}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

    def test_run_case_does_not_inject_project_query_from_part_filter(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "PartManager",
                            "filters": {"material__name": "Cobalt"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="I found Gear but no affected projects."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Apollo is affected."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="project_from_part_filter",
            description="Recover project query after part material lookup",
            conversation=[
                {
                    "user": "Which projects would be affected if cobalt parts were updated?"
                }
            ],
            expectations={
                "contract": {
                    "category": "read_only_safety",
                    "hard": {
                        "required_tool_calls": [
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            }
                        ],
                        "results_contain": ["Apollo"],
                        "answer_contains": ["Apollo"],
                        "forbidden_tools": ["mutate"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "get_manager_schema":
                return {"manager": args["manager"], "fields": ["name"]}
            if name == "query" and args["manager"] == "PartManager":
                return {"data": [{"name": "Gear"}]}
            if name == "query" and args["manager"] == "ProjectManager":
                return {"data": [{"name": "Apollo"}]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "inject_target_project_query" not in result.recovery_events

    def test_run_case_does_not_synthesize_rows_after_max_iterations(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"parts__material__name": "Cobalt"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="max_iteration_rows",
            description="Synthesize a final answer from rows when model never stops",
            conversation=[{"user": "What projects contain parts with cobalt?"}],
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
                        "results_contain": ["Apollo"],
                        "answer_contains": ["Apollo"],
                    },
                }
            },
        )

        with (
            patch(
                "general_manager.chat.evals.runner.execute_chat_tool",
                return_value={"data": [{"name": "Apollo"}]},
            ),
            patch("general_manager.chat.evals.runner.MAX_TOOL_ITERATIONS", 1),
        ):
            stream = io.StringIO()
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [{"name": "query", "description": "Query"}],
                    stream=stream,
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "synthesize_answer_after_max_iterations" not in result.recovery_events
        assert "assistant: Returned rows:" not in stream.getvalue()

    def test_run_case_blocks_data_query_for_broad_manager_discovery(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="search_managers",
                        args={"query": "all managers"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="query",
                        args={"manager": "MaterialManager", "fields": ["name"]},
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Material and Part managers are available."),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="block_discovery_query",
            description="Broad discovery must not query data records",
            conversation=[{"user": "What data do you have access to?"}],
            expectations={
                "contract": {
                    "category": "manager_discovery",
                    "hard": {
                        "required_tool_calls": [{"name": "search_managers"}],
                        "forbidden_tools": ["query"],
                        "answer_contains": ["Material", "Part"],
                    },
                }
            },
        )

        def _fake_tool(name: str, _args: dict[str, Any], _context: Any) -> Any:
            if name == "search_managers":
                return [{"manager": "MaterialManager"}, {"manager": "PartManager"}]
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

    def test_run_case_does_not_repair_incomplete_discovery_traversal_answer(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="search_managers",
                        args={"query": "projects materials"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="find_path",
                        args={
                            "from_manager": "ProjectManager",
                            "to_manager": "MaterialManager",
                        },
                    ),
                    ToolCallEvent(
                        id="3",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {"parts__material__name": "Aluminum"},
                            "fields": ["name"],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "Mercury uses aluminum parts. No additional "
                            "projects were found."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="repair_discovery_answer_terms",
            description="Discovery traversal answer must include discovered path terms",
            conversation=[
                {"user": "Help me explore which projects use aluminum parts."}
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [
                            {"name": "search_managers"},
                            {"name": "find_path"},
                        ],
                        "answer_contains": ["Project", "Material"],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "search_managers":
                return [{"manager": "ProjectManager"}, {"manager": "MaterialManager"}]
            if name == "get_manager_schema":
                return {"manager": args["manager"], "fields": ["name"]}
            if name == "find_path":
                return ["parts", "material"]
            if name == "query":
                return {"data": [{"name": "Mercury"}]}
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "find_path", "description": "Find path"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "repair_incomplete_discovery_answer" not in result.recovery_events

    def test_run_case_does_not_inject_project_material_query_after_discovery(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="search_managers",
                        args={"query": "all managers"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="find_path",
                        args={
                            "from_manager": "ProjectManager",
                            "to_manager": "MaterialManager",
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "ProjectManager connects to MaterialManager through parts."
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="inject_project_material_query_after_discovery",
            description="Discovery traversal must query obvious requested records",
            conversation=[
                {
                    "user": (
                        "I need to find which projects use aluminum parts. "
                        "Help me explore the data model first."
                    )
                }
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [
                            {"name": "search_managers"},
                            {"name": "find_path"},
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            },
                        ],
                        "results_contain": ["Mercury", "Bearing", "Aluminum"],
                        "answer_contains": [
                            "Project",
                            "Material",
                            "Mercury",
                            "Bearing",
                            "Aluminum",
                        ],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "search_managers":
                return [{"manager": "ProjectManager"}, {"manager": "MaterialManager"}]
            if name == "get_manager_schema":
                return {
                    "manager": args["manager"],
                    "fields": ["name"],
                    "relations": [{"name": "parts", "target": "PartManager"}],
                    "filters": [
                        "parts__material__name",
                        "parts__material__name__icontains",
                    ],
                }
            if name == "find_path":
                return ["parts", "material"]
            if name == "query":
                return {
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
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "find_path", "description": "Find path"},
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "inject_project_material_query" not in result.recovery_events
        assert "repair_incomplete_discovery_answer" not in result.recovery_events

    def test_run_case_retries_project_material_query_when_material_omitted(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="search_managers",
                        args={"query": "all managers"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="find_path",
                        args={
                            "from_manager": "ProjectManager",
                            "to_manager": "MaterialManager",
                        },
                    ),
                    ToolCallEvent(
                        id="3",
                        name="get_manager_schema",
                        args={"manager": "ProjectManager"},
                    ),
                    ToolCallEvent(
                        id="4",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {
                                "parts__material__name__icontains": "aluminum",
                            },
                            "fields": ["name", {"parts": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(content="Returned rows: Mercury, Bearing."),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content="Mercury uses the Bearing part made from Aluminum."
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="retry_project_material_query",
            description="Retry project material query when material is omitted",
            conversation=[
                {
                    "user": (
                        "I need to find which projects use aluminum parts. "
                        "Help me explore the data model first."
                    )
                }
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [
                            {"name": "search_managers"},
                            {"name": "find_path"},
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            },
                        ],
                        "results_contain": ["Mercury", "Bearing", "Aluminum"],
                        "answer_contains": [
                            "Mercury",
                            "Bearing",
                            "Aluminum",
                        ],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "search_managers":
                return [{"manager": "ProjectManager"}, {"manager": "MaterialManager"}]
            if name == "find_path":
                return ["parts", "material"]
            if name == "get_manager_schema":
                return {
                    "manager": args["manager"],
                    "fields": ["name"],
                    "relations": [{"name": "parts", "target": "PartManager"}],
                    "filters": [
                        "parts__material__name",
                        "parts__material__name__icontains",
                    ],
                }
            if name == "query" and any(
                isinstance(field, dict)
                and "parts" in field
                and any(
                    isinstance(part_field, dict) and "material" in part_field
                    for part_field in field["parts"]
                )
                for field in args["fields"]
            ):
                return {
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
            if name == "query":
                return {
                    "data": [
                        {
                            "name": "Mercury",
                            "parts": [{"name": "Bearing"}],
                        }
                    ]
                }
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "find_path", "description": "Find path"},
                        {"name": "get_manager_schema", "description": "Schema"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True
        assert "inject_relation_query_fields" in result.recovery_events
        assert "inject_project_material_query" not in result.recovery_events

    def test_run_case_does_not_repair_deferred_discovery_result_answer(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="1",
                        name="search_managers",
                        args={"query": "all managers"},
                    ),
                    ToolCallEvent(
                        id="2",
                        name="find_path",
                        args={
                            "from_manager": "ProjectManager",
                            "to_manager": "MaterialManager",
                        },
                    ),
                    ToolCallEvent(
                        id="3",
                        name="query",
                        args={
                            "manager": "ProjectManager",
                            "filters": {
                                "parts__material__name__icontains": "aluminum",
                            },
                            "fields": ["name", {"parts": ["name"]}],
                        },
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
                [
                    TextChunkEvent(
                        content=(
                            "ProjectManager connects to MaterialManager through "
                            "parts. Would you like me to run this query to show "
                            "you which projects use aluminum parts?"
                        )
                    ),
                    DoneEvent(usage=TokenUsage()),
                ],
            ]
        )
        case = EvalCase(
            name="repair_deferred_discovery_result",
            description="Discovery traversal must answer from already-run query",
            conversation=[
                {
                    "user": (
                        "I need to find which projects use aluminum parts. "
                        "Help me explore the data model first."
                    )
                }
            ],
            expectations={
                "contract": {
                    "category": "relation_traversal",
                    "hard": {
                        "required_tool_calls": [
                            {"name": "search_managers"},
                            {"name": "find_path"},
                            {
                                "name": "query",
                                "args_contain": {"manager": "ProjectManager"},
                            },
                        ],
                        "results_contain": ["Mercury", "Bearing", "Aluminum"],
                        "answer_contains": [
                            "Project",
                            "Material",
                            "Mercury",
                            "Bearing",
                            "Aluminum",
                        ],
                        "answer_excludes": [
                            "Would you like me to run this query",
                        ],
                    },
                }
            },
        )

        def _fake_tool(name: str, args: dict[str, Any], _context: Any) -> Any:
            if name == "search_managers":
                return [{"manager": "ProjectManager"}, {"manager": "MaterialManager"}]
            if name == "get_manager_schema":
                return {
                    "manager": args["manager"],
                    "fields": ["name"],
                    "relations": [{"name": "parts", "target": "PartManager"}],
                    "filters": [
                        "parts__material__name",
                        "parts__material__name__icontains",
                    ],
                }
            if name == "find_path":
                return ["parts", "material"]
            if name == "query":
                return {
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
            raise AssertionError

        with patch(
            "general_manager.chat.evals.runner.execute_chat_tool",
            side_effect=_fake_tool,
        ):
            result = asyncio.run(
                run_case(
                    provider,
                    case,
                    [
                        {"name": "search_managers", "description": "Search"},
                        {"name": "find_path", "description": "Find path"},
                        {"name": "query", "description": "Query"},
                    ],
                    recover_missing_tools=True,
                )
            )

        assert result.passed is False
        assert result.contract_score is not None
        assert result.contract_score.passed is False
        assert "repair_incomplete_discovery_answer" not in result.recovery_events


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
