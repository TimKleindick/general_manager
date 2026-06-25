"""Refresh due timeout-backed GraphQL warm-up recipes.

This command module does not define `__all__`; Django discovers the `Command`
class by module path.
"""

from __future__ import annotations

from argparse import ArgumentParser
from collections.abc import Mapping

from django.core.management.base import BaseCommand, CommandError

from general_manager.api.graphql_warmup import refresh_due_graphql_warmup_recipes


class InvalidGraphQLWarmUpRefreshLimitError(CommandError):
    """Raised when the due-refresh command receives an invalid limit."""

    def __init__(self) -> None:
        """Build the fixed command-option validation error message.

        Message:
            `GraphQL warm-up refresh limit must be an integer or omitted.`
        """
        super().__init__("GraphQL warm-up refresh limit must be an integer or omitted.")


class Command(BaseCommand):
    """Refresh due timeout-backed GraphQL warm-up recipes from the CLI.

    The command accepts an optional integer `--limit`, delegates to
    `refresh_due_graphql_warmup_recipes(limit=...)`, writes a success summary to
    stdout as a styled success line,
    `GraphQL warm-up refreshed <count> recipe(s).`, and returns `None`. The
    singular label `recipe` is used only when the refreshed count is exactly
    `1`. Invalid programmatic `limit` option values raise
    `InvalidGraphQLWarmUpRefreshLimitError` before the refresh executor is
    called; executor errors propagate unchanged.
    """

    help = "Refresh due timeout-backed GraphQL warm-up recipes."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register `--limit` as an optional integer argument.

        The option uses `type=int`, `default=None`, and the help text
        `Maximum number of due recipes to refresh.`.
        """
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of due recipes to refresh.",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Refresh due timeout recipes and print the number refreshed.

        Args:
            *args: Positional command arguments; ignored.
            **options: Django command options. `limit` must be `None` or an
                integer, and `bool` is rejected even though it subclasses `int`.

        Raises:
            InvalidGraphQLWarmUpRefreshLimitError: If `limit` is not `None` or
                an integer.
            Exception: Refresh executor exceptions are not wrapped.

        Output:
            A styled success line,
            `GraphQL warm-up refreshed <count> recipe(s).`, using `recipe`
            only when `<count>` is exactly `1`.
        """
        del args
        refreshed = refresh_due_graphql_warmup_recipes(
            limit=self._limit_from_options(options)
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"GraphQL warm-up refreshed {refreshed} "
                f"{self._recipe_label(refreshed)}."
            )
        )

    def _limit_from_options(self, options: Mapping[str, object]) -> int | None:
        """Return the validated `limit` option for the refresh executor.

        Args:
            options: Parsed Django command options.

        Returns:
            The integer limit or `None` when omitted.

        Raises:
            InvalidGraphQLWarmUpRefreshLimitError: If `limit` is neither `None`
                nor a non-boolean integer.
        """
        limit = options.get("limit")
        if limit is None:
            return None
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise InvalidGraphQLWarmUpRefreshLimitError()
        return limit

    def _recipe_label(self, count: int) -> str:
        """Return `recipe` for exactly one refreshed item, otherwise `recipes`."""
        return "recipe" if count == 1 else "recipes"
