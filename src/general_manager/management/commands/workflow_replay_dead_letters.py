from __future__ import annotations

from django.core.management.base import BaseCommand

from general_manager.workflow.models import WorkflowOutbox


class Command(BaseCommand):
    help = "Replay workflow dead-letter outbox entries by moving them back to pending."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="Maximum number of dead-letter rows to requeue.",
        )

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        del args
        limit = max(1, int(options["limit"]))
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
        )
        self.stdout.write(self.style.SUCCESS(f"Requeued {updated} dead-letter rows."))
