"""Verify production setup in a fresh Django process with an empty database."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_production_discovery_checks_signals_and_graphql_without_maintenance(tmp_path):
    app = tmp_path / "startup_probe"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "managers.py").write_text(
        textwrap.dedent("""
        from django.db import models
        from general_manager import GeneralManager, ReadOnlyInterface, IndexConfig

        class Country(GeneralManager):
            _data = [{"code": "DE"}]
            class Interface(ReadOnlyInterface):
                code = models.CharField(max_length=2, unique=True)
            class SearchConfig:
                indexes = [IndexConfig(name="countries", fields=["code"])]
    """)
    )
    (tmp_path / "startup_settings.py").write_text(
        textwrap.dedent("""
        SECRET_KEY = "startup-test"
        DEBUG = False
        INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "general_manager", "startup_probe"]
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        ROOT_URLCONF = "startup_urls"
        MIDDLEWARE = []
        GENERAL_MANAGER = {
            "AUTOCREATE_GRAPHQL": True,
            "SEARCH_BACKEND": "general_manager.search.backends.dev.DevSearchBackend",
            "SEARCH_AUTO_REINDEX": True,
            "SEARCH_RECONCILE_ENABLED": True,
        }
    """)
    )
    (tmp_path / "startup_urls.py").write_text("urlpatterns = []\n")
    script = textwrap.dedent("""
        import django
        from django.db import connection
        from contextlib import ExitStack
        from unittest.mock import patch

        statements = []
        def observe(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(observe):
            django.setup()
            from startup_probe.managers import Country
            from general_manager.manager.meta import GeneralManagerMeta
            from general_manager.api.graphql import GraphQL
            from general_manager.cache.signals import post_data_change
            from general_manager.interface.infrastructure.startup_hooks import registered_startup_hooks
            from general_manager.interface.infrastructure.system_checks import registered_system_checks
            from general_manager.search.backend_registry import get_search_backend
            from django.core.management.commands.check import Command

            assert Country in GeneralManagerMeta.all_classes
            assert Country.Interface._model._general_manager_class is Country
            assert Country.Interface in registered_startup_hooks()
            assert Country.Interface in registered_system_checks()
            assert post_data_change.has_listeners(Country)
            schema = GraphQL.get_schema()
            assert schema is not None
            assert "countryList" in schema.graphql_schema.query_type.fields
            backend = get_search_backend()
            capability = Country.Interface.require_capability("read_only_management")
            with ExitStack() as stack:
                sync = stack.enter_context(patch.object(capability, "sync_data"))
                upsert = stack.enter_context(patch.object(backend, "upsert"))
                delete = stack.enter_context(patch.object(backend, "delete"))
                index = stack.enter_context(patch.object(backend, "ensure_index"))
                Command().run_from_argv(["manage.py", "check"])
                sync.assert_not_called()
                upsert.assert_not_called()
                delete.assert_not_called()
                index.assert_not_called()
        assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE")) for sql in statements), statements
    """)
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "startup_settings"
    env["PYTHONPATH"] = os.pathsep.join(
        (str(tmp_path), str(Path(__file__).resolve().parents[2] / "src"))
    )
    result = subprocess.run(  # noqa: S603 - fixed script and current Python interpreter.
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
