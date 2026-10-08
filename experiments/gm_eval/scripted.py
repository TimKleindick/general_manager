"""Deterministic model replies for infrastructure tests; never tool answers."""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
import json
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from general_manager.chat.providers.base import ChatEvent, Message, ToolDefinition


class ScriptedFactory:
    """Each role consumes declared model replies through the real provider path."""

    offline_only = True

    def __init__(self, responses: Mapping[str, Sequence[Any]]) -> None:
        self.responses = {role: deque(values) for role, values in responses.items()}

    def __call__(self, config: Mapping[str, Any]) -> Any:
        factory = self

        class ScriptedProvider:
            reported_usage = None
            requests = 0

            async def complete(
                self, messages: list[Message], tools: list[ToolDefinition]
            ) -> AsyncIterator[ChatEvent]:
                from general_manager.chat.providers.base import (
                    DoneEvent,
                    TextChunkEvent,
                    TokenUsage,
                    ToolCallEvent,
                )

                self.requests += 1
                role = str(config["role"])
                response = factory.responses[role].popleft()
                if isinstance(response, BaseException):
                    raise response
                if isinstance(response, str):
                    yield TextChunkEvent(response)
                elif isinstance(response, ToolCallEvent):
                    yield response
                else:
                    for event in response:
                        yield event
                    return
                yield DoneEvent(TokenUsage())

        return ScriptedProvider()


