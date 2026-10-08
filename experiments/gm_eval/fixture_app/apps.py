"""App identity for generated synthetic models."""

from django.apps import AppConfig


class FixtureAppConfig(AppConfig):
    name = "experiments.gm_eval.fixture_app"
    label = "gm_eval_fixture"
    default_auto_field = "django.db.models.BigAutoField"
