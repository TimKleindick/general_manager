"""The installed evaluator observes the production scheduler, never a tool loop."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from general_manager.chat.evals.fixtures import setup_toy_schema
from general_manager.chat.evals.runner import EvalCase, run_case
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from general_manager.chat.tools import get_tool_definitions


def project_case() -> EvalCase:
    return EvalCase(
        "native_projects",
        "The real fixture has a third manager absent from the old fake resolver.",
        [{"user": "List the names of every project in ProjectManager."}],
        {
            "tool_calls": [
                {"name": "query", "args_contain": {"manager": "ProjectManager"}}
            ],
            "results_contain": ["Apollo", "Mercury"],
            "answer_contains": ["Apollo", "Mercury"],
        },
    )


class NativeProjectProvider:
    """A neutral provider instance; no configured-construction API is required."""

    def __init__(self) -> None:
        self.calls = []
        self.closed = 0

    async def complete(self, messages, tools):
        self.calls.append((list(messages), list(tools)))
        position = (len(self.calls) - 1) % 5
        plan = {
            "intent": "read",
            "tasks": [
                {
                    "task_id": "task_1",
                    "objective": "List ProjectManager project names",
                    "depends_on": [],
                    "requirements": [
                        {
                            "requirement_id": "projects",
                            "kind": "query",
                            "description": "All project names",
                            "operation": None,
                        }
                    ],
                    "completion_criteria": ["projects"],
                    "routing_features": [],
                }
            ],
        }
        replies = [
            TextChunkEvent(json.dumps(plan)),
            ToolCallEvent(
                "schema",
                "get_manager_schema",
                {"manager": "ProjectManager", "view": "full"},
            ),
            ToolCallEvent(
                "query", "query", {"manager": "ProjectManager", "fields": ["name"]}
            ),
            TextChunkEvent(
                json.dumps({"action": "complete", "evidence_ids": ["task_1:query:1"]})
            ),
            TextChunkEvent(
                json.dumps(
                    {
                        "answer": "Apollo and Mercury",
                        "evidence_ids": ["task_1:query:1"],
                    }
                )
            ),
        ]
        try:
            assert position < len(replies), "no hidden recovery or extra provider call"
            yield replies[position]
            yield DoneEvent(TokenUsage(2, 3))
        finally:
            self.closed += 1


def test_default_evaluator_uses_real_manager_schema_tools_and_planned_completion():
    setup_toy_schema()
    provider = NativeProjectProvider()
    result = asyncio.run(run_case(provider, project_case(), get_tool_definitions()))

    assert result.passed, (result.error, result.answer, result.tool_calls)
    assert result.answer == "Apollo and Mercury"
    assert result.tool_results[-1]["data"] == [
        {"name": "Apollo"},
        {"name": "Mercury"},
    ]
    assert result.diagnostics["orchestration"]["coverage"] == {
        "resolved": 1,
        "total": 1,
    }
    assert result.requests == len(provider.calls) == provider.closed == 5
    assert result.usage == {"input_tokens": 10, "output_tokens": 15}


def test_omitted_tool_definitions_select_the_native_scheduler():
    setup_toy_schema()
    provider = NativeProjectProvider()
    result = asyncio.run(run_case(provider, project_case()))
    assert result.passed, result.error
    assert len(provider.calls) == provider.closed == 5


@pytest.mark.parametrize("change", ["schema", "subset", "duplicate", "unknown"])
def test_custom_tool_definitions_fail_before_dispatch(change):
    setup_toy_schema()
    definitions = deepcopy(get_tool_definitions())
    if change == "schema":
        definition = next(tool for tool in definitions if tool["name"] == "query")
        definition["input_schema"]["properties"]["manager"]["enum"] = [
            "MaterialManager"
        ]
    elif change == "subset":
        definitions = [tool for tool in definitions if tool["name"] == "query"]
    elif change == "duplicate":
        definitions.append(deepcopy(definitions[0]))
    else:
        definitions.append({"name": "custom"})
    provider = NativeProjectProvider()
    with pytest.raises(ValueError, match="Custom eval tools were removed"):
        asyncio.run(run_case(provider, project_case(), definitions))
    assert provider.calls == []


def test_editing_getter_output_cannot_rewrite_the_native_tool_contract():
    from general_manager.chat.tool_metadata import TOOL_INPUT_SCHEMAS

    setup_toy_schema()
    original = deepcopy(TOOL_INPUT_SCHEMAS)
    with patch.dict(TOOL_INPUT_SCHEMAS, deepcopy(original), clear=True):
        definitions = get_tool_definitions()
        query = next(tool for tool in definitions if tool["name"] == "query")
        query["input_schema"]["properties"]["manager"]["enum"] = ["MaterialManager"]
        provider = NativeProjectProvider()
        with pytest.raises(ValueError, match="Custom eval tools were removed"):
            asyncio.run(run_case(provider, project_case(), definitions))
        assert provider.calls == []
        assert TOOL_INPUT_SCHEMAS == original


def test_each_turn_discovers_current_manager_visibility(monkeypatch):
    from general_manager.api.graphql import GraphQL
    from general_manager.chat.planned.config import get_planned_chat_settings
    from general_manager.chat.schema_index import (
        clear_schema_index_cache,
        planner_catalog_summary,
    )

    setup_toy_schema()

    class VisibilityChange:
        changed = False

        def write(self, text):
            if not self.changed and text == "Apollo and Mercury":
                self.changed = True
                monkeypatch.setattr(
                    GraphQL.manager_registry["ProjectManager"], "chat_exposed", False
                )
                clear_schema_index_cache()

        def flush(self):
            pass

    case = project_case()
    case.conversation.append({"user": "List the currently exposed projects again."})
    provider = NativeProjectProvider()
    output = VisibilityChange()
    result = asyncio.run(run_case(provider, case, [], stream=output))
    assert output.changed
    reference = next(
        json.loads(message.content.removeprefix("REFERENCE_DATA="))
        for message in provider.calls[5][0]
        if message.role == "user" and message.content.startswith("REFERENCE_DATA=")
    )
    discovery = reference["catalog_and_schema_summary"]
    assert "ProjectManager" not in discovery["schema"]
    assert discovery == planner_catalog_summary(get_planned_chat_settings())
    # The fixed script still requests the hidden manager; native resolution denies it.
    assert not result.passed and result.error == "manager_unresolved"
    assert (
        sum(
            isinstance(item, dict)
            and isinstance(item.get("data"), list)
            and {"name": "Apollo"} in item["data"]
            for item in result.tool_results
        )
        == 1
    )
    assert len(provider.calls) == provider.closed


@pytest.mark.parametrize(
    "option", [{"strategy": "legacy"}, {"recover_missing_tools": True}]
)
def test_removed_legacy_options_fail_before_any_provider_request(option):
    provider = NativeProjectProvider()
    with pytest.raises(ValueError, match="removed"):
        asyncio.run(run_case(provider, project_case(), [], **option))
    assert provider.calls == []


def test_suite_rejects_removed_recovery_even_when_selection_is_empty():
    from general_manager.chat.evals.runner import run_eval_suite

    provider = NativeProjectProvider()
    with pytest.raises(ValueError, match="removed"):
        asyncio.run(
            run_eval_suite(
                provider, ["basic_queries"], tier=999, recover_missing_tools=True
            )
        )
    assert provider.calls == []


@pytest.mark.parametrize(
    "option",
    [
        {"max_provider_requests": 0},
        {"max_provider_requests": 65},
        {"max_provider_requests": True},
        {"max_tool_iterations": -1},
        {"max_provider_requests": 5, "max_tool_iterations": 5},
    ],
)
def test_invalid_or_ambiguous_request_cap_fails_before_dispatch(option):
    provider = NativeProjectProvider()
    with pytest.raises(ValueError, match="max_provider_requests"):
        asyncio.run(run_case(provider, project_case(), [], **option))
    assert provider.calls == []


def test_explicit_compatibility_request_cap_includes_the_planner():
    setup_toy_schema()
    provider = NativeProjectProvider()
    result = asyncio.run(
        run_case(
            provider, project_case(), get_tool_definitions(), max_tool_iterations=1
        )
    )
    assert not result.passed and result.status == "unscored"
    assert result.error == "turn_request_budget_exhausted"
    assert len(provider.calls) == provider.closed == result.requests == 1
    assert result.tool_calls == []
    assert result.usage == {"input_tokens": 2, "output_tokens": 3}


def test_unobserved_usage_is_not_reported_as_an_observed_zero():
    class Incomplete:
        async def complete(self, messages, tools):
            yield TextChunkEvent("incomplete")

    result = asyncio.run(run_case(Incomplete(), project_case(), []))
    assert result.status == "unscored" and not result.passed
    assert result.usage == {}


class OtherNativeProjectProvider(NativeProjectProvider):
    pass


def test_module_cli_retains_custom_no_argument_provider_comparison(monkeypatch, capsys):
    from general_manager.chat.evals import runner
    from general_manager.chat.evals.__main__ import main

    monkeypatch.setattr(runner, "load_dataset", lambda _name: [project_case()])
    paths = [
        "tests.unit.test_chat_eval_execution.NativeProjectProvider",
        "tests.unit.test_chat_eval_execution.OtherNativeProjectProvider",
    ]
    main(
        [
            "--settings",
            "tests.test_settings",
            "--fixture",
            "toy",
            "--dataset",
            "basic_queries",
            "--compare",
            ",".join(paths),
        ]
    )
    output = capsys.readouterr().out
    assert all(path in output for path in paths)
    assert "100%" in output


def test_request_cap_resets_per_turn_and_real_tool_history_reaches_the_next_planner():
    setup_toy_schema()
    provider = NativeProjectProvider()
    case = project_case()
    case.conversation.append({"user": "List those projects again."})
    case.expectations["turns"] = [
        {"answer_contains": ["Apollo", "Mercury"]},
        {"answer_contains": ["Apollo", "Mercury"]},
    ]
    result = asyncio.run(
        run_case(provider, case, get_tool_definitions(), max_provider_requests=5)
    )
    assert result.passed, (result.error, result.answer)
    assert result.requests == len(provider.calls) == provider.closed == 10
    reference = next(
        json.loads(message.content.removeprefix("REFERENCE_DATA="))
        for message in provider.calls[5][0]
        if message.role == "user" and message.content.startswith("REFERENCE_DATA=")
    )
    context = reference["conversation_context"]
    assert sum(row["role"] == "user" for row in context) == 2
    assert any(row["role"] == "tool" for row in context)
    assert any(
        "Apollo" in row["content"] for row in context if row["role"] == "assistant"
    )


def test_mutation_plan_never_reenters_a_provider_tool_loop():
    class MutationProvider:
        calls = 0

        async def complete(self, messages, tools):
            self.calls += 1
            yield TextChunkEvent('{"intent":"mutation","tasks":[]}')
            yield DoneEvent(TokenUsage())

    provider = MutationProvider()
    case = EvalCase("write", "", [{"user": "Update the Apollo record."}], {})
    result = asyncio.run(run_case(provider, case, []))
    assert not result.passed and result.status == "unscored"
    assert result.error == "read_only_eval_mutation_plan"
    assert provider.calls == 1
    assert result.tool_calls == []


@pytest.mark.parametrize("phase", ["tool", "answer"])
@pytest.mark.parametrize("fail_on_flush", [False, True])
def test_output_failure_closes_native_execution_without_hidden_continuation(
    phase, fail_on_flush
):
    message = "output failed"

    class BrokenOutput:
        failed = False

        def write(self, value):
            if (phase == "tool" and "tool_call " in value) or (
                phase == "answer" and "Apollo" in value
            ):
                self.failed = True
            if self.failed and not fail_on_flush:
                raise OSError(message)

        def flush(self):
            if self.failed and fail_on_flush:
                raise OSError(message)

    async def check():
        setup_toy_schema()
        provider = NativeProjectProvider()
        result = await run_case(provider, project_case(), [], stream=BrokenOutput())
        assert not result.passed and result.status == "unscored"
        assert result.error == "output failed"
        assert len(provider.calls) == provider.closed
        assert not [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]

    asyncio.run(check())
