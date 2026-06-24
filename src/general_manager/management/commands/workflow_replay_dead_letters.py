"""Replay workflow dead-letter outbox rows back to pending status.

This command module does not define `__all__`; Django discovers the `Command`
class by module path.
"""

from __future__ import annotations

from argparse import ArgumentParser
from collections.abc import Mapping

from django.core.management.base import BaseCommand, CommandError

from general_manager.workflow.models import WorkflowOutbox


class InvalidWorkflowReplayLimitError(CommandError):
    """Raised when the replay command receives an invalid limit."""

    def __init__(self) -> None:
        """Build the fixed command-option validation error message.

        Message:
            `Workflow replay limit must be an integer.`
        """
        super().__init__("Workflow replay limit must be an integer.")


class Command(BaseCommand):
    """Move dead-letter workflow outbox rows back to pending.

    The command accepts `--limit`, defaults to `1000`, selects the oldest
    `WorkflowOutbox` ids whose status is `WorkflowOutbox.STATUS_DEAD_LETTER` by
    `created_at`, updates only those selected ids, resets replay bookkeeping
    fields, writes a status line to stdout, and returns `None`. Non-positive
    limits requeue no rows, print the styled success line
    `Requeued 0 dead-letter rows.`, and return before any database query.
    Invalid programmatic `limit` option values raise
    `InvalidWorkflowReplayLimitError` before the database is queried; ORM errors
    are not wrapped.
    """

    help = "Replay workflow dead-letter outbox entries by moving them back to pending."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register `--limit` as an optional integer argument.

        The option uses `type=int`, `default=1000`, and the help text
        `Maximum number of dead-letter rows to requeue.`.
        """
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="Maximum number of dead-letter rows to requeue.",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Replay up to `limit` dead-letter rows and print the result.

        Args:
            *args: Positional command arguments; ignored.
            **options: Django command options. `limit` must be an integer, and
                `bool` is rejected even though it subclasses `int`.

        Output:
            The styled success line `Requeued 0 dead-letter rows.` when `limit`
            is non-positive. `No dead-letter outbox rows found.` when a
            positive limit selects no rows. Otherwise a styled success line,
            `Requeued <count> dead-letter row(s).`, using `row` only when
            `<count>` is exactly `1`.

        Raises:
            InvalidWorkflowReplayLimitError: If `limit` is not an integer.
            Exception: Query and update errors are not wrapped.
        """
        del args
        limit = self._limit_from_options(options)
        if limit <= 0:
            self.stdout.write(self.style.SUCCESS("Requeued 0 dead-letter rows."))
            return
        ids = list(
            WorkflowOutbox.objects.filter(status=WorkflowOutbox.STATUS_DEAD_LETTER)
            .order_by("created_at")
            .values_list("id", flat=True)[:limit]
        )
        if not ids:
            self.stdout.write("No dead-letter outbox rows found.")
            return
        updated = WorkflowOutbox.objects.filter(id__in=ids).update(
            status=WorkflowOutbox.STATUS_PENDING,
            last_error=None,
            claim_token=None,
            claimed_at=None,
            attempts=0,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Requeued {updated} {self._dead_letter_label(updated)}."
            )
        )

    def _limit_from_options(self, options: Mapping[str, object]) -> int:
        """Return the validated replay limit from parsed command options.

        Missing `limit` uses the parser default of `1000`.

        Raises:
            InvalidWorkflowReplayLimitError: If `limit` is not a non-boolean
                integer.
        """
        limit = options.get("limit", 1000)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise InvalidWorkflowReplayLimitError()
        return limit

    def _dead_letter_label(self, count: int) -> str:
        """Return `dead-letter row` for one row, otherwise `dead-letter rows`."""
        return "dead-letter row" if count == 1 else "dead-letter rows"
