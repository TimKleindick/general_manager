#!/usr/bin/env python3
"""Run the chat eval suite against a live LLM provider.

Usage:
    python scripts/run_chat_evals.py                          # all datasets, default model
    python scripts/run_chat_evals.py --dataset basic_queries   # single dataset
    python scripts/run_chat_evals.py --model qwen3.5:9b        # different model
    python scripts/run_chat_evals.py --base-url http://host.docker.internal:11434
    python scripts/run_chat_evals.py -v                        # verbose failure details
    python scripts/run_chat_evals.py -v --show-chat          # verbose + stream conversations to stdout
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# Ensure the project root is on sys.path so test settings are importable.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")

import django

django.setup()

from types import SimpleNamespace

import graphene

from general_manager.api.graphql import GraphQL
from general_manager.chat.evals.runner import (
    list_datasets,
    print_report,
    run_eval_suite_sync,
)
from general_manager.chat.providers.ollama import OllamaProvider
from general_manager.chat.schema_index import clear_schema_index_cache
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.path_mapping import PathMap
from tests.utils.simple_manager_interface import BaseTestInterface


# ---------------------------------------------------------------------------
# Test schema setup
# ---------------------------------------------------------------------------


def setup_test_schema() -> None:
    """Register test managers, GraphQL types, and PathMap relationships."""
    GraphQL.reset_registry()
    GeneralManagerMeta.all_classes.clear()
    GeneralManagerMeta.pending_graphql_interfaces.clear()
    GeneralManagerMeta.pending_attribute_initialization.clear()
    PathMap.mapping.clear()
    if hasattr(PathMap, "instance"):
        delattr(PathMap, "instance")

    # -- Managers ----------------------------------------------------------

    class MaterialInterface(BaseTestInterface):
        @staticmethod
        def get_attribute_types() -> dict[str, dict[str, object]]:
            return {"name": {"type": str}, "density": {"type": float}}

    class MaterialManager(GeneralManager):
        Interface = MaterialInterface

    class PartInterface(BaseTestInterface):
        @staticmethod
        def get_attribute_types() -> dict[str, dict[str, object]]:
            return {"name": {"type": str}}

    class PartManager(GeneralManager):
        Interface = PartInterface

    class ProjectInterface(BaseTestInterface):
        @staticmethod
        def get_attribute_types() -> dict[str, dict[str, object]]:
            return {"name": {"type": str}}

    class ProjectManager(GeneralManager):
        Interface = ProjectInterface

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

    # -- GraphQL types -----------------------------------------------------

    class MaterialType(graphene.ObjectType):
        """Materials used in manufacturing."""

        name = graphene.String()
        density = graphene.Float()

    class PartType(graphene.ObjectType):
        """Inventory parts catalog."""

        name = graphene.String()
        material = graphene.Field(MaterialType)

    class ProjectType(graphene.ObjectType):
        """Engineering projects."""

        name = graphene.String()
        parts = graphene.List(PartType)

    class MaterialFilter(graphene.InputObjectType):
        name = graphene.String()
        density__gt = graphene.Float()

    class PartFilter(graphene.InputObjectType):
        name = graphene.String()
        material__name = graphene.String()
        material__name__icontains = graphene.String()

    class ProjectFilter(graphene.InputObjectType):
        name = graphene.String()
        parts__name = graphene.String()
        parts__material__name = graphene.String()
        parts__material__name__icontains = graphene.String()

    class PageInfoType(graphene.ObjectType):
        total_count = graphene.Int(required=True)

    class MaterialPageType(graphene.ObjectType):
        items = graphene.List(MaterialType, required=True)
        page_info = graphene.Field(PageInfoType, required=True)

    class PartPageType(graphene.ObjectType):
        items = graphene.List(PartType, required=True)
        page_info = graphene.Field(PageInfoType, required=True)

    class ProjectPageType(graphene.ObjectType):
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

    class Query(graphene.ObjectType):
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
    GraphQL.manager_registry = {
        "MaterialManager": MaterialManager,
        "PartManager": PartManager,
        "ProjectManager": ProjectManager,
    }
    GraphQL._query_class = Query
    GraphQL._schema = graphene.Schema(query=Query)

    # -- Relationships -----------------------------------------------------

    # Initialize the singleton once so later missing lookups do not rebuild the
    # cache and overwrite these seeded eval-only paths.
    PathMap("MaterialManager")

    PathMap.mapping[("PartManager", "MaterialManager")] = SimpleNamespace(
        path=["material"]
    )
    PathMap.mapping[("ProjectManager", "PartManager")] = SimpleNamespace(path=["parts"])
    PathMap.mapping[("MaterialManager", "ProjectManager")] = SimpleNamespace(
        path=["material", "parts"]
    )

    clear_schema_index_cache()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        default=None,
        help=f"Run a single dataset ({', '.join(list_datasets())})",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Ollama model name (default: from settings, typically gemma4:e4b)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=(
            "Ollama base URL (default: from settings; in devcontainers localhost "
            "is automatically remapped to host.docker.internal when available)"
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed failure information",
    )
    parser.add_argument(
        "--show-chat",
        "--stdout",
        action="store_true",
        dest="show_chat",
        help="Stream each eval conversation to stdout while it runs",
    )
    args = parser.parse_args()

    setup_test_schema()

    overrides: dict[str, str] = {}
    if args.model:
        overrides["model"] = args.model
    if args.base_url:
        overrides["base_url"] = args.base_url

    if overrides:
        # Monkey-patch provider config for this run.
        original = OllamaProvider._provider_config

        @staticmethod  # type: ignore[misc]
        def _patched_config() -> dict:
            cfg = original()
            cfg.update(overrides)
            return cfg

        OllamaProvider._provider_config = _patched_config  # type: ignore[assignment]

    config = OllamaProvider._provider_config()
    resolved_base_url, remapped = _resolve_ollama_base_url(str(config["base_url"]))
    if resolved_base_url != config["base_url"]:
        # Apply the resolved base URL after any CLI overrides are in place.
        original = OllamaProvider._provider_config

        @staticmethod  # type: ignore[misc]
        def _patched_config() -> dict:
            cfg = original()
            cfg["base_url"] = resolved_base_url
            return cfg

        OllamaProvider._provider_config = _patched_config  # type: ignore[assignment]
        config = OllamaProvider._provider_config()
    dataset_names = [args.dataset] if args.dataset else None

    print(f"Provider: OllamaProvider ({config['model']})")
    print(f"Base URL: {config['base_url']}")
    if remapped:
        print("Note: remapped localhost Ollama URL for container access")
    print(f"Datasets: {dataset_names or list_datasets()}")
    print()

    provider = OllamaProvider()
    results = run_eval_suite_sync(
        provider,
        dataset_names,
        stream=sys.stdout if args.show_chat else None,
    )
    report = print_report(results, verbose=args.verbose)
    print(report)

    if not all(r.passed for r in results):
        sys.exit(1)


def _resolve_ollama_base_url(base_url: str) -> tuple[str, bool]:
    """Map localhost Ollama URLs to the Docker host when needed."""
    parsed = urlsplit(base_url)
    hostname = parsed.hostname
    if hostname not in {"127.0.0.1", "localhost"}:
        return base_url, False
    if not _host_port_reachable(hostname, parsed.port or 11434):
        docker_host = "host.docker.internal"
        if _host_port_reachable(docker_host, parsed.port or 11434):
            netloc = docker_host
            if parsed.port is not None:
                netloc = f"{netloc}:{parsed.port}"
            return urlunsplit(
                (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
            ), True
    return base_url, False


def _host_port_reachable(host: str, port: int) -> bool:
    """Check whether a TCP host/port pair is reachable quickly."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    main()
