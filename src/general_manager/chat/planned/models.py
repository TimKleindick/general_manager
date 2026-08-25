"""Immutable domain types for validated planned-chat task graphs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


TaskStatus: TypeAlias = Literal[
    "pending",
    "running",
    "resolved",
    "blocked",
    "budget_exhausted",
]

TerminalReason: TypeAlias = Literal[
    "invalid_plan",
    "manager_unresolved",
    "dependency_blocked",
    "budget_exhausted",
    "deadline_exceeded",
    "provider_failed",
    "synthesis_failed",
]

RequirementKind: TypeAlias = Literal["schema", "path", "query", "calculation"]
RoutingFeature: TypeAlias = Literal[
    "has_dependency",
    "requires_calculation",
    "multiple_queries",
]
PlanIntent: TypeAlias = Literal["read", "mutation"]

CALCULATION_OPERATIONS: tuple[str, ...] = (
    "count",
    "sum",
    "average",
    "minimum",
    "maximum",
    "difference",
    "ratio",
    "percentage",
)


@dataclass(frozen=True)
class EvidenceRequirement:
    """One explicit piece of evidence needed to resolve a planned task."""

    requirement_id: str
    kind: RequirementKind
    description: str
    operation: str | None


@dataclass(frozen=True)
class PlannedTask:
    """A validated root or bounded dynamic child task."""

    task_id: str
    objective: str
    depends_on: tuple[str, ...]
    requirements: tuple[EvidenceRequirement, ...]
    completion_criteria: tuple[str, ...]
    routing_features: tuple[RoutingFeature, ...]
    parent_id: str | None = None


@dataclass(frozen=True)
class ValidatedPlan:
    """An immutable plan accepted by the planned-chat validator."""

    intent: PlanIntent
    tasks: tuple[PlannedTask, ...]
