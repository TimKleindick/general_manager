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


# ---------------------------------------------------------------------------
# Runner integration tests (with scripted provider)
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    """Provider that returns a pre-defined sequence of events per call."""

    def __init__(self, scripts: list[list]) -> None:
        self._scripts = list(scripts)
        self._call_index = 0

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator:
        idx = min(self._call_index, len(self._scripts) - 1)
        self._call_index += 1
        for event in self._scripts[idx]:
            yield event


class RunnerIntegrationTests(SimpleTestCase):
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
    ) -> EvalResult:
        case = EvalCase(name=name, description="", conversation=[], expectations={})
        return EvalResult(
            case=case,
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
        assert "Overall" in report
        assert "50%" in report

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
