"""Schema fixtures used by chat eval runs."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import json
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn, cast

import graphene

from general_manager.api.graphql import GraphQL
from general_manager.chat.schema_index import clear_schema_index_cache
from general_manager.chat.evals.runner import EvalCase, PlannedEvalRoleOverride
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.path_mapping import PathMap

if TYPE_CHECKING:

    class _GrapheneObjectType:
        pass

    class _GrapheneInputObjectType:
        pass

else:
    _GrapheneObjectType = graphene.ObjectType
    _GrapheneInputObjectType = graphene.InputObjectType


def _reset_eval_schema() -> None:
    GraphQL.reset_registry()
    GeneralManagerMeta.all_classes.clear()
    GeneralManagerMeta.pending_graphql_interfaces.clear()
    GeneralManagerMeta.pending_attribute_initialization.clear()
    PathMap.mapping.clear()
    if hasattr(PathMap, "instance"):
        delattr(PathMap, "instance")


def setup_toy_schema(
    *, data: Mapping[str, list[Mapping[str, Any]]] | None = None
) -> None:
    """Register the Material, Part, and Project toy eval schema.

    ``data`` is an optional in-memory fixture override.  Records may refer to
    related records by id, name, or a mapping containing either key.  The
    default data remains the small three-material fixture used by the shipped
    eval datasets.
    """
    _reset_eval_schema()

    class MaterialManager:
        """Toy manager exposing material records to chat evals."""

        chat_exposed = True

    class PartManager:
        """Toy manager exposing part records to chat evals."""

        chat_exposed = True

    class ProjectManager:
        """Toy manager exposing project records to chat evals."""

        chat_exposed = True

    if data is None:
        raw_materials: list[Mapping[str, Any]] = [
            {"id": 1, "name": "Steel", "density": 7.8},
            {"id": 2, "name": "Aluminum", "density": 2.7},
            {"id": 3, "name": "Cobalt", "density": 8.9},
        ]
        raw_parts: list[Mapping[str, Any]] = [
            {"id": 1, "name": "Bolt", "material": 1},
            {"id": 2, "name": "Bearing", "material": 2},
            {"id": 3, "name": "Gear", "material": 3},
        ]
        raw_projects: list[Mapping[str, Any]] = [
            {"id": 1, "name": "Apollo", "parts": [3]},
            {"id": 2, "name": "Mercury", "parts": [2]},
        ]
    else:
        raw_materials = list(data.get("materials", []))
        raw_parts = list(data.get("parts", []))
        raw_projects = list(data.get("projects", []))

    materials = [dict(item) for item in raw_materials]

    def _resolve_record(
        reference: Any,
        records: list[dict[str, Any]],
        relation_name: str,
    ) -> dict[str, Any]:
        if isinstance(reference, Mapping):
            if "id" in reference:
                reference = reference["id"]
            elif "name" in reference:
                reference = reference["name"]
        for record in records:
            if reference in {record.get("id"), record.get("name")}:
                return record
        raise ValueError(  # noqa: TRY003
            f"Unknown {relation_name} fixture reference: {reference!r}"
        )

    parts = []
    for raw_part in raw_parts:
        part = dict(raw_part)
        part["material"] = _resolve_record(part.get("material"), materials, "material")
        parts.append(part)

    projects = []
    for raw_project in raw_projects:
        project = dict(raw_project)
        project["parts"] = [
            _resolve_record(reference, parts, "part")
            for reference in project.get("parts", [])
        ]
        projects.append(project)

    class MaterialType(_GrapheneObjectType):
        """Materials used in manufacturing."""

        name = graphene.String()
        density = graphene.Float()

    class PartType(_GrapheneObjectType):
        """Inventory parts catalog."""

        name = graphene.String()
        material = graphene.Field(MaterialType)

    class ProjectType(_GrapheneObjectType):
        """Engineering projects."""

        name = graphene.String()
        parts = graphene.List(PartType)

    class MaterialFilter(_GrapheneInputObjectType):
        """Filter input for material query evals."""

        name = graphene.String()
        name__icontains = graphene.String()
        density__gt = graphene.Float()

    class PartFilter(_GrapheneInputObjectType):
        """Filter input for part query evals."""

        name = graphene.String()
        material__name = graphene.String()
        material__name__icontains = graphene.String()

    class ProjectFilter(_GrapheneInputObjectType):
        """Filter input for project and nested part/material evals."""

        name = graphene.String()
        name__icontains = graphene.String()
        parts__name = graphene.String()
        parts__material__name = graphene.String()
        parts__material__name__icontains = graphene.String()

    class PageInfoType(_GrapheneObjectType):
        """Pagination metadata returned by eval fixture lists."""

        total_count = graphene.Int(required=True)

    class MaterialPageType(_GrapheneObjectType):
        """Paged material query result for chat eval fixtures."""

        items = graphene.List(MaterialType, required=True)
        page_info = graphene.Field(PageInfoType, required=True)

    class PartPageType(_GrapheneObjectType):
        """Paged part query result for chat eval fixtures."""

        items = graphene.List(PartType, required=True)
        page_info = graphene.Field(PageInfoType, required=True)

    class ProjectPageType(_GrapheneObjectType):
        """Paged project query result for chat eval fixtures."""

        items = graphene.List(ProjectType, required=True)
        page_info = graphene.Field(PageInfoType, required=True)

    def _lookup_values(value: Any, segments: list[str]) -> list[Any]:
        if not segments:
            return [value]
        if isinstance(value, list):
            output: list[Any] = []
            for item in value:
                output.extend(_lookup_values(item, segments))
            return output
        if isinstance(value, dict):
            next_value = value.get(segments[0])
            return _lookup_values(next_value, segments[1:])
        return []

    def _matches_filter(record: dict[str, Any], filters: dict[str, Any] | None) -> bool:
        if not filters:
            return True
        for raw_key, expected in filters.items():
            parts = str(raw_key).split("__")
            op = "exact"
            if parts[-1] in {"icontains", "gt"}:
                op = parts.pop()
            actual_values = _lookup_values(record, parts)
            if op == "icontains":
                needle = str(expected).lower()
                if not any(needle in str(value).lower() for value in actual_values):
                    return False
                continue
            if op == "gt":
                try:
                    threshold = float(expected)
                except (TypeError, ValueError):
                    return False
                if not any(float(value) > threshold for value in actual_values):
                    return False
                continue
            if expected not in actual_values:
                return False
        return True

    def _page_payload(
        records: list[dict[str, Any]], page_size: int | None, page: int = 1
    ) -> dict[str, Any]:
        items = (
            records[(page - 1) * page_size : page * page_size]
            if page_size is not None
            else records
        )
        return {
            "items": items,
            "page_info": {"total_count": len(records)},
        }

    class Query(_GrapheneObjectType):
        """Root query exposing the toy manager fixture lists."""

        materialmanager_list = graphene.Field(
            MaterialPageType,
            filter=graphene.Argument(MaterialFilter),
            page_size=graphene.Int(),
            page=graphene.Int(default_value=1),
        )
        partmanager_list = graphene.Field(
            PartPageType,
            filter=graphene.Argument(PartFilter),
            page_size=graphene.Int(),
            page=graphene.Int(default_value=1),
        )
        projectmanager_list = graphene.Field(
            ProjectPageType,
            filter=graphene.Argument(ProjectFilter),
            page_size=graphene.Int(),
            page=graphene.Int(default_value=1),
        )

        def resolve_materialmanager_list(  # type: ignore[no-untyped-def]
            self, info, filter=None, page_size=None, page=1
        ):
            """Return material fixture rows matching the provided filters."""
            del self, info
            rows = [item for item in materials if _matches_filter(item, filter)]
            return _page_payload(rows, page_size, page)

        def resolve_partmanager_list(  # type: ignore[no-untyped-def]
            self, info, filter=None, page_size=None, page=1
        ):
            """Return part fixture rows matching the provided filters."""
            del self, info
            rows = [item for item in parts if _matches_filter(item, filter)]
            return _page_payload(rows, page_size, page)

        def resolve_projectmanager_list(  # type: ignore[no-untyped-def]
            self, info, filter=None, page_size=None, page=1
        ):
            """Return project fixture rows matching the provided filters."""
            del self, info
            rows = [item for item in projects if _matches_filter(item, filter)]
            return _page_payload(rows, page_size, page)

    GraphQL.graphql_type_registry = {
        "MaterialManager": MaterialType,
        "PartManager": PartType,
        "ProjectManager": ProjectType,
    }
    GraphQL.graphql_filter_type_registry = {
        "MaterialManager": MaterialFilter,
        "PartManager": PartFilter,
        "ProjectManager": ProjectFilter,
    }
    GraphQL.manager_registry = cast(
        Any,
        {
            "MaterialManager": MaterialManager,
            "PartManager": PartManager,
            "ProjectManager": ProjectManager,
        },
    )
    GraphQL._query_class = Query
    GraphQL._schema = graphene.Schema(query=Query)

    PathMap("MaterialManager")
    cast(Any, PathMap.mapping)[("PartManager", "MaterialManager")] = SimpleNamespace(
        path=["material"]
    )
    cast(Any, PathMap.mapping)[("ProjectManager", "PartManager")] = SimpleNamespace(
        path=["parts"]
    )
    cast(Any, PathMap.mapping)[("ProjectManager", "MaterialManager")] = SimpleNamespace(
        path=["parts", "material"]
    )
    clear_schema_index_cache()


def setup_large_schema(*, manager_count: int = 150, chain_length: int = 8) -> None:
    """Register a synthetic schema with many managers and a known relation chain."""
    _reset_eval_schema()
    if manager_count < 1:
        msg = "manager_count must be positive"
        raise ValueError(msg)
    chain_length = max(1, min(chain_length, manager_count))

    manager_names = [
        f"SyntheticManager{index:02d}" for index in range(1, manager_count + 1)
    ]
    manager_classes = {
        name: type(name, (), {"chat_exposed": True}) for name in manager_names
    }
    records_by_manager: dict[str, list[dict[str, Any]]] = {}
    for index, name in reversed(list(enumerate(manager_names, start=1))):
        record: dict[str, Any] = {
            "id": index,
            "name": f"{name} record",
            "code": f"SM{index:02d}-001",
            "status": "active",
        }
        if index < chain_length:
            record["next_item"] = records_by_manager[manager_names[index]][0]
        records_by_manager[name] = [record]

    graphene_types: dict[str, type[Any]] = {}
    for index, name in reversed(list(enumerate(manager_names, start=1))):
        attrs: dict[str, Any] = {
            "__doc__": (
                f"Synthetic manager {index:02d} for large schema discovery tests."
            ),
            "name": graphene.String(),
            "code": graphene.String(),
            "status": graphene.String(),
        }
        if index < chain_length:
            attrs["next_item"] = graphene.Field(graphene_types[manager_names[index]])
        graphene_types[name] = type(f"{name}Type", (_GrapheneObjectType,), attrs)

    filter_types = {
        name: type(
            f"{name}Filter",
            (_GrapheneInputObjectType,),
            {
                "name": graphene.String(),
                "code": graphene.String(),
                "status": graphene.String(),
            },
        )
        for name in manager_names
    }

    class PageInfoType(_GrapheneObjectType):
        """Pagination metadata returned by synthetic large-schema lists."""

        total_count = graphene.Int(required=True)

    page_types = {
        name: type(
            f"{name}PageType",
            (_GrapheneObjectType,),
            {
                "items": graphene.List(graphene_types[name], required=True),
                "page_info": graphene.Field(PageInfoType, required=True),
            },
        )
        for name in manager_names
    }

    def _matches_filter(record: dict[str, Any], filters: dict[str, Any] | None) -> bool:
        if not filters:
            return True
        return all(record.get(str(key)) == value for key, value in filters.items())

    def _page_payload(
        records: list[dict[str, Any]], page_size: int | None, page: int = 1
    ) -> dict[str, Any]:
        items = (
            records[(page - 1) * page_size : page * page_size]
            if page_size is not None
            else records
        )
        return {"items": items, "page_info": {"total_count": len(records)}}

    query_attrs: dict[str, Any] = {}
    for name in manager_names:
        field_name = f"{name.lower()}_list"
        query_attrs[field_name] = graphene.Field(
            page_types[name],
            filter=graphene.Argument(filter_types[name]),
            page_size=graphene.Int(),
            page=graphene.Int(default_value=1),
        )

        def _make_resolver(manager_name: str) -> Any:
            def _resolver(self, info, filter=None, page_size=None, page=1):  # type: ignore[no-untyped-def]
                del self, info
                rows = [
                    item
                    for item in records_by_manager[manager_name]
                    if _matches_filter(item, filter)
                ]
                return _page_payload(rows, page_size, page)

            return _resolver

        query_attrs[f"resolve_{field_name}"] = _make_resolver(name)

    Query = type("SyntheticQuery", (_GrapheneObjectType,), query_attrs)

    GraphQL.graphql_type_registry = graphene_types
    GraphQL.graphql_filter_type_registry = filter_types
    GraphQL.manager_registry = cast(Any, manager_classes)
    GraphQL._query_class = Query
    GraphQL._schema = graphene.Schema(query=Query)

    PathMap(manager_names[0])
    for start in range(chain_length):
        for end in range(start + 1, chain_length):
            cast(Any, PathMap.mapping)[(manager_names[start], manager_names[end])] = (
                SimpleNamespace(path=["next_item"] * (end - start))
            )
    clear_schema_index_cache()


class DeterministicPlannedProvider:
    """Role-pinned in-process provider for planned eval cases only."""

    scripts: ClassVar[dict[str, dict[str, list[dict[str, Any]]]]] = {}
    positions: ClassVar[dict[tuple[str, str], set[int]]] = {}
    case_name: str
    role: str

    @classmethod
    def configure(cls, case: EvalCase) -> None:
        """Reset the deterministic scripts for one eval run."""
        planned = case.expectations.get("planned", {})
        raw_scripts = planned.get("scripts", {}) if isinstance(planned, dict) else {}
        cls.scripts[case.name] = {
            str(role): _expand_planned_script(entries)
            for role, entries in raw_scripts.items()
            if isinstance(entries, list)
        }
        cls.positions = {
            key: value for key, value in cls.positions.items() if key[0] != case.name
        }

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "DeterministicPlannedProvider":
        provider = cls()
        provider.case_name = str(config["case_name"])
        provider.role = str(config["role"])
        return provider

    async def complete(
        self, messages: list[object], _tools: list[object]
    ) -> AsyncIterator[TextChunkEvent | ToolCallEvent | DoneEvent]:
        key = (self.case_name, self.role)
        scripts = type(self).scripts[self.case_name][self.role]
        task_id = _planned_task_id(messages)
        consumed = type(self).positions.setdefault(key, set())
        position = next(
            (
                index
                for index, candidate in enumerate(scripts)
                if index not in consumed and candidate.get("task_id") in (None, task_id)
            ),
            None,
        )
        if position is None:
            _script_exhausted(self.role)
        consumed.add(position)
        step = scripts[position]
        sleep_seconds = step.get("sleep_seconds")
        if isinstance(sleep_seconds, (int, float)) and sleep_seconds > 0:
            await asyncio.sleep(sleep_seconds)
        usage = step.get("usage", {})
        if "tool" in step:
            yield ToolCallEvent(
                id=f"{self.role}-{position}",
                name=str(step["tool"]),
                args=dict(step.get("args", {})),
            )
        else:
            payload = step.get("text", step)
            yield TextChunkEvent(json.dumps(payload, separators=(",", ":")))
        yield DoneEvent(
            TokenUsage(
                input_tokens=int(usage.get("input_tokens", 1)),
                output_tokens=int(usage.get("output_tokens", 1)),
            )
        )


def _script_exhausted(role: str) -> NoReturn:
    raise RuntimeError(  # noqa: TRY003
        f"deterministic script exhausted for {role}"
    )


def _expand_planned_script(entries: list[Any]) -> list[dict[str, Any]]:
    """Expand concise repeated fake tool steps into concrete provider rounds."""
    expanded: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        repeat = entry.get("repeat_tool")
        if not isinstance(repeat, dict):
            expanded.append(dict(entry))
            continue
        count = repeat.get("count")
        if not isinstance(count, int) or count < 1:
            continue
        base_args = repeat.get("args", {})
        for index in range(count):
            args = dict(base_args) if isinstance(base_args, dict) else {}
            args["eval_round"] = index + 1
            if isinstance(entry.get("task_id"), str):
                args["eval_task"] = entry["task_id"]
            expanded.append(
                {
                    "task_id": entry.get("task_id"),
                    "tool": repeat.get("tool"),
                    "args": args,
                }
            )
    return expanded


def _planned_task_id(messages: list[object]) -> str | None:
    """Read the current user reference, which can precede native tool history."""
    for message in reversed(messages):
        content = getattr(message, "content", "")
        if (
            getattr(message, "role", None) == "user"
            and isinstance(content, str)
            and content.startswith("REFERENCE_DATA=")
        ):
            break
    else:
        return None
    try:
        reference = json.loads(content.removeprefix("REFERENCE_DATA="))
    except json.JSONDecodeError:
        return None
    task = reference.get("task") if isinstance(reference, dict) else None
    task_id = task.get("task_id") if isinstance(task, dict) else None
    return task_id if isinstance(task_id, str) else None


def planned_role_overrides(
    case: EvalCase,
) -> dict[str, PlannedEvalRoleOverride]:
    """Return one deterministic fake provider profile for every planned role."""
    DeterministicPlannedProvider.configure(case)
    provider_path = "general_manager.chat.evals.fixtures.DeterministicPlannedProvider"
    return {
        role: PlannedEvalRoleOverride(
            provider_path=provider_path,
            provider_config={"case_name": case.name, "role": role},
            trust_group="local",
        )
        for role in (
            "planner",
            "executor",
            "synthesizer",
            "fallback",
        )
    }


__all__ = [
    "DeterministicPlannedProvider",
    "planned_role_overrides",
    "setup_large_schema",
    "setup_toy_schema",
]
