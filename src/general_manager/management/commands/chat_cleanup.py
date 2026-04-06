from __future__ import annotations

from django.core.management.base import BaseCommand

from general_manager.chat.models import cleanup_expired_chat_records
from general_manager.chat.settings import get_chat_settings


class Command(BaseCommand):
    help = "Remove expired chat conversations and pending confirmations."

    def handle(self, *args, **options) -> None:  # type: ignore[no-untyped-def]
        del args, options
        ttl_hours = int(get_chat_settings().get("ttl_hours", 24))
        deleted = cleanup_expired_chat_records(ttl_hours=ttl_hours)
        self.stdout.write(
            self.style.SUCCESS(
                "Deleted "
                f"{deleted['conversations']} chat conversations and "
                f"{deleted['pending_confirmations']} pending confirmations."
            )
        )
