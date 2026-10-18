"""Lookup feedback preserves the declared mode and points to observed next work."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from general_manager.chat.graphql_contract import clear_contract_cache
from general_manager.api.graphql import GraphQL
from general_manager.chat.planned.models import EvidenceRequirement, SchemaBinding
from general_manager.chat.planned.scheduler import _executor_messages
from general_manager.chat.providers.base import ToolCallEvent
from general_manager.chat.tools import execute_chat_tool
from tests.unit.test_chat_category_value_binding_r26 import (
    _binding,
    _call,
    _lookup,
    _public_runner,
)
from tests.unit import test_chat_category_value_binding_r26 as category_tools
from tests.unit.test_chat_planned_tool_feedback import _record_rounds, _reference


category_schema = category_tools.category_schema


def _messages(runner):
    runtime = runner.runtimes["task_1"]
    return _executor_messages(
        runner.prepared.user_text,
        runtime.task,
        runner.evidence,
        tool_history=runtime.tool_history,
        last_tool_round=runtime.last_tool_round,
    )


def test_lookup_and_binding_modes_survive_history_without_entering_backend_cache(
    category_schema,
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _call(runner, "get_manager_schema", manager=schema.manager, view="full")
    dispatched = []

    def tool(name, args, context):
        dispatched.append((name, deepcopy(args)))
        return execute_chat_tool(name, args, context)

    runner.callbacks = replace(runner.callbacks, execute_tool=tool)
    lookup = {
        "manager": schema.manager,
        "fields": ["id", "name", "family"],
        "category_lookup": {"schema_evidence_id": "task_1:schema:1"},
        "requirement_id": "query",
    }
    _call(runner, "query", **lookup)
    _call(runner, "query", **lookup)
    business = {
        "manager": schema.manager,
        "fields": ["id", "name", "family"],
        "filters": {"family_Exact": schema.actual},
        "category_bindings": [_binding()],
        "requirement_id": "query",
    }
    result, progress = _call(runner, "query", **business)

    assert progress and [row["id"] for row in result["data"]] == [1, 2]
    calls = [call for message in _messages(runner) for call in message.tool_calls]
    assert [call.args for call in calls[1:]] == [lookup, lookup, business]
    results = [
        message.tool_result for message in _messages(runner) if message.role == "tool"
    ]
    assert results[1:] == [
        {"evidence_ref": "task_1:query:2"},
        {"evidence_ref": "task_1:query:2"},
        {"evidence_ref": "task_1:query:3"},
    ]
    assert len(dispatched) == 2
    assert all(
        not {"requirement_id", "category_lookup", "category_bindings"}.intersection(
            args
        )
        for _, args in dispatched
    )
    assert len(runner.call_cache) == 2
    feedback = _reference(_messages(runner))["category_lookups"][0]
    assert feedback["next_step"] == "check_requirements_and_complete"
    assert feedback["business_query_evidence_ids"] == ["task_1:query:3"]


@pytest.mark.parametrize("partial", [False, True])
def test_lookup_feedback_references_exact_population_and_current_schema(
    category_schema, partial
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema, **({"limit": 1} if partial else {}))
    reference = _reference(_messages(runner))
    assert reference["category_lookups"] == [
        {
            "query_evidence_id": "task_1:query:2",
            "schema_evidence_id": "task_1:schema:1",
            "manager": schema.manager,
            "root": "exposedItems",
            "snapshot": runner.evidence.get("task_1:schema:1").payload()["snapshot"],
            "complete": not partial,
            "next_step": "gather_complete_lookup" if partial else "bind_business_query",
            "schema_evidence_ids": ["task_1:schema:1"],
        }
    ]
    assert "category_lookups" in _messages(runner)[0].content
    assert "Display Alpha" not in str(reference["category_lookups"])


@pytest.mark.parametrize("change", ["schema", "exposure"])
def test_stale_or_hidden_lookup_never_gets_ready_feedback(category_schema, change):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    if change == "schema":
        import graphene

        GraphQL._schema = graphene.Schema(query=schema.query_type, auto_camelcase=False)
    else:
        GraphQL.manager_registry[schema.manager].chat_exposed = False
    clear_contract_cache()
    assert "category_lookups" not in _reference(_messages(runner))


def test_lookup_feedback_keeps_legitimate_cached_read_new_requirement_links(
    category_schema,
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    runtime = runner.runtimes["task_1"]
    extra = EvidenceRequirement("other_query", "query", "another use of lookup", None)
    runtime.task = replace(
        runtime.task, requirements=(*runtime.task.requirements, extra)
    )
    before = len(schema.calls)
    _, progress = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "name", "family", "rank"],
        requirement_id="other_query",
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
    )
    assert progress and len(schema.calls) == before
    assert (
        runner.evidence.for_requirement("task_1", extra)[0].evidence_id
        == "task_1:query:2"
    )
    feedback = _reference(_messages(runner))["category_lookups"]
    assert len(feedback) == 1 and feedback[0]["complete"] is True
    assert runtime.status == "running"


def test_executor_uses_complete_lookup_then_bound_business_query_then_completion(
    category_schema, monkeypatch
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    runtime = runner.runtimes["task_1"]
    lookup = {
        "manager": schema.manager,
        "fields": ["id", "name", "family"],
        "category_lookup": {"schema_evidence_id": "task_1:schema:1"},
    }
    calls = _record_rounds(
        monkeypatch,
        [
            ToolCallEvent(
                "schema",
                "get_manager_schema",
                {"manager": schema.manager, "view": "full"},
            ),
            ToolCallEvent("lookup", "query", lookup),
            ToolCallEvent("lookup-cache", "query", lookup),
            ToolCallEvent(
                "business",
                "query",
                {
                    "manager": schema.manager,
                    "fields": ["id", "name", "family"],
                    "filters": {"family_Exact": schema.actual},
                    "category_bindings": [_binding()],
                },
            ),
            {
                "action": "complete",
                "evidence_ids": ["task_1:schema:1", "task_1:query:3"],
            },
        ],
    )
    asyncio.run(runner.run_task(runtime))
    after_lookup = _reference(calls[2][1])
    after_repeat = _reference(calls[3][1])
    assert after_lookup["category_lookups"] == after_repeat["category_lookups"]
    assert after_repeat["category_lookups"][0]["next_step"] == "bind_business_query"
    assert after_repeat["last_tool_round"] == {"evidence_or_links_changed": False}
    assert runtime.status == "resolved" and not runtime.fallback_used
    assert runtime.selected_evidence_ids == ("task_1:schema:1", "task_1:query:3")
    assert len(schema.calls) == 2
    business = runner.evidence.get("task_1:query:3").payload()
    assert [row["id"] for row in business["data"]] == [1, 2]
    assert len(calls) == runner.prepared.budget.global_count == 5
    after_business = _reference(calls[4][1])["category_lookups"][0]
    assert after_business["next_step"] == "check_requirements_and_complete"
    assert after_business["business_query_evidence_ids"] == ["task_1:query:3"]


def test_rejected_category_binding_does_not_claim_business_query_completion(
    category_schema,
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    result, progress = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": "Display Alpha"},
        category_bindings=[_binding()],
    )
    assert result == {"status": "error", "code": "invalid_category_binding"}
    assert not progress
    feedback = _reference(_messages(runner))["category_lookups"][0]
    assert feedback["next_step"] == "bind_business_query"
    assert "business_query_evidence_ids" not in feedback


def test_empty_lookup_does_not_advertise_an_observed_category_binding(category_schema):
    schema = category_schema
    schema.rows.clear()
    runner = _public_runner(schema.manager)
    result, _ = _lookup(runner, schema)
    assert result["data"] == [] and result["complete"] is True
    feedback = _reference(_messages(runner))["category_lookups"][0]
    assert feedback["complete"] is True
    assert feedback["next_step"] == "resolve_unavailable_category_values"
    assert "business_query_evidence_ids" not in feedback


@pytest.mark.parametrize(
    "window",
    [
        {"limit": 1},
        {"limit": 1, "offset": 1},
        {"arguments": {"page": 1, "pageSize": 1}},
    ],
)
def test_later_complete_business_query_replaces_matching_partial_population(
    category_schema, window
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    result, _ = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
        **window,
    )
    assert result["complete"] is False
    feedback = _reference(_messages(runner))["category_lookups"][0]
    assert feedback["next_step"] == "gather_complete_business_query"
    assert feedback["business_query_evidence_ids"] == ["task_1:query:3"]
    result, _ = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
    )
    assert result["complete"] is True
    feedback = _reference(_messages(runner))["category_lookups"][0]
    assert feedback["next_step"] == "check_requirements_and_complete"
    assert feedback["business_query_evidence_ids"] == ["task_1:query:4"]
    # Replacement affects operational feedback only, never immutable history.
    assert runner.evidence.get("task_1:query:3").payload()["complete"] is False


@pytest.mark.parametrize("change", ["filter", "projection", "manager"])
def test_complete_different_business_scope_cannot_cover_old_partial(
    category_schema, change
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    business = {
        "manager": schema.manager,
        "fields": ["id", "family"],
        "filters": {"family_Exact": schema.actual},
        "category_bindings": [_binding()],
    }
    _call(runner, "query", **business, limit=1)
    changed = {
        "filter": {"filters": {"family_Exact": "other"}},
        "projection": {"fields": ["id", "name", "family"]},
        "manager": {"manager": "HiddenCategory"},
    }[change]
    result, _ = _call(runner, "query", **{**business, **changed})
    if change == "manager":
        assert result["code"] == "invalid_category_binding"
    else:
        assert result["complete"] is True
    feedback = _reference(_messages(runner))["category_lookups"][0]
    assert feedback["next_step"] == "gather_complete_business_query"
    assert "task_1:query:3" in feedback["business_query_evidence_ids"]


def test_each_distinct_partial_business_population_needs_its_own_complete_successor(
    category_schema,
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)

    def read(values, **window):
        return _call(
            runner,
            "query",
            manager=schema.manager,
            fields=["id", "family", "rank"],
            filters={"family_In": values},
            category_bindings=[_binding(path=["filters", "family_In"])],
            **window,
        )

    assert read([schema.actual], limit=1)[0]["complete"] is False
    assert read([schema.actual, "other"], limit=1)[0]["complete"] is False
    assert read([schema.actual])[0]["complete"] is True
    feedback = _reference(_messages(runner))["category_lookups"][0]
    assert feedback["next_step"] == "gather_complete_business_query"
    assert set(feedback["business_query_evidence_ids"]) == {
        "task_1:query:4",
        "task_1:query:5",
    }
    assert read([schema.actual, "other"])[0]["complete"] is True
    feedback = _reference(_messages(runner))["category_lookups"][0]
    assert feedback["next_step"] == "check_requirements_and_complete"
    assert set(feedback["business_query_evidence_ids"]) == {
        "task_1:query:5",
        "task_1:query:6",
    }


def test_earlier_complete_query_does_not_hide_a_new_partial_observation(
    category_schema,
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    business = {
        "manager": schema.manager,
        "fields": ["id", "family"],
        "filters": {"family_Exact": schema.actual},
        "category_bindings": [_binding()],
    }
    assert _call(runner, "query", **business)[0]["complete"] is True
    assert _call(runner, "query", **business, limit=1)[0]["complete"] is False
    feedback = _reference(_messages(runner))["category_lookups"][0]
    assert feedback["next_step"] == "gather_complete_business_query"


def test_overview_lookup_reuses_selected_details_at_same_snapshot(category_schema):
    schema = category_schema
    overview = execute_chat_tool(
        "get_manager_schema", {"manager": schema.manager}, schema.context
    )
    root = overview["roots"][0]
    filter_type = overview["root_fields"][root]["arguments"]["filter"][
        "type"
    ].removesuffix("!")
    names = (overview["type"], filter_type)
    runner = _public_runner(schema.manager)
    runtime = runner.runtimes["task_1"]
    runtime.task = replace(
        runtime.task,
        requirements=(
            replace(
                runtime.task.requirements[0],
                schema=SchemaBinding(schema.manager, "overview", (), "current"),
            ),
            EvidenceRequirement(
                "details",
                "schema",
                "category output and native filter input",
                None,
                schema=SchemaBinding(
                    schema.manager, "detail", names, overview["snapshot"]
                ),
            ),
            runtime.task.requirements[1],
        ),
        completion_criteria=("schema", "details", "query"),
    )
    _call(runner, "get_manager_schema", manager=schema.manager, requirement_id="schema")
    _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
    )
    _call(
        runner,
        "get_manager_schema",
        manager=schema.manager,
        view="detail",
        types=list(names),
        snapshot=overview["snapshot"],
        requirement_id="details",
    )
    feedback = _reference(_messages(runner))["category_lookups"][0]
    assert feedback["schema_evidence_ids"] == ["task_1:schema:1", "task_1:schema:3"]
    assert feedback["next_step"] == "bind_business_query"
    result, progress = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": schema.actual},
        category_bindings=[{**_binding(), "schema_evidence_id": "task_1:schema:3"}],
    )
    assert progress and [row["id"] for row in result["data"]] == [1, 2]
    feedback = _reference(_messages(runner))["category_lookups"][0]
    assert feedback["next_step"] == "check_requirements_and_complete"
    assert feedback["business_query_evidence_ids"] == ["task_1:query:4"]
    assert not any(
        record.payload().get("schema_view") == "full"
        for record in runner.evidence.records
        if record.kind == "schema"
    )
