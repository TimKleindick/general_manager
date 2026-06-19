"""Refresh due timeout-backed GraphQL warm-up recipes."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from general_manager.api.graphql_warmup import refresh_due_graphql_warmup_recipes


class Command(BaseCommand):
    """Refresh due timeout-backed GraphQL warm-up recipes."""

    help = "Refresh due timeout-backed GraphQL warm-up recipes."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of due recipes to refresh.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        refreshed = refresh_due_graphql_warmup_recipes(limit=options.get("limit"))
        self.stdout.write(
            self.style.SUCCESS(f"GraphQL warm-up refreshed {refreshed} recipes.")
        )
