from __future__ import annotations

from django.core.management.base import BaseCommand

from general_manager.workflow.tasks import publish_outbox_batch


class Command(BaseCommand):
    help = "Drain and route pending workflow outbox records."

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        del args, options
        processed = publish_outbox_batch()
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} outbox records."))
