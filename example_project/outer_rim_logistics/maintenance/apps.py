from __future__ import annotations

from django.apps import AppConfig


class MaintenanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "maintenance"

    def ready(self) -> None:
        from . import managers  # noqa: F401
