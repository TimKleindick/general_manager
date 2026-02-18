from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from general_manager.mcp.ai_assistant import AIAssistant
from general_manager.mcp.contract import QueryContext


class _FakePolicyEngine:
    def discover_domains(self) -> list[dict[str, Any]]:
        return [{"domain": "Project"}]


@dataclass
class _FakeResponse:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.payload


class _FakeService:
    def __init__(self) -> None:
        self.policy_engine = _FakePolicyEngine()

    def discover_data_domains(self, context: QueryContext) -> _FakeResponse:
        del context
        return _FakeResponse(
            {
                "data": {"domains": [{"domain": "Project"}]},
                "provenance": {},
                "errors": [],
            }
        )

    def describe_domain_schema(
        self, domain: str, context: QueryContext
    ) -> _FakeResponse:
        del domain
        del context
        return _FakeResponse(
            {
                "data": {"schema": {"domain": "Project"}},
                "provenance": {},
                "errors": [],
            }
        )

    def explain_query_plan(self, request: Any, context: QueryContext) -> _FakeResponse:
        del request
        del context
        return _FakeResponse({"data": {"plan": {}}, "provenance": {}, "errors": []})

    def run_query(self, request: Any, context: QueryContext) -> _FakeResponse:
        del request
        del context
        return _FakeResponse(
            {
                "data": {"rows": [], "aggregates": {}, "page_info": {}},
                "provenance": {},
                "errors": [],
            }
        )


class _FakeSynthesizer:
    def synthesize(
        self,
        question: str,
        query_request: dict[str, Any],
        gateway_payload: dict[str, Any],
    ) -> str:
        del question
        del query_request
        del gateway_payload
        return "ok"


class _RepairingPlanner:
    def __init__(self) -> None:
        self.repair_called = False

    def plan(self, question: str, domains: list[dict[str, Any]]) -> dict[str, Any]:
        del question
        del domains
        return {
            "domain": "Project",
            "operation": "aggregate",
            "metrics": [{"field": "id"}],
        }

    def repair_plan(
        self,
        *,
        question: str,
        domains: list[dict[str, Any]],
        invalid_plan: dict[str, Any],
        validation_error: str,
    ) -> dict[str, Any]:
        del question
        del domains
        del invalid_plan
        del validation_error
        self.repair_called = True
        return {"domain": "Project", "operation": "discover"}


def test_normalize_plan_payload_handles_german_count_question() -> None:
    payload = {
        "domain": "Project",
        "operation": "aggregate",
        "filters": [{"field": "derivatives", "op": "count_gt", "value": 5}],
        "metrics": [],
    }

    normalized = AIAssistant._normalize_plan_payload(
        "wie viele projekte haben mehr als 5 derivate", payload
    )

    assert normalized["operation"] == "query"
    assert normalized["filters"][0]["field"] == "derivative_count"
    assert normalized["filters"][0]["op"] == "count_gt"
    assert normalized["select"] == ["id", "name", "derivative_count"]


def test_answer_uses_repair_plan_when_initial_plan_is_invalid() -> None:
    planner = _RepairingPlanner()
    assistant = AIAssistant(
        service=_FakeService(),
        planner=planner,
        synthesizer=_FakeSynthesizer(),
    )
    context = QueryContext(user=object(), request_id="req-1", tenant=None)

    result = assistant.answer("frage", context)

    assert planner.repair_called is True
    assert result["query_request"]["operation"] == "discover"
