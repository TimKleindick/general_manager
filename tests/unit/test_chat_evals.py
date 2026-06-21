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
        recovery_messages = provider.calls[2]["messages"]
        assert any(
            message.role == "system" and "previous query failed" in message.content
            for message in recovery_messages
        )

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
        recovery_messages = provider.calls[3]["messages"]
        assert any(
            message.role == "system"
            and "Do not repeat unavailable manager names" in message.content
            for message in recovery_messages
        )

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

    def test_run_case_injects_project_query_from_part_material_result(self) -> None:
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

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

    def test_run_case_injects_cross_manager_path_after_discovery(
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

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

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
                            "Called tool search_managers. The next message is "
                            "the tool result; answer from it exactly."
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

    def test_run_case_synthesizes_bridge_answer_from_discovery_tools(self) -> None:
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

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

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

    def test_run_case_injects_cross_manager_path_for_traversal_answer(self) -> None:
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

        assert result.passed is True
        assert result.contract_score is not None
        assert result.contract_score.passed is True

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

    def test_run_case_synthesizes_bridge_answer_from_query_rows(self) -> None:
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
