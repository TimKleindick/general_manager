"""Offline context-transport regressions, not evidence of model answer quality."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest
from django.test.utils import override_settings

from general_manager.chat.planned.models import (
    EvidenceRequirement,
    PlannedTask,
    ValidatedPlan,
)
from general_manager.chat.planned.planner import PlanningResult
from general_manager.chat.planned.resolver import ManagerCandidate
from general_manager.chat.planned.scheduler import (
    SchedulerCallbacks,
    prepare_planned_turn,
    iter_planned_read_events,
)
from general_manager.chat.providers.base import Message, TokenUsage, ToolCallEvent
from tests.unit.test_chat_planned_scheduler import (
    _Executor,
    _settings,
    _collect,
    _reference_content,
)


class _Resolver:
    def __init__(self, manager):
        self.manager = manager

    def resolve(self, _query, _anchors=()):
        return (ManagerCandidate(self.manager, ("exact manager name",), True),)


def _run(context, *, manager="Device", history=False, schema=None, fallback=False):
    requirements = [EvidenceRequirement("query", "query", "Read status fields", None)]
    if schema is not None:
        requirements.insert(
            0, EvidenceRequirement("schema", "schema", "Read field descriptions", None)
        )
    task = PlannedTask(
        "status",
        "Read statuses for " + manager,
        (),
        tuple(requirements),
        tuple(r.requirement_id for r in requirements),
        (),
    )
    messages = [Message(role="system", content="Existing application prompt")]
    if history:
        messages.extend(
            [
                Message(role="user", content="Earlier status question"),
                Message(
                    role="assistant",
                    content="UNVERIFIED_HISTORY_CLAIM: these status flags are equivalent",
                ),
            ]
        )
    planner_context = []

    async def planner(_text, supplied, *_args):
        planner_context.extend(supplied)
        return PlanningResult(ValidatedPlan("read", (task,)), TokenUsage())

    with override_settings(GENERAL_MANAGER={"CHAT": {"system_prompt": context}}):
        prepared = asyncio.run(
            prepare_planned_turn(
                "Explain both current flags",
                messages,
                _settings(),
                {},
                planner=planner,
                resolver=_Resolver(manager),
            )
        )
    rows = {
        "data": [{"id": 7, "enabled": False, "retained": True}],
        "total_count": 1,
        "has_more": False,
    }
    results = {"query": rows, "get_manager_schema": schema}
    ids = ["status:query:1"]
    responses = []
    if schema is not None:
        responses.append(
            ToolCallEvent("schema", "get_manager_schema", {"manager": manager})
        )
        ids = ["status:schema:1", "status:query:2"]
    responses.extend(
        [
            ToolCallEvent(
                "rows",
                "query",
                {"manager": manager, "fields": ["id", "enabled", "retained"]},
            ),
            {"action": "complete", "evidence_ids": ids},
        ]
    )
    if fallback:
        responses.append({"answer": "", "evidence_ids": []})
    responses.append({"answer": "Offline test response", "evidence_ids": ids})
    _Executor.responses = responses
    _Executor.responses_by_task = {}
    _Executor.calls = []
    _Executor.roles = []
    before_schema = deepcopy(schema)
    # A later settings read must not replace the turn's captured definitions.
    with override_settings(
        GENERAL_MANAGER={"CHAT": {"system_prompt": "LATER_CONFIGURATION"}}
    ):
        events = asyncio.run(
            _collect(
                iter_planned_read_events(
                    prepared,
                    scope={},
                    conversation=None,
                    messages=messages,
                    callbacks=SchedulerCallbacks(
                        execute_tool=lambda name, _args, _scope: results[name]
                    ),
                )
            )
        )
    assert events[-1]["type"] == "done"
    assert schema == before_schema
    references = []
    synthesis_messages = []
    for call in _Executor.calls:
        content = _reference_content(call)
        if content.startswith("RESOLVED_REFERENCE_DATA="):
            references.append(json.loads(content.split("=", 1)[1]))
            synthesis_messages.append(call)
    assert references
    return references, synthesis_messages, planner_context, prepared


@pytest.mark.parametrize(
    ("manager", "context"),
    [
        (
            "Device",
            "Device.enabled controls business availability. Device.retained controls record retention; these are independent statuses.",
        ),
        (
            "Subscription",
            "Subscription.enabled controls billing permission. Subscription.retained controls audit retention, not billing permission.",
        ),
    ],
)
@pytest.mark.parametrize("history", [False, True])
def test_configured_status_definitions_reach_synthesis_across_managers_and_followups(
    manager, context, history
):
    references, messages, planner_context, prepared = _run(
        context, manager=manager, history=history
    )
    reference = references[0]
    assert reference["configured_context"] == {
        "source": "GENERAL_MANAGER.CHAT.system_prompt",
        "text": context,
    }
    assert prepared.configured_context == context
    assert any(
        message.role == "system" and message.content == context
        for message in planner_context
    )
    assert context in [
        message.content for message in messages[0] if message.role == "system"
    ]
    assert "LATER_CONFIGURATION" not in json.dumps(reference)
    assert "UNVERIFIED_HISTORY_CLAIM" not in json.dumps(reference["resolved_evidence"])
    assert (
        "UNVERIFIED_HISTORY_CLAIM" in json.dumps(reference["conversation_context"])
    ) is history
    assert any(message.role == "assistant" for message in planner_context) is history
    evidence = reference["resolved_evidence"]
    assert evidence[0]["provenance"]["manager"] == manager
    assert evidence[0]["payload"]["data"][0] == {
        "id": 7,
        "enabled": False,
        "retained": True,
    }


@pytest.mark.parametrize(
    "context", [None, "", "  ", {"enabled": "invented"}, ["invented"], 42, False]
)
def test_missing_or_malformed_configured_metadata_does_not_invent_definitions(context):
    references, _, _, prepared = _run(context)
    assert "configured_context" not in references[0]
    assert prepared.configured_context == ""
    assert "invented" not in json.dumps(references)


def test_conflicting_schema_and_configured_definitions_remain_scoped_reference_data():
    context = "Device.enabled means business availability. Subscription.enabled means billing permission."
    schema = {
        "manager": "Device",
        "types": {
            "DeviceType": {
                "kind": "object",
                "fields": {
                    "enabled": {
                        "type": "Boolean",
                        "description": "Enabled means audit retention in this schema.",
                    },
                    "retained": {
                        "type": "Boolean",
                        "description": "Retained marks a record still kept in storage.",
                    },
                },
            }
        },
    }
    references, messages, _, _ = _run(context, schema=schema)
    assert references[0]["configured_context"]["text"] == context
    assert references[0]["resolved_evidence"][0]["payload"] == schema
    instruction = messages[0][1].content
    assert "conflicting" in instruction and "missing" in instruction
    assert "manager" in instruction
    assert "audit retention in this schema" not in instruction


def test_configured_instruction_retains_role_and_phase_contract_on_fallback():
    context = "Answer concisely in German using the configured business vocabulary."
    references, messages, _, _ = _run(context, fallback=True)
    assert len(references) == 2
    assert references[0] == references[1]
    for reference, call in zip(references, messages, strict=True):
        assert reference["configured_context"]["text"] == context
        assert call[0].role == "system" and call[0].content == context
        assert "as instructions" in call[1].content
        assert "not evidence of record values" in call[1].content
