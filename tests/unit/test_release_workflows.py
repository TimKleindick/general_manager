"""Regression tests for release-related GitHub Actions workflows."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
import tomllib

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


def step_by_id(job: Mapping[str, Any], step_id: str) -> dict[str, Any]:
    """Return the step with a specific workflow identifier."""
    steps = cast(list[dict[str, Any]], job["steps"])
    return next(step for step in steps if step.get("id") == step_id)


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

    assert job["name"] == "lint-and-mypy"
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


def test_docs_workflow_builds_on_main_or_dispatch_and_deploys_push_only() -> None:
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
    deploy_job = workflow["jobs"]["deploy"]
    assert deploy_job["needs"] == "build"
    assert deploy_job["if"] == "${{ github.event_name == 'push' }}"
    assert "mkdocs build --strict" in run_commands(workflow["jobs"]["build"])
    upload_step = action_step(
        workflow["jobs"]["build"], "actions/upload-pages-artifact@v3"
    )
    assert upload_step["if"] == "${{ github.event_name == 'push' }}"
    action_step(deploy_job, "actions/deploy-pages@v4")


def test_publish_workflow_runs_every_exact_sha_through_quality_and_release() -> None:
    workflow = load_workflow("publish.yml")

    assert workflow["on"] == {"push": {"branches": ["main"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert "concurrency" not in workflow
    assert set(workflow["jobs"]) == {"quality", "artifact", "release"}
    assert workflow["jobs"]["quality"] == {"uses": "./.github/workflows/quality.yml"}
    assert workflow["jobs"]["artifact"]["needs"] == "quality"
    assert workflow["jobs"]["release"]["needs"] == "artifact"


def test_publish_artifact_job_builds_once_at_the_exact_commit() -> None:
    workflow = load_workflow("publish.yml")
    job = workflow["jobs"]["artifact"]

    assert job["permissions"] == {"contents": "read"}
    assert job["outputs"] == {
        "released": "${{ steps.prepare.outputs.released || 'false' }}",
        "version": "${{ steps.prepare.outputs.version }}",
        "tag": "${{ steps.prepare.outputs.tag }}",
    }
    assert action_step(job, "actions/checkout@v4")["with"] == {
        "ref": "${{ github.sha }}",
        "fetch-depth": "0",
        "persist-credentials": "false",
    }
    assert action_step(job, "actions/setup-python@v5")["with"] == {
        "python-version": "3.12"
    }

    commands = run_commands(job)
    assert "python -m pip install --upgrade pip" in commands
    assert (
        "python -m pip install python-semantic-release==10.6.1 build twine==6.0.1"
    ) in commands
    prepare = step_by_id(job, "prepare")
    assert prepare["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    prepare_command = str(prepare["run"])
    assert "semantic-release version" in prepare_command
    for flag in (
        "--no-commit",
        "--no-tag",
        "--no-push",
        "--no-vcs-release",
    ):
        assert flag in prepare_command
    assert "python -m build" not in commands


def test_publish_artifact_job_validates_before_uploading_sha_keyed_files() -> None:
    job = load_workflow("publish.yml")["jobs"]["artifact"]
    steps = cast(list[dict[str, Any]], job["steps"])
    released_condition = "${{ steps.prepare.outputs.released == 'true' }}"
    conditional_steps = [step for step in steps if step.get("if") == released_condition]

    assert len(conditional_steps) == 5
    commands = [str(step.get("run", "")) for step in conditional_steps]
    venv_python = "/tmp/general-manager-release-venv/bin/python"  # noqa: S108
    version_binding = conditional_steps[0]
    assert version_binding["env"] == {
        "EXPECTED_VERSION": "${{ steps.prepare.outputs.version }}"
    }
    assert "import os" in commands[0]
    assert "import tomllib" in commands[0]
    assert 'configuration["project"]["version"]' in commands[0]
    assert 'os.environ["EXPECTED_VERSION"]' in commands[0]
    assert commands[1].strip() == "twine check dist/*"
    archive_validation = conditional_steps[2]
    assert archive_validation["env"] == {
        "EXPECTED_VERSION": "${{ steps.prepare.outputs.version }}"
    }
    assert commands[2].strip() == (
        'python scripts/validate_distribution.py archives dist "$EXPECTED_VERSION"'
    )
    assert "python -m venv /tmp/general-manager-release-venv" in commands[3]
    assert (
        f"{venv_python} -m pip install "
        "\"$(find dist -maxdepth 1 -type f -name '*.whl' -print -quit)\""
    ) in commands[3]
    assert "cd /tmp" in commands[3]
    assert (
        f'{venv_python} "$GITHUB_WORKSPACE/scripts/validate_distribution.py" installed'
    ) in commands[3]

    upload = conditional_steps[4]
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"] == {
        "name": "validated-distributions-${{ github.sha }}",
        "path": "dist/*",
        "retention-days": "90",
        "if-no-files-found": "error",
    }


def test_publish_release_job_mutates_only_after_downloading_validated_files() -> None:
    workflow = load_workflow("publish.yml")
    job = workflow["jobs"]["release"]

    assert job["if"] == "${{ needs.artifact.outputs.released == 'true' }}"
    assert job["permissions"] == {"contents": "write"}
    checkout = action_step(job, "actions/checkout@v4")
    assert checkout["with"] == {
        "repository": "TimKleindick/general_manager",
        "ssh-key": "${{ secrets.SSH_DEPLOY_KEY }}",
        "ssh-strict": "true",
        "persist-credentials": "true",
        "fetch-depth": "0",
        "ref": "${{ github.sha }}",
    }

    commands = run_commands(job)
    assert 'git switch -C main "$GITHUB_SHA"' in commands
    assert (
        "git remote set-url origin git@github.com:TimKleindick/general_manager.git"
    ) in commands
    assert "git branch --set-upstream-to=origin/main main" in commands
    assert 'git config --global user.name "github-actions"' in commands
    assert (
        'git config --global user.email "actions@users.noreply.github.com"' in commands
    )
    assert action_step(job, "actions/setup-python@v5")["with"] == {
        "python-version": "3.12"
    }
    assert (
        "python -m pip install python-semantic-release==10.6.1 twine==6.0.1" in commands
    )
    assert "python -m build" not in commands

    download = action_step(job, "actions/download-artifact@v4")
    assert download["with"] == {
        "name": "validated-distributions-${{ github.sha }}",
        "path": "validated-dist",
    }
    final_release = step_by_id(job, "release")
    assert str(final_release["run"]).strip() == (
        "semantic-release version --skip-build"
    )
    assert final_release["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    assert final_release["continue-on-error"] == "true"

    verify = step_by_id(job, "verify_release")
    assert verify["env"] == {
        "EXPECTED_VERSION": "${{ needs.artifact.outputs.version }}",
        "EXPECTED_TAG": "${{ needs.artifact.outputs.tag }}",
        "ACTUAL_VERSION": "${{ steps.release.outputs.version }}",
        "ACTUAL_TAG": "${{ steps.release.outputs.tag }}",
        "PSR_RELEASED": "${{ steps.release.outputs.released }}",
        "PSR_COMMIT_SHA": "${{ steps.release.outputs.commit_sha }}",
        "GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
    }
    verify_command = str(verify["run"])
    assert (
        'git fetch --force origin "refs/tags/$EXPECTED_TAG:'
        'refs/tags/$EXPECTED_TAG"' in verify_command
    )
    assert 'TAG_COMMIT="$(git rev-parse "$EXPECTED_TAG^{commit}")"' in verify_command
    assert 'test "$RELEASE_PARENTS" = "$GITHUB_SHA"' in verify_command
    assert "git diff-tree --no-commit-id --name-only -r" in verify_command
    assert (
        "EXPECTED_FILES=\"$(printf '%s\\n' CHANGELOG.md pyproject.toml | sort)\""
        in verify_command
    )
    assert (
        'RELEASE_FILES="$(git diff-tree --no-commit-id --name-only -r '
        '"$RELEASE_COMMIT" | sort)"' in verify_command
    )
    assert 'test "$RELEASE_FILES" = "$EXPECTED_FILES"' in verify_command
    assert "tomllib.loads" in verify_command
    assert 'test "$TAG_VERSION" = "$EXPECTED_VERSION"' in verify_command
    assert 'test "$PSR_COMMIT_SHA" = "$TAG_COMMIT"' in verify_command
    assert 'if [ "$PSR_RELEASED" = "true" ]' in verify_command
    assert 'test -n "$PSR_COMMIT_SHA"' in verify_command
    released_commit_check = """if [ "$PSR_RELEASED" = "true" ]; then
  test -n "$PSR_COMMIT_SHA"
  test "$PSR_COMMIT_SHA" = "$TAG_COMMIT"
