"""Exercise the README onboarding flow against the active Python environment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent


SETTINGS_SNIPPET = dedent(
    """
    INSTALLED_APPS += [
        "general_manager",
        "projects.apps.ProjectsConfig",
    ]

    AUTOCREATE_GRAPHQL = True
    GRAPHQL_URL = "graphql/"
    ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
    """
).strip()

MANAGER_SNIPPET = dedent(
    """
    from typing import ClassVar

    from django.db.models import CharField

    from general_manager import GeneralManager
    from general_manager.interface import DatabaseInterface
    from general_manager.permission import AdditiveManagerPermission


    class Project(GeneralManager):
        name: str

        class Interface(DatabaseInterface):
            name = CharField(max_length=100)

        class Permission(AdditiveManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]
            __create__: ClassVar[list[str]] = ["isAuthenticated"]
            __update__: ClassVar[list[str]] = ["isAuthenticated"]
            __delete__: ClassVar[list[str]] = ["isAuthenticated"]
    """
).strip()

GRAPHQL_QUERY = "query { projectList { items { name } } }"
EXPECTED_RESPONSE = {"data": {"projectList": {"items": [{"name": "Apollo"}]}}}
EXPECTED_RESPONSE_TEXT = '{"data":{"projectList":{"items":[{"name":"Apollo"}]}}}'

ONBOARDING_COMMANDS = (
    "python -m venv .venv",
    "python -m pip install GeneralManager",
    "django-admin startproject gm_demo .",
    "python manage.py startapp projects",
    "python manage.py makemigrations projects",
    "python manage.py migrate",
    'Project.Factory.create(name="Apollo")',
    "python manage.py runserver",
    "curl --get",
    "--data-urlencode",
)

DOCUMENTED_PATHS = (Path("README.md"), Path("docs/quickstart.md"))
RESPONSE_MARKER = "ONBOARDING_RESPONSE="
USAGE = "usage: smoke_readme_onboarding.py REPOSITORY_ROOT"


def _run(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment.pop("PYTHONPATH", None)
    command = [sys.executable, *arguments]
    result = subprocess.run(  # noqa: S603 - fixed interpreter, no shell
        command,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        msg = (
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        raise AssertionError(msg)
    return result


def _verify_documentation(repository_root: Path) -> None:
    required_fragments = (
        *ONBOARDING_COMMANDS,
        SETTINGS_SNIPPET,
        MANAGER_SNIPPET,
        GRAPHQL_QUERY,
        EXPECTED_RESPONSE_TEXT,
    )
    for relative_path in DOCUMENTED_PATHS:
        document = (repository_root / relative_path).read_text(encoding="utf-8")
        missing = [
            fragment for fragment in required_fragments if fragment not in document
        ]
        if missing:
            rendered = "\n- ".join(missing)
            msg = f"{relative_path} is missing onboarding fragments:\n- {rendered}"
            raise AssertionError(msg)


def _query_script() -> str:
    return dedent(
        f"""
        from django.test import Client

        response = Client().get("/graphql/", {{"query": {GRAPHQL_QUERY!r}}})
        if response.status_code != 200:
            raise SystemExit(
                f"GraphQL returned {{response.status_code}}: "
                f"{{response.content.decode('utf-8')}}"
            )
        print({RESPONSE_MARKER!r} + response.content.decode("utf-8"))
        """
    )


def _response_from_output(output: str) -> dict[str, object]:
    response_lines = [
        line.removeprefix(RESPONSE_MARKER)
        for line in output.splitlines()
        if line.startswith(RESPONSE_MARKER)
    ]
    if len(response_lines) != 1:
        msg = f"Expected one GraphQL response marker, got: {output}"
        raise AssertionError(msg)
    payload = json.loads(response_lines[0])
    if not isinstance(payload, dict):
        msg = f"Expected a JSON object, got: {payload!r}"
        raise TypeError(msg)
    return payload


def main(arguments: list[str]) -> None:
    if len(arguments) != 1:
        raise SystemExit(USAGE)

    repository_root = Path(arguments[0]).resolve()
    _verify_documentation(repository_root)

    with tempfile.TemporaryDirectory(prefix="general-manager-readme-") as temp_dir:
        project_root = Path(temp_dir)
        _run(project_root, "-m", "django", "startproject", "gm_demo", ".")
        _run(project_root, "manage.py", "startapp", "projects")

        settings_path = project_root / "gm_demo/settings.py"
        settings_path.write_text(
            settings_path.read_text(encoding="utf-8")
            + "\n\n"
            + SETTINGS_SNIPPET
            + "\n",
            encoding="utf-8",
        )
        (project_root / "projects/managers.py").write_text(
            MANAGER_SNIPPET + "\n",
            encoding="utf-8",
        )

        _run(
            project_root,
            "manage.py",
            "makemigrations",
            "projects",
            "--noinput",
        )
        _run(project_root, "manage.py", "migrate", "--noinput")
        _run(
            project_root,
            "manage.py",
            "shell",
            "-c",
            (
                "from projects.managers import Project; "
                'Project.Factory.create(name="Apollo")'
            ),
        )
        query_result = _run(
            project_root,
            "manage.py",
            "shell",
            "-c",
            _query_script(),
        )

    actual_response = _response_from_output(query_result.stdout)
    if actual_response != EXPECTED_RESPONSE:
        msg = f"Unexpected GraphQL response: {actual_response!r}"
        raise AssertionError(msg)
    print("README onboarding smoke passed")


if __name__ == "__main__":
    main(sys.argv[1:])
