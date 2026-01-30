from __future__ import annotations

from django.apps import AppConfig


class MissionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mission"

    def ready(self) -> None:
        from . import managers  # noqa: F401
