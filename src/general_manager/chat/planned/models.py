"""Immutable domain types for validated planned-chat task graphs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from general_manager.chat.planned.contract import (
    CALCULATION_OPERATIONS as CALCULATION_OPERATIONS,
    REQUIREMENT_KINDS as REQUIREMENT_KINDS,
    ROUTING_FEATURE_VALUES as ROUTING_FEATURE_VALUES,
    PlanIntent as PlanIntent,
    RequirementKind as RequirementKind,
    RoutingFeature as RoutingFeature,
)


TaskStatus: TypeAlias = Literal[
    "pending",
    "running",
    "awaiting_clarification",
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


@dataclass(frozen=True)
class ConversionBinding:
    """Observed rowwise factor and exact task-linked schema witnesses."""

    factor_path: tuple[str, ...]
    factor_identity_path: tuple[str, ...]
    schema_requirement_ids: tuple[str, ...]
    schema_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        values = (
            self.factor_path,
            self.factor_identity_path,
            self.schema_requirement_ids,
            self.schema_evidence_ids,
        )
        if (
            any(
                not isinstance(items, tuple)
                or not items
                or any(not isinstance(item, str) or not item.strip() for item in items)
                for items in values
            )
            or len(self.schema_requirement_ids) != len(self.schema_evidence_ids)
            or len(set(self.schema_requirement_ids)) != len(self.schema_requirement_ids)
            or len(set(self.schema_evidence_ids)) != len(self.schema_evidence_ids)
            or len(self.schema_evidence_ids) > 6
        ):
            message = "invalid conversion source/path/schema binding"
            raise ValueError(message)

    def as_mapping(self) -> dict[str, object]:
        return {
            "factor_path": list(self.factor_path),
            "factor_identity_path": list(self.factor_identity_path),
            "schema_requirement_ids": list(self.schema_requirement_ids),
            "schema_evidence_ids": list(self.schema_evidence_ids),
        }

    @classmethod
    def from_mapping(cls, value: object) -> ConversionBinding:
        from collections.abc import Mapping

        keys = {
            "factor_path",
            "factor_identity_path",
            "schema_requirement_ids",
            "schema_evidence_ids",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != keys
            or any(not isinstance(value[key], list) for key in keys)
        ):
            message = "invalid conversion binding shape"
            raise ValueError(message)
        return cls(
            tuple(value["factor_path"]),
            tuple(value["factor_identity_path"]),
            tuple(value["schema_requirement_ids"]),
            tuple(value["schema_evidence_ids"]),
        )


@dataclass(frozen=True)
class CalculationBinding:
    """A declared source/field/group contract, never an asserted business label."""

    source_requirement_ids: tuple[str, ...]
    value_path: tuple[str, ...] | None
    group_by: tuple[tuple[str, ...], ...] = ()
    unit_path: tuple[str, ...] | None = None
    utc_year_by: tuple[tuple[str, ...], ...] = ()
    conversion: ConversionBinding | None = None

    def __post_init__(self) -> None:
        paths = (
            *self.group_by,
            *self.utc_year_by,
            *((self.value_path,) if self.value_path is not None else ()),
            *((self.unit_path,) if self.unit_path is not None else ()),
        )
        if (
            (
                self.conversion is not None
                and (
                    not isinstance(self.conversion, ConversionBinding)
                    or not self.value_path
                    or self.unit_path is None
                )
            )
            or not isinstance(self.source_requirement_ids, tuple)
            or not self.source_requirement_ids
            or any(
                not isinstance(item, str) or not item.strip()
                for item in self.source_requirement_ids
            )
            or len(set(self.source_requirement_ids)) != len(self.source_requirement_ids)
            or not isinstance(self.group_by, tuple)
            or not isinstance(self.utc_year_by, tuple)
            or any(
                not isinstance(path, tuple)
                or any(not isinstance(part, str) or not part.strip() for part in path)
                for path in paths
            )
            or any(not path for path in (*self.group_by, *self.utc_year_by))
            or self.unit_path == ()
            or (
                self.value_path is None
                and (self.group_by or self.utc_year_by or self.unit_path is not None)
            )
        ):
            message = "invalid calculation source/field/group binding."
            raise ValueError(message)

    def as_mapping(self) -> dict[str, object]:
        return {
            **(
                {"conversion": self.conversion.as_mapping()}
                if self.conversion is not None
                else {}
            ),
            **(
                {"utc_year_by": [list(path) for path in self.utc_year_by]}
                if self.utc_year_by
                else {}
            ),
            "source_requirement_ids": list(self.source_requirement_ids),
            "value_path": None if self.value_path is None else list(self.value_path),
            "group_by": [list(path) for path in self.group_by],
            "unit_path": None if self.unit_path is None else list(self.unit_path),
        }

    @classmethod
    def from_mapping(cls, value: object) -> "CalculationBinding":
        from collections.abc import Mapping

        conversion = None
        if isinstance(value, Mapping) and "conversion" in value:
            conversion = ConversionBinding.from_mapping(value["conversion"])
            value = {key: item for key, item in value.items() if key != "conversion"}
        if (
            not isinstance(value, Mapping)
            or set(value)
            not in (
                {"source_requirement_ids", "value_path", "group_by", "unit_path"},
                {
                    "source_requirement_ids",
                    "value_path",
                    "group_by",
                    "unit_path",
                    "utc_year_by",
                },
            )
            or not isinstance(value.get("utc_year_by", []), list)
            or any(not isinstance(path, list) for path in value.get("utc_year_by", []))
            or not isinstance(value["source_requirement_ids"], list)
            or not isinstance(value["group_by"], list)
            or any(not isinstance(path, list) for path in value["group_by"])
            or any(
                value[key] is not None and not isinstance(value[key], list)
                for key in ("value_path", "unit_path")
            )
        ):
            message = "invalid calculation binding shape."
            raise ValueError(message)
        return cls(
            tuple(value["source_requirement_ids"]),
            None if value["value_path"] is None else tuple(value["value_path"]),
            tuple(tuple(path) for path in value["group_by"]),
            None if value["unit_path"] is None else tuple(value["unit_path"]),
            tuple(tuple(path) for path in value.get("utc_year_by", [])),
            conversion,
        )


@dataclass(frozen=True)
class SchemaBinding:
    """Exact manager/view/type/snapshot scope, independent of prose descriptions."""

    manager: str
    view: str
    types: tuple[str, ...]
    snapshot: str

    def __post_init__(self) -> None:
        from general_manager.chat.schema_inspection import (
            SCHEMA_VIEWS,
            SNAPSHOT_PATTERN,
            TYPE_NAME_PATTERN,
        )

        if (
            not isinstance(self.manager, str)
            or not self.manager.strip()
            or not isinstance(self.view, str)
            or self.view not in SCHEMA_VIEWS
            or not isinstance(self.types, tuple)
            or any(
                not isinstance(name, str) or not TYPE_NAME_PATTERN.fullmatch(name)
                for name in self.types
            )
            or len(set(self.types)) != len(self.types)
            or bool(self.types) != (self.view == "detail")
            or not isinstance(self.snapshot, str)
            or (
                self.snapshot != "current"
                and not SNAPSHOT_PATTERN.fullmatch(self.snapshot)
            )
        ):
            message = "invalid schema manager/view/type/snapshot binding."
            raise ValueError(message)

    def as_mapping(self) -> dict[str, object]:
        return {
            "manager": self.manager,
            "view": self.view,
            "types": list(self.types),
            "snapshot": self.snapshot,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "SchemaBinding":
        from collections.abc import Mapping

        if (
            not isinstance(value, Mapping)
            or set(value) != {"manager", "view", "types", "snapshot"}
            or not isinstance(value["types"], list)
        ):
            message = "invalid schema binding shape."
            raise ValueError(message)
        return cls(
            value["manager"], value["view"], tuple(value["types"]), value["snapshot"]
        )


@dataclass(frozen=True)
class EvidenceRequirement:
    """One explicit piece of evidence needed to resolve a planned task."""

    requirement_id: str
    kind: RequirementKind
    description: str
    operation: str | None
    binding: CalculationBinding | None = None
    binding_required: bool = False
    schema: SchemaBinding | None = None


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
