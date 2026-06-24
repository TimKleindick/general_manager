"""Warm opted-in GraphQL property cache entries."""

from __future__ import annotations

from argparse import ArgumentParser
from typing import cast

from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_warmup import (
    GraphQLWarmUpManagerClass,
    warm_up_graphql_properties,
)


class UnknownGraphQLWarmUpManagerError(CommandError):
    """Raised when a command manager argument cannot be resolved."""

    def __init__(self, manager_name: str) -> None:
        """Build an error for an unknown GraphQL registry manager name."""
        super().__init__(f"Unknown GraphQL manager: {manager_name}")


class InvalidGraphQLWarmUpManagerPathError(CommandError):
    """Raised when a dotted manager import path cannot be resolved."""

    def __init__(self, manager_path: str) -> None:
        """Build an error for an invalid dotted manager import path."""
        super().__init__(f"Invalid GraphQL manager path: {manager_path}")


class Command(BaseCommand):
    """Run all-entry GraphQL property warm-up."""

    help = "Warm opted-in GraphQL property cache entries."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments for selecting managers."""
        parser.add_argument(
            "--manager",
            action="append",
            default=None,
            help=(
                "Manager class name from the GraphQL registry or import path. "
                "Can be supplied more than once."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        """Run warm-up for selected managers and print a summary."""
        del args
        manager_classes = self._manager_classes(
            cast(list[str] | None, options.get("manager"))
        )
        summary = warm_up_graphql_properties(manager_classes)
        self.stdout.write(
            self.style.SUCCESS(
                "GraphQL warm-up complete: "
                f"{summary.evaluated} evaluated, "
                f"{summary.failed} failed, "
                f"{summary.recipes} recipes"
            )
        )

    def _manager_classes(
        self,
        manager_names: list[str] | None,
    ) -> list[GraphQLWarmUpManagerClass] | None:
        """Resolve manager names or dotted import paths from command options."""
        if not manager_names:
            return None
        manager_classes: list[GraphQLWarmUpManagerClass] = []
        for manager_name in manager_names:
            if "." in manager_name:
                try:
                    manager_classes.append(
                        cast(GraphQLWarmUpManagerClass, import_string(manager_name))
                    )
                except (AttributeError, ImportError, ModuleNotFoundError) as error:
                    raise InvalidGraphQLWarmUpManagerPathError(manager_name) from error
                continue
            manager_class = GraphQL.manager_registry.get(manager_name)
            if manager_class is None:
                raise UnknownGraphQLWarmUpManagerError(manager_name)
            manager_classes.append(cast(GraphQLWarmUpManagerClass, manager_class))
        return manager_classes
