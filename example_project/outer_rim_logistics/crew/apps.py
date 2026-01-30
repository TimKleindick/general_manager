from __future__ import annotations

from django.apps import AppConfig


class CrewConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "crew"

    def ready(self) -> None:
        from . import managers  # noqa: F401
