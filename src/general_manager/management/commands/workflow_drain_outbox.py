"""Drain pending workflow outbox records through the workflow task adapter."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from general_manager.workflow.tasks import publish_outbox_batch


class Command(BaseCommand):
    """Run one workflow outbox drain batch from a Django management command.

    The command accepts no custom arguments, calls `publish_outbox_batch()`,
    writes a styled success line to stdout, and returns `None`. It does not
    catch or wrap claim, routing, telemetry, inline task, or Celery dispatch
    exceptions from the task adapter.
    """

    help = "Drain and route pending workflow outbox records."

    def handle(self, *args: object, **options: object) -> None:
        """Drain one pending outbox batch and print the claimed row count.

        Args:
            *args: Positional command arguments; ignored.
            **options: Django command options; no custom options are read.

        Output:
            A styled success line:
            `Dispatched <count> outbox record(s) for routing.`, using `record`
            only when `<count>` is exactly `1`.

        Raises:
            Exception: Exceptions from `publish_outbox_batch()` are not wrapped.
        """
        del args, options
        processed = publish_outbox_batch()
        self.stdout.write(
            self.style.SUCCESS(
                f"Dispatched {processed} {self._record_label(processed)} for routing."
            )
        )

    def _record_label(self, count: int) -> str:
        """Return `outbox record` for one row, otherwise `outbox records`."""
        return "outbox record" if count == 1 else "outbox records"
