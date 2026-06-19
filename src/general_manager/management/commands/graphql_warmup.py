"""Warm opted-in GraphQL property cache entries."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_warmup import warm_up_graphql_properties


class UnknownGraphQLWarmUpManagerError(CommandError):
    """Raised when a command manager argument cannot be resolved."""

    def __init__(self, manager_name: str) -> None:
        super().__init__(f"Unknown GraphQL manager: {manager_name}")


class Command(BaseCommand):
    """Run all-entry GraphQL property warm-up."""

    help = "Warm opted-in GraphQL property cache entries."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--manager",
            action="append",
            default=None,
            help=(
                "Manager class name from the GraphQL registry or import path. "
                "Can be supplied more than once."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        manager_classes = self._manager_classes(options.get("manager"))
        summary = warm_up_graphql_properties(manager_classes)
        self.stdout.write(
            self.style.SUCCESS(
                "GraphQL warm-up complete: "
                f"{summary.evaluated} evaluated, "
                f"{summary.failed} failed, "
                f"{summary.recipes} recipes"
            )
        )

    def _manager_classes(self, manager_names: list[str] | None) -> list[type] | None:
        if not manager_names:
            return None
        manager_classes: list[type] = []
        for manager_name in manager_names:
            if "." in manager_name:
                manager_classes.append(import_string(manager_name))
                continue
            manager_class = GraphQL.manager_registry.get(manager_name)
            if manager_class is None:
                raise UnknownGraphQLWarmUpManagerError(manager_name)
            manager_classes.append(manager_class)
        return manager_classes
