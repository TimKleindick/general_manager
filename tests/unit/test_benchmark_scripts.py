"""Regression coverage for benchmark cleanup and reproducible, safe reports."""

from __future__ import annotations

import importlib
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


class _WorkloadFailed(RuntimeError):
    """Stop before running a workload against real services."""


class _CleanupFailed(RuntimeError):
    """Simulate a schema cleanup failure."""


@pytest.mark.parametrize(
    "script_name", ["benchmark_create_many", "benchmark_bulk_throughput"]
)
@pytest.mark.parametrize("cleanup_failure", ["drop_models", "schema_editor", None])
def test_main_always_restores_registry_and_tears_down_database(
    monkeypatch: pytest.MonkeyPatch, script_name: str, cleanup_failure: str | None
) -> None:
    from django.contrib.auth import get_user_model
    import django.db
    import django.test.runner
    import general_manager.manager.general_manager as manager_module
    from general_manager.manager.meta import GeneralManagerMeta
    import tests.utils.database as database_utils

    script = importlib.import_module(f"scripts.{script_name}")
    user_model = get_user_model()
    prior_managers = list(GeneralManagerMeta.all_classes)
    monkeypatch.setattr(GeneralManagerMeta, "all_classes", prior_managers)
    monkeypatch.setattr(
        user_model, "history", SimpleNamespace(model=user_model), raising=False
    )

    class BenchmarkManager:
        def __init_subclass__(cls) -> None:
            cls.Interface._model = user_model

    events: list[str] = []
    old_config = object()

    def teardown_databases(config: object) -> None:
        assert config is old_config
        assert GeneralManagerMeta.all_classes == prior_managers
        events.append("database_teardown")

    runner = SimpleNamespace(
        setup_databases=lambda: old_config, teardown_databases=teardown_databases
    )

    def schema_editor() -> Any:
        events.append("schema_editor")
        if cleanup_failure == "schema_editor":
            raise _CleanupFailed
        return nullcontext(object())

    connection = SimpleNamespace(vendor="postgresql", schema_editor=schema_editor)

    def drop_test_models(_editor: object, model_classes: Any) -> None:
        assert list(model_classes) == [user_model, user_model]
        events.append("drop_models")
        if cleanup_failure == "drop_models":
            raise _CleanupFailed

    def fail_workload(*_args: object, **_kwargs: object) -> None:
        raise _WorkloadFailed

    monkeypatch.setattr(
        user_model._default_manager.__class__, "create_user", fail_workload
    )
    monkeypatch.setattr(manager_module, "GeneralManager", BenchmarkManager)
    monkeypatch.setattr(django.db, "connection", connection)
    monkeypatch.setattr(django.test.runner, "DiscoverRunner", lambda **_: runner)
    monkeypatch.setattr(
        database_utils, "create_test_models", lambda *_: [user_model, user_model]
    )
    monkeypatch.setattr(database_utils, "drop_test_models", drop_test_models)
    if script_name == "benchmark_create_many":
        monkeypatch.setattr(script, "GeneralManager", BenchmarkManager)
        monkeypatch.setattr(script, "connection", connection)
        monkeypatch.setattr(script, "DiscoverRunner", lambda **_: runner)
        monkeypatch.setattr(
            script, "create_test_models", database_utils.create_test_models
        )
        monkeypatch.setattr(script, "drop_test_models", drop_test_models)
    else:
        monkeypatch.setattr(script, "_configure_environment", lambda _: None)
        import general_manager.models as manager_models

        monkeypatch.setattr(manager_models, "BenchTarget", None, raising=False)
        monkeypatch.setattr(manager_models, "BenchItem", None, raising=False)
    monkeypatch.setattr("sys.argv", [script_name])

    with pytest.raises(_CleanupFailed if cleanup_failure else _WorkloadFailed):
        script.main()
    assert GeneralManagerMeta.all_classes == prior_managers
    assert events == (
        ["schema_editor", "database_teardown"]
        if cleanup_failure == "schema_editor"
        else ["schema_editor", "drop_models", "database_teardown"]
    )


@pytest.mark.parametrize("dependency", ["django-redis", "channels-redis"])
def test_development_requirements_include_benchmark_redis_backends(
    dependency: str,
) -> None:
    from packaging.requirements import Requirement

    requirements_path = Path(__file__).parents[2] / "requirements" / "development.txt"
    dependencies = {
        Requirement(line.split("#", 1)[0].strip()).name
        for line in requirements_path.read_text().splitlines()
        if line.strip() and not line.startswith(("#", "-"))
    }
    assert dependency in dependencies


@pytest.mark.parametrize(
    "name", ["benchmark_bulk_throughput.py", "benchmark_bulk_throughput_settings.py"]
)
def test_harness_fingerprint_covers_script_and_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    from scripts import benchmark_bulk_throughput as script

    monkeypatch.setattr(
        script, "__file__", str(tmp_path / "benchmark_bulk_throughput.py")
    )
    for filename in (
        "benchmark_bulk_throughput.py",
        "benchmark_bulk_throughput_settings.py",
    ):
        (tmp_path / filename).write_text("original")
    original = script._harness_source_hash()
    (tmp_path / name).write_text("changed")
    assert script._harness_source_hash() != original


def test_append_rejects_changed_or_missing_harness_fingerprint() -> None:
    from scripts import benchmark_bulk_throughput as script

    current = {"environment": {"benchmark_harness_sha256": "new"}, "configuration": {}}
    for previous_hash in ("old", None):
        previous = {
            "environment": {"benchmark_harness_sha256": previous_hash},
            "configuration": {},
        }
        with pytest.raises(RuntimeError, match="benchmark_harness_sha256"):
            script._validate_append_compatibility(previous, current)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("redis://alice:s%40cret@localhost:6379/15", "redis://localhost:6379/15"),
        (
            "rediss://:secret@[::1]:6379/14?password=secret&db=14",
            "rediss://[::1]:6379/14?password=REDACTED&db=14",
        ),
        ("redis://localhost:6379/15", "redis://localhost:6379/15"),
    ],
)
def test_report_redis_url_redacts_credentials(url: str, expected: str) -> None:
    from scripts import benchmark_bulk_throughput as script

    assert script._redact_redis_url(url) == expected
