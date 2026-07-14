from __future__ import annotations

import importlib.metadata as metadata
import importlib.resources as resources
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
import django.core.management
from django.core.management import get_commands

from scripts import validate_distribution


def _validator(name: str) -> Callable[..., None]:
    validator = getattr(validate_distribution, name, None)
    assert callable(validator), f"scripts.validate_distribution.{name} is missing"
    return cast(Callable[..., None], validator)


def test_installed_resources_accept_current_package_data() -> None:
    _validator("validate_installed_resources")()


def test_installed_resources_names_missing_datasets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = "chat/evals/datasets/multi_hop.yaml"

    class FakeResource:
        def __init__(self, relative_path: str) -> None:
            self.relative_path = relative_path

        def is_file(self) -> bool:
            return self.relative_path != missing

    class FakePackage:
        def joinpath(self, *descendants: str) -> FakeResource:
            return FakeResource("/".join(descendants))

    monkeypatch.setattr(resources, "files", lambda _: FakePackage())

    with pytest.raises(ValueError, match=r"multi_hop\.yaml"):
        _validator("validate_installed_resources")()


def test_installed_resources_rejects_missing_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_version(_: str) -> str:
        raise metadata.PackageNotFoundError("GeneralManager")

    monkeypatch.setattr(metadata, "version", missing_version)

    with pytest.raises(ValueError, match="distribution metadata"):
        _validator("validate_installed_resources")()


def test_installed_resources_rejects_empty_distribution_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(metadata, "version", lambda _: "  ")

    with pytest.raises(ValueError, match="empty version"):
        _validator("validate_installed_resources")()


def test_installed_migrations_rejects_preconfigured_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_migrate(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("migrate was called with preconfigured settings")

    monkeypatch.setattr(django.core.management, "call_command", unexpected_migrate)

    with pytest.raises(ValueError, match="already configured"):
        _validator("validate_installed_migrations")()


def test_installed_migrations_apply_with_isolated_minimal_settings(
    tmp_path: Path,
) -> None:
    script = Path(validate_distribution.__file__).resolve()
    source_root = script.parents[1] / "src"
    code = f"""
import runpy

namespace = runpy.run_path({str(script)!r})
validator = namespace.get("validate_installed_migrations")
assert callable(validator), "validate_installed_migrations is missing"
validator()

from django.db import connection

tables = set(connection.introspection.table_names())
assert "auth_user" in tables
assert "general_manager_workfloweventrecord" in tables
print("migration-ok")
"""
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment["PYTHONPATH"] = str(source_root)

    result = subprocess.run(  # noqa: S603 - fixed interpreter and test code.
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "migration-ok"


def test_installed_clis_discovers_command_and_uses_current_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    private_cwds: list[Path] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        private_cwd = Path(kwargs["cwd"])
        assert private_cwd.is_dir()
        private_cwds.append(private_cwd)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("PYTHONPATH", str(Path(tempfile.gettempdir()) / "hostile"))

    _validator("validate_installed_clis")()

    assert get_commands()["chat_cleanup"] == "general_manager"
    assert len(calls) == 1
    command, options = calls[0]
    assert command == [
        sys.executable,
        "-I",
        "-m",
        "general_manager.chat.evals",
        "--help",
    ]
    assert options["check"] is True
    assert private_cwds[0].parent == Path(tempfile.gettempdir())
    assert not private_cwds[0].exists()
    assert options["env"]["DJANGO_SETTINGS_MODULE"] == "django.conf.global_settings"
    assert "PYTHONPATH" not in options["env"]


def test_validate_installed_runs_all_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    for name in ("resources", "migrations", "clis"):
        monkeypatch.setattr(
            validate_distribution,
            f"validate_installed_{name}",
            lambda name=name: calls.append(name),
            raising=False,
        )

    _validator("validate_installed")()

    assert calls == ["resources", "migrations", "clis"]


def test_main_accepts_installed_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[None] = []
    monkeypatch.setattr(
        validate_distribution,
        "validate_installed",
        lambda: calls.append(None),
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", ["validate_distribution.py", "installed"])

    validate_distribution.main()

    assert calls == [None]