fi"""
    assert released_commit_check in verify_command
    assert 'if [ -n "$PSR_COMMIT_SHA" ]' not in verify_command
    assert 'gh release view "$EXPECTED_TAG"' in verify_command
    assert "gh release create" in verify_command
    assert "--verify-tag" in verify_command
    assert "--generate-notes" in verify_command
    assert 'test "$EXPECTED_VERSION" = "$ACTUAL_VERSION"' in verify_command
    assert 'test "$EXPECTED_TAG" = "$ACTUAL_TAG"' in verify_command

    steps = cast(list[dict[str, Any]], job["steps"])
    pre_upload = step_by_id(job, "verify_pypi_before")
    publish = step_by_id(job, "publish_pypi")
    post_upload = step_by_id(job, "verify_pypi_after")
    assert steps.index(final_release) < steps.index(verify)
    assert steps.index(verify) < steps.index(pre_upload)
    assert steps.index(pre_upload) < steps.index(publish)
    assert steps.index(publish) < steps.index(post_upload)

    assert pre_upload["env"] == {
        "EXPECTED_VERSION": "${{ needs.artifact.outputs.version }}"
    }
    assert str(pre_upload["run"]).strip() == (
        "python scripts/verify_pypi_artifacts.py "
        'GeneralManager "$EXPECTED_VERSION" validated-dist'
    )
    assert str(publish["run"]).strip() == (
        "twine upload --non-interactive --skip-existing validated-dist/*"
    )
    assert post_upload["env"] == pre_upload["env"]
    assert str(post_upload["run"]).strip() == (
        "python scripts/verify_pypi_artifacts.py "
        'GeneralManager "$EXPECTED_VERSION" validated-dist --require-all'
    )
    assert "twine upload dist/*" not in commands
    assert "semantic-release changelog" not in commands
    assert "git describe --tags" not in commands
    assert publish["env"] == {
        "TWINE_USERNAME": "__token__",
        "TWINE_PASSWORD": "${{ secrets.PYPI_API_TOKEN }}",
    }

    write_jobs = {
        name
        for name, candidate in workflow["jobs"].items()
        if candidate.get("permissions", {}).get("contents") == "write"
    }
    assert write_jobs == {"release"}


def test_publish_release_recovers_a_verified_branch_push_when_tag_is_missing() -> None:
    job = load_workflow("publish.yml")["jobs"]["release"]
    verify_command = str(step_by_id(job, "verify_release")["run"])

    fetch_main = 'git fetch origin "+refs/heads/main:refs/remotes/origin/main"'
    inspect_remote_tag = (
        'git ls-remote --exit-code --refs origin "refs/tags/$EXPECTED_TAG"'
    )
    assert fetch_main in verify_command
    assert inspect_remote_tag in verify_command
    assert verify_command.index(fetch_main) < verify_command.index(inspect_remote_tag)
    assert 'case "$REMOTE_TAG_STATUS" in' in verify_command
    assert "2)" in verify_command
    assert "*)" in verify_command
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in verify_command
    assert (
        'git rev-list --first-parent --reverse "$GITHUB_SHA..origin/main"'
        in verify_command
    )

    validate_candidate = 'validate_release_commit "$CANDIDATE_COMMIT"'
    create_tag = 'git tag --force "$EXPECTED_TAG" "$CANDIDATE_COMMIT"'
    push_tag = 'git push origin "refs/tags/$EXPECTED_TAG"'
    refetch_tag = (
        'git fetch --force origin "refs/tags/$EXPECTED_TAG:refs/tags/$EXPECTED_TAG"'
    )
    validate_remote_tag = 'validate_release_commit "$TAG_COMMIT"'
    assert validate_candidate in verify_command
    assert create_tag in verify_command
    assert push_tag in verify_command
    assert refetch_tag in verify_command
    assert validate_remote_tag in verify_command
    assert verify_command.index(validate_candidate) < verify_command.index(create_tag)
    assert verify_command.index(create_tag) < verify_command.index(push_tag)
    assert verify_command.index(push_tag) < verify_command.rindex(refetch_tag)
    assert verify_command.rindex(refetch_tag) < verify_command.index(
        validate_remote_tag
    )
    assert 'TAG_PUSH_STATUS="$?"' in verify_command
    assert 'test "$RELEASE_PARENTS" = "$GITHUB_SHA"' in verify_command
    assert 'test "$RELEASE_FILES" = "$EXPECTED_FILES"' in verify_command
    assert 'test "$TAG_VERSION" = "$EXPECTED_VERSION"' in verify_command


def test_semantic_release_keeps_build_as_its_only_publish_artifact_path() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text())
    semantic_release = configuration["tool"]["semantic_release"]

    assert semantic_release["build_command"] == "python -m build"
    assert "upload_to_pypi" not in semantic_release
    assert "upload_to_release" not in semantic_release
    assert "upload_to_repository" not in semantic_release

    publish_workflow = (WORKFLOWS / "publish.yml").read_text()
    assert "python -m build" not in publish_workflow
