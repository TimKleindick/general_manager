"""Known unsupported query inputs remain local; unknown resolver errors stop."""

import pytest

from tests.experiments.test_gm_eval_harness import _child


def test_real_graphql_query_and_read_replay_preserve_known_input_error():
    result = _child(r"""
import json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import _diagnose_query_failure
runtime = bootstrap(manager_count=5)
arguments = {
    "manager": "Project", "fields": ["code"], "filters": {},
    "arguments": {"exclude": {"materialsList": {"none": {"id_In": [2]}}}},
}
try:
    try:
        runtime.tool("query", arguments)
    except Exception as error:
        observed = {"error_type": type(error).__name__, "message": str(error)}
    else:
        observed = {"error_type": None}
    observed["diagnostic"] = _diagnose_query_failure(runtime, {"name": "query", "args": arguments})
    observed["allowed_filter_rows"] = runtime.tool("query", {
        "manager": "Project", "fields": ["code"],
        "filters": {"materialsList": {"none": {"id_In": [2]}}},
    })["data"]
    print(json.dumps(observed))
finally:
    runtime.close()
""")
    assert result["error_type"] == "UnsupportedExcludeNoneRelationFilterError"
    assert result["diagnostic"]["status"] == "model_task_failure"
    assert result["diagnostic"]["error_type"] == result["error_type"]
    assert result["diagnostic"]["independent_read_only_replay"] is True
    assert isinstance(result["allowed_filter_rows"], list)


@pytest.mark.parametrize(
    ("damage", "expected_type", "expected_status"),
    [
        ("known", "UnsupportedExcludeNoneRelationFilterError", "model_task_failure"),
        (
            "two_known",
            "UnsupportedExcludeNoneRelationFilterError",
            "model_task_failure",
        ),
        ("same_text_value_error", "ValueError", "harness_failure"),
        ("unknown", "ValueError", "harness_failure"),
        ("mixed", "ValueError", "harness_failure"),
    ],
)
def test_only_wholly_known_graphql_error_types_remain_local(
    damage, expected_type, expected_status
):
    result = _child(
        "damage = "
        + repr(damage)
        + "\n"
        + r"""
import json
from unittest.mock import patch
from graphql import ExecutionResult, GraphQLError
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import _diagnose_query_failure
runtime = bootstrap(manager_count=5)
from general_manager.api.graphql_resolvers import UnsupportedExcludeNoneRelationFilterError
known = UnsupportedExcludeNoneRelationFilterError()
errors = {
    "known": [GraphQLError(str(known), original_error=known)],
    "two_known": [GraphQLError(str(known), original_error=known), GraphQLError(str(known), original_error=UnsupportedExcludeNoneRelationFilterError())],
    "same_text_value_error": [GraphQLError(str(known), original_error=ValueError(str(known)))],
    "unknown": [GraphQLError("unexplained resolver failure", original_error=RuntimeError("unexplained resolver failure"))],
    "mixed": [GraphQLError(str(known), original_error=known), GraphQLError("unexplained resolver failure", original_error=RuntimeError("unexplained resolver failure"))],
}[damage]
arguments = {"manager": "Project", "fields": ["code"], "filters": {}}
try:
    with patch.object(runtime.schema, "execute", return_value=ExecutionResult(data=None, errors=errors)):
        try:
            runtime.tool("query", arguments)
        except Exception as error:
            observed = {"error_type": type(error).__name__, "same_exception": error is known}
        else:
            observed = {"error_type": None}
        observed["diagnostic"] = _diagnose_query_failure(runtime, {"name": "query", "args": arguments})
    print(json.dumps(observed))
finally:
    runtime.close()
"""
    )
    assert result["error_type"] == expected_type
    assert result["diagnostic"]["status"] == expected_status
    if damage in {"known", "two_known"}:
        assert result["same_exception"] is True


def test_real_scheduler_exposes_actionable_local_input_error():
    result = _child(r"""
import asyncio, json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import make_script
runtime = bootstrap(manager_count=5)
from general_manager.chat.providers.base import ToolCallEvent
arguments = {
    "manager": "Project", "fields": ["code"], "filters": {},
    "arguments": {"exclude": {"materialsList": {"none": {"id_In": [2]}}}},
}
case = {"id": "unsupported-query-input", "turns": ["Read Customer"]}
delegate = make_script(case, runtime)
def factory(config):
    provider = delegate(config)
    class Wrapper:
        reported_usage = None
        async def complete(self, messages, tools):
            async for event in provider.complete(messages, tools):
                yield ToolCallEvent(event.id, event.name, arguments) if isinstance(event, ToolCallEvent) else event
    return Wrapper()
try:
    report = asyncio.run(run_case(runtime, case, make_profile("weak-only"), factory))
    print(json.dumps({
        "status": report["status"], "failure_flags": report["failure_flags"],
        "tool_failure_diagnostics": report["tool_failure_diagnostics"],
        "results": [item["result"] for turn in report["turns"] for item in turn["tool_results"]],
    }))
finally:
    runtime.close()
""")
    assert "harness_failure" not in result["failure_flags"]
    assert result["status"] == "model_task_failure"
    assert result["tool_failure_diagnostics"] == []
    assert any(
        item.get("code") == "invalid_graphql_request"
        and "none" in item.get("message", "")
        and "exclude" in item.get("message", "")
        for item in result["results"]
    )
    assert not any(item.get("code") == "tool_failed" for item in result["results"])


def test_public_read_guidance_discloses_unsupported_exclude_quantifier():
    from general_manager.chat.system_prompt import build_system_prompt
    from general_manager.chat.tool_metadata import READ_TOOL_GUIDANCE, TOOL_DESCRIPTIONS

    for text in (
        READ_TOOL_GUIDANCE,
        TOOL_DESCRIPTIONS["query"],
        build_system_prompt(),
    ):
        assert "none" in text and "exclude" in text and "unsupported" in text
