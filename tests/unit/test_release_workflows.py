"""Regression tests for release-related GitHub Actions workflows."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def load_workflow(name: str) -> dict[str, Any]:
    """Load a workflow without YAML 1.1 coercing the ``on`` key to a boolean."""
    path = WORKFLOWS / name
    assert path.exists(), f"Expected workflow to exist: {path}"
    loaded = yaml.load(
        path.read_text(),
        Loader=yaml.BaseLoader,  # noqa: S506 - Preserve the workflow's `on` key.
    )
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


def run_commands(job: Mapping[str, Any]) -> str:
    """Join shell commands from a job's run steps for semantic assertions."""
    steps = cast(list[dict[str, Any]], job["steps"])
    return "\n".join(str(step["run"]) for step in steps if "run" in step)


def action_step(job: Mapping[str, Any], action: str) -> dict[str, Any]:
    """Return the step that invokes a specific action."""
    steps = cast(list[dict[str, Any]], job["steps"])
    return next(step for step in steps if step.get("uses") == action)


def test_quality_workflow_has_reusable_least_privilege_triggers() -> None:
    workflow = load_workflow("quality.yml")

    assert set(workflow["on"]) == {
        "pull_request",
        "workflow_call",
        "workflow_dispatch",
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"test", "lint-and-mypy", "docs"}


def test_quality_test_job_preserves_supported_matrix_and_test_services() -> None:
    job = load_workflow("quality.yml")["jobs"]["test"]

    assert job["name"] == "🧪 Run Tests"
    assert job["strategy"]["matrix"] == {"python-version": ["3.12", "3.13", "3.14"]}
    assert job["services"]["meilisearch"] == {
        "image": "getmeili/meilisearch:v1.30.0",
        "ports": ["7700:7700"],
        "env": {"MEILI_NO_ANALYTICS": "true"},
        "options": (
            '--health-cmd="wget -qO- http://127.0.0.1:7700/health" '
            "--health-interval=5s --health-timeout=5s --health-retries=10"
        ),
    }

    commands = run_commands(job)
    assert 'pip install -e ".[file-upload-image]"' in commands
    assert "pip install pytest pytest-django pytest-cov meilisearch==0.40.0" in commands
    assert (
        "python3 -m pytest --cov=general_manager --cov-report=xml:coverage.xml"
        in commands
    )
    assert action_step(job, "actions/setup-python@v5")["with"] == {
        "python-version": "${{ matrix.python-version }}"
    }
    assert action_step(
        job,
        "codecov/codecov-action@e79a6962e0d4c0c17b229090214935d2e33f8354",
    )["with"] == {
        "files": "coverage.xml",
        "flags": "unittests",
        "version": "v11.2.7",
        "fail_ci_if_error": "false",
    }


def test_quality_lint_job_runs_all_static_quality_gates() -> None:
    job = load_workflow("quality.yml")["jobs"]["lint-and-mypy"]
    commands = run_commands(job)

    assert job["name"] == "Lint and Type Check"
    assert "ruff check --config pyproject.toml src tests scripts" in commands
    assert "ruff format --config pyproject.toml --check ." in commands
    assert "mypy --strict" in commands
    assert "pre-commit run --all-files" in commands


def test_quality_docs_job_builds_strictly_with_existing_toolchain() -> None:
    job = load_workflow("quality.yml")["jobs"]["docs"]

    assert job["name"] == "Build MkDocs"
    assert action_step(job, "actions/checkout@v4")["with"] == {
        "fetch-depth": "0",
        "persist-credentials": "false",
    }
    assert action_step(job, "actions/setup-python@v5")["with"] == {
        "python-version": "3.12"
    }
    assert action_step(job, "actions/cache@v4")["with"] == {
        "path": "~/.cache/pip",
        "key": (
            "${{ runner.os }}-pip-${{ "
            "hashFiles('pyproject.toml', 'mkdocs.yml', 'requirements/*.txt') }}"
        ),
        "restore-keys": "${{ runner.os }}-pip-\n",
    }
    commands = run_commands(job)
    assert "pip install -e ." in commands
    assert (
        "pip install mkdocs-material mkdocstrings[python] pymdown-extensions"
        in commands
    )
    assert "pip install -r requirements/development.txt" in commands
    assert "mkdocs build --strict" in commands


def test_legacy_test_and_lint_workflows_are_removed() -> None:
    assert not (WORKFLOWS / "test.yml").exists()
    assert not (WORKFLOWS / "lint.yml").exists()


def test_docs_workflow_only_deploys_from_main_or_manual_runs() -> None:
    workflow = load_workflow("docs.yml")

    assert workflow["on"] == {
        "push": {"branches": ["main"]},
        "workflow_dispatch": "",
    }
    assert workflow["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert set(workflow["jobs"]) == {"build", "deploy"}
    assert workflow["jobs"]["deploy"]["needs"] == "build"
    assert "mkdocs build --strict" in run_commands(workflow["jobs"]["build"])
    action_step(workflow["jobs"]["build"], "actions/upload-pages-artifact@v3")
    action_step(workflow["jobs"]["deploy"], "actions/deploy-pages@v4")
