"""Reconcile and clean durable upload intents in one bounded batch."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from general_manager.uploads.config import get_file_upload_settings
from general_manager.uploads.finalization import run_upload_cleanup

_BATCH_SIZE_ERROR = "--batch-size must be a positive integer"
_OLDER_THAN_ERROR = "--older-than must be a positive integer"


class Command(BaseCommand):
    """Run safe, retryable upload reconciliation and cleanup."""

    help = "Reconcile and clean a bounded batch of file upload intents."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--batch-size", type=int, default=None)
        parser.add_argument(
            "--older-than",
            type=int,
            default=None,
            metavar="SECONDS",
            help="Only expire or clean intents older than this many seconds.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        upload_settings = get_file_upload_settings()
        batch_size = options.get("batch_size")
        older_than = options.get("older_than")
        if batch_size is None:
            batch_size = upload_settings.cleanup_batch_size
        if older_than is None:
            older_than = upload_settings.cleanup_min_age_seconds
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size <= 0
        ):
            raise CommandError(_BATCH_SIZE_ERROR)
        if (
            isinstance(older_than, bool)
            or not isinstance(older_than, int)
            or older_than <= 0
        ):
            raise CommandError(_OLDER_THAN_ERROR)
        counts = run_upload_cleanup(
            batch_size=batch_size,
            older_than_seconds=older_than,
            dry_run=options.get("dry_run") is True,
        )
        self.stdout.write(
            " ".join(
                (
                    f"reconciled={counts.reconciled}",
                    f"expired={counts.expired}",
                    f"cleaned={counts.cleaned}",
                    f"deleted={counts.deleted}",
                    f"failed={counts.failed}",
                    f"skipped={counts.skipped}",
                )
            )
        )
