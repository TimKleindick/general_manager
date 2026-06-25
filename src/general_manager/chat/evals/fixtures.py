"""Schema fixtures used by chat eval runs."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import graphene

from general_manager.api.graphql import GraphQL
from general_manager.chat.schema_index import clear_schema_index_cache
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


def setup_toy_schema() -> None:
    """Register the Material, Part, and Project toy eval schema."""
    _reset_eval_schema()

    class MaterialManager:
        chat_exposed = True

    class PartManager:
        chat_exposed = True

    class ProjectManager:
        chat_exposed = True

    materials = [
        {"id": 1, "name": "Steel", "density": 7.8},
        {"id": 2, "name": "Aluminum", "density": 2.7},
        {"id": 3, "name": "Cobalt", "density": 8.9},
    ]
    materials_by_name = {item["name"]: item for item in materials}
    parts = [
        {"id": 1, "name": "Bolt", "material": materials_by_name["Steel"]},
        {"id": 2, "name": "Bearing", "material": materials_by_name["Aluminum"]},
        {"id": 3, "name": "Gear", "material": materials_by_name["Cobalt"]},
    ]
    parts_by_name = {item["name"]: item for item in parts}
    projects = [
        {"id": 1, "name": "Apollo", "parts": [parts_by_name["Gear"]]},
        {"id": 2, "name": "Mercury", "parts": [parts_by_name["Bearing"]]},
    ]

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
        name = graphene.String()
        density__gt = graphene.Float()

    class PartFilter(_GrapheneInputObjectType):
        name = graphene.String()
        material__name = graphene.String()
        material__name__icontains = graphene.String()

    class ProjectFilter(_GrapheneInputObjectType):
        name = graphene.String()
        parts__name = graphene.String()
        parts__material__name = graphene.String()
        parts__material__name__icontains = graphene.String()

    class PageInfoType(_GrapheneObjectType):
        total_count = graphene.Int(required=True)

    class MaterialPageType(_GrapheneObjectType):
        items = graphene.List(MaterialType, required=True)
        page_info = graphene.Field(PageInfoType, required=True)

    class PartPageType(_GrapheneObjectType):
        items = graphene.List(PartType, required=True)
        page_info = graphene.Field(PageInfoType, required=True)

    class ProjectPageType(_GrapheneObjectType):
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
        records: list[dict[str, Any]], page_size: int | None
    ) -> dict[str, Any]:
        items = records[:page_size] if page_size is not None else records
        return {
            "items": items,
            "page_info": {"total_count": len(records)},
        }

    class Query(_GrapheneObjectType):
        materialmanager_list = graphene.Field(
            MaterialPageType,
            filter=graphene.Argument(MaterialFilter),
            page_size=graphene.Int(),
        )
        partmanager_list = graphene.Field(
            PartPageType,
            filter=graphene.Argument(PartFilter),
            page_size=graphene.Int(),
        )
        projectmanager_list = graphene.Field(
            ProjectPageType,
            filter=graphene.Argument(ProjectFilter),
            page_size=graphene.Int(),
        )

        def resolve_materialmanager_list(  # type: ignore[no-untyped-def]
            self, info, filter=None, page_size=None
        ):
            del self, info
            rows = [item for item in materials if _matches_filter(item, filter)]
            return _page_payload(rows, page_size)

        def resolve_partmanager_list(  # type: ignore[no-untyped-def]
            self, info, filter=None, page_size=None
        ):
            del self, info
            rows = [item for item in parts if _matches_filter(item, filter)]
            return _page_payload(rows, page_size)

        def resolve_projectmanager_list(  # type: ignore[no-untyped-def]
            self, info, filter=None, page_size=None
        ):
            del self, info
            rows = [item for item in projects if _matches_filter(item, filter)]
            return _page_payload(rows, page_size)

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
    cast(Any, PathMap.mapping)[("MaterialManager", "ProjectManager")] = SimpleNamespace(
        path=["material", "parts"]
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
        records: list[dict[str, Any]], page_size: int | None
    ) -> dict[str, Any]:
        items = records[:page_size] if page_size is not None else records
        return {"items": items, "page_info": {"total_count": len(records)}}

    query_attrs: dict[str, Any] = {}
    for name in manager_names:
        field_name = f"{name.lower()}_list"
        query_attrs[field_name] = graphene.Field(
            page_types[name],
            filter=graphene.Argument(filter_types[name]),
            page_size=graphene.Int(),
        )

        def _make_resolver(manager_name: str) -> Any:
            def _resolver(self, info, filter=None, page_size=None):  # type: ignore[no-untyped-def]
                del self, info
                rows = [
                    item
                    for item in records_by_manager[manager_name]
                    if _matches_filter(item, filter)
                ]
                return _page_payload(rows, page_size)

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


__all__ = ["setup_large_schema", "setup_toy_schema"]
