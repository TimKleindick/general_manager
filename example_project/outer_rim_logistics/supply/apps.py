from __future__ import annotations

from django.apps import AppConfig


class SupplyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "outer_rim_logistics.supply"

    def ready(self) -> None:
        from . import managers  # import triggers manager registration
