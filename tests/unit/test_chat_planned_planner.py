"""Contract tests for structured planned-chat requests."""

from __future__ import annotations

import json
import asyncio
from types import MappingProxyType
from typing import ClassVar

import pytest

from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.config import PlannedChatSettings, ProviderProfile
from general_manager.chat.planned.planner import InvalidPlanError, plan_request
from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    TokenUsage,
)


def _plan() -> dict[str, object]:
    return {
        "intent": "read",
        "tasks": [
            {
                "task_id": "task_1",
                "objective": "Find parts.",
                "depends_on": [],
                "requirements": [
                    {
                        "requirement_id": "query_1",
                        "kind": "query",
                        "description": "Query parts.",
                        "operation": None,
                    }
                ],
                "completion_criteria": ["query_1"],
                "routing_features": [],
            }
        ],
    }


class _PlannerProvider:
    responses: ClassVar[list[str]] = []
    calls: ClassVar[list[list[Message]]] = []

    @classmethod
    def from_config(cls, _config: object) -> _PlannerProvider:
        return cls()

    async def complete(self, messages: list[Message], _tools: list[object]):
        type(self).calls.append(messages)
        yield TextChunkEvent(type(self).responses.pop(0))
        yield DoneEvent(TokenUsage(input_tokens=2, output_tokens=3))


def _settings() -> PlannedChatSettings:
    profile = ProviderProfile(
        "planner",
        "tests.unit.test_chat_planned_planner._PlannerProvider",
        MappingProxyType({"model": "test"}),
        "local",
    )
    return PlannedChatSettings(
        enabled=True,
        profiles=MappingProxyType({"planner": profile}),
        roles=MappingProxyType({"planner": "planner", "fallback_executor": "planner"}),
        catalog_source=None,
    )


def test_planner_corrects_invalid_json_then_returns_validated_plan() -> None:
    _PlannerProvider.calls.clear()
    _PlannerProvider.responses = ["not json", json.dumps(_plan())]

    plan = asyncio.run(
        plan_request("show parts", [], _settings(), RoundBudget(()), {"parts": []})
    )

    assert plan.intent == "read"
    assert len(_PlannerProvider.calls) == 2


def test_planner_rejects_write_request_misclassified_as_read() -> None:
    _PlannerProvider.calls.clear()
    _PlannerProvider.responses = [json.dumps(_plan())] * 3

    with pytest.raises(InvalidPlanError, match="invalid_plan"):
        asyncio.run(
            plan_request(
                "show parts then delete the obsolete part",
                [],
                _settings(),
                RoundBudget(()),
                {},
            )
        )


def test_planner_uses_fallback_after_one_invalid_correction() -> None:
    _PlannerProvider.calls.clear()
    _PlannerProvider.responses = ["not json", "still not json", json.dumps(_plan())]
    budget = RoundBudget(())

    plan = asyncio.run(plan_request("show parts", [], _settings(), budget, {}))

    assert plan.tasks[0].task_id == "task_1"
    assert len(_PlannerProvider.calls) == 3
    assert budget.global_used == 3
    reference = json.loads(
        _PlannerProvider.calls[0][1].content.removeprefix("REFERENCE_DATA=")
    )
    assert reference["catalog_and_schema_summary"] == {}
    assert (
        "routing_features"
        in reference["required_json_schema"]["properties"]["tasks"]["items"]["required"]
    )