def make_script(case: Mapping[str, Any], runtime: Any) -> Any:
    """Probe real schema/data for the case, without pretending to solve its task.

    The replies exercise provider routing, persistence and a real read. They do
    not contain gold facts, semantic judgments, or fabricated tool responses.
    """
    contract = str(case.get("contract", "customer"))
    manager = "Customer"
    arguments: dict[str, Any] = {
        "manager": manager,
        "filters": {"code": "C01"},
        "fields": ["code", "name", "aliases"],
    }
    if contract in {
        "revenue",
        "rank_revenue",
        "clarify_importance",
        "fx_rank",
        "missing_revenue",
        "zero_missing",
    }:
        manager = "ProjectCommercial"
        arguments = {
            "manager": manager,
            "filters": {"project": {"code": "P01"}},
            "fields": ["year", "plannedRevenue", {"project": ["code"]}],
        }
    elif contract in {"plan", "clarify_development", "actual_plan"}:
        manager = "ShipmentPlan"
        arguments = {
            "manager": manager,
            "filters": {"project": {"code": "P01"}, "year": 2031},
            "fields": ["year", "quantity", {"project": ["code"]}],
        }
    elif contract in {
        "forecast",
        "forecast_boreal",
        "insufficient",
        "recommend",
        "metric_correction",
        "final",
    }:
        manager = "CustomerOutlook"
        arguments = {
            "manager": manager,
            "filters": {"customer": {"code": "C01"}, "year": 2031},
            "fields": [
                "year",
                "forecast",
                "existingPlan",
                "historyComplete",
                {"customer": ["code"]},
            ],
        }
    elif contract in {
        "top_material",
        "empty",
        "copper_projects",
        "both_materials",
        "no_shipments",
        "supplier_path",
        "shared_material",
        "disconnected",
        "three_turn",
    }:
        manager = "Project"
        arguments = {
            "manager": manager,
            "filters": {
                "customer": {"code": "C01"},
                "materialsList": {"any": {"code": "M01"}},
            },
            "fields": ["code"],
        }
    elif contract in {
        "projects",
        "mixed",
        "follow_filter",
        "owner_billto",
        "provenance",
        "cycle",
    }:
        manager = "Project"
        arguments = {
            "manager": manager,
            "filters": {"code": "P01"},
            "fields": ["code", {"customer": ["code"]}],
        }
    elif contract in {"material", "brass"}:
        manager = "Material"
        arguments = {
            "manager": manager,
            "filters": {"code": "M01"},
            "fields": ["code", "name", "aliases"],
        }
    elif contract == "steel_inventory_scope":
        manager = "Material"
        arguments = {
            "manager": manager,
            "filters": {"family": "steel"},
            "fields": ["code", "name", "family", "active"],
        }
    elif contract == "active_customers":
        manager = "Customer"
        arguments = {
            "manager": manager,
            "filters": {"active": True},
            "fields": ["code", "name", "active"],
        }
    elif contract == "ongoing_projects":
        manager = "Project"
        arguments = {
            "manager": manager,
            "filters": {"status": "ongoing"},
            "fields": ["code", "name", "status"],
        }
    elif contract == "material_density":
        manager = "Material"
        arguments = {
            "manager": manager,
            "filters": {"code": "M01"},
            "fields": ["code", "densityGCm3"],
        }
    elif contract in {"project_costs_wbs", "project_costs_project"}:
        manager = "ProjectCost"
        arguments = {
            "manager": manager,
            "filters": {
                "kind": "actual",
                "costDate_Gte": "2025-01-01",
                "costDate_Lt": "2026-01-01",
            },
            "fields": [
                "code",
                "kind",
                "costDate",
                "netAmount",
                "currency",
                {"project": ["code"]},
                {"wbs": ["code", "name"]},
            ],
        }
    elif contract == "recent_shipments":
        manager = "Shipment"
        arguments = {
            "manager": manager,
            "filters": {
                "shippedAt_Gte": "2026-09-01",
                "shippedAt_Lt": "2026-10-01",
            },
            "fields": ["code", "shippedAt", "quantity", "source"],
        }
    clarification_first = (
        contract
        in {
            "clarify_importance",
            "clarify_development",
            "clarify_actuals",
            "ambiguous_customer",
            "weight",
            "three_turn",
            "final",
        }
        and len(case["turns"]) > 1
    )

    def factory(config: Mapping[str, Any]) -> Any:
        class ProbeProvider:
            reported_usage = None
            requests = 0

            async def complete(
                self, messages: list[Message], tools: list[ToolDefinition]
            ) -> AsyncIterator[ChatEvent]:
                from general_manager.chat.providers.base import (
                    DoneEvent,
                    TextChunkEvent,
                    TokenUsage,
                    ToolCallEvent,
                )
                from general_manager.chat.planned.schema_projection import (
                    logical_messages,
                )

                self.requests += 1
                reference_message = next(
                    message.content
                    for message in reversed(logical_messages(messages))
                    if message.role == "user"
                    and message.content.startswith(
                        ("REFERENCE_DATA=", "RESOLVED_REFERENCE_DATA=")
                    )
                )
                reference = json.loads(reference_message.split("=", 1)[1])
                clarification = (
                    clarification_first
                    and reference.get("original_request") == case["turns"][0]
                )
                tool_name = "get_manager_schema" if clarification else "query"
                call_args = (
                    {"manager": "Customer", "view": "full"}
                    if clarification
                    else arguments
                )
                requirement_kind = "schema" if clarification else "query"
                if "catalog_and_schema_summary" in reference:
                    reply = {
                        "intent": "read",
                        "tasks": [
                            {
                                "task_id": "probe",
                                "objective": f"Inspect {manager} for a synthetic infrastructure probe",
                                "depends_on": [],
                                "requirements": [
                                    {
                                        "requirement_id": "rows",
                                        "kind": requirement_kind,
                                        "description": "Read existing synthetic rows",
                                        "operation": None,
                                    }
                                ],
                                "completion_criteria": ["rows"],
                                "routing_features": [],
                            }
                        ],
                    }
                elif "resolved_evidence" in reference:
                    reply = {
                        "answer": "Which metric and years should define this request?"
                        if clarification
                        else "Offline infrastructure probe completed; no model task answer was evaluated.",
                        "evidence_ids": [
                            item["evidence_id"]
                            for item in reference["resolved_evidence"]
                        ],
                    }
                else:
                    evidence = reference["task_evidence"]
                    if not evidence:
                        yield ToolCallEvent(
                            "probe-read",
                            tool_name,
                            call_args,
                        )
                        yield DoneEvent(TokenUsage())
                        return
                    reply = {
                        "action": "complete",
                        "evidence_ids": [item["evidence_id"] for item in evidence],
                    }
                yield TextChunkEvent(json.dumps(reply))
                yield DoneEvent(TokenUsage())

        return ProbeProvider()

    script = cast(Any, factory)
    script.offline_only = True
    return script
