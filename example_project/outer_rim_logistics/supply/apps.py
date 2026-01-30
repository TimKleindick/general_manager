from __future__ import annotations

from django.apps import AppConfig


class SupplyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "supply"

    def ready(self) -> None:
        from . import managers  # noqa: F401
