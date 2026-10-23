"""Guard the example image's COPY boundary and build-context exclusions.

Run directly with Python; Docker-backed checks use synthetic files and scratch
images, so they need no registry access or production credentials.
"""

from __future__ import annotations

import io
import json
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import uuid
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = "example_project/outer_rim_logistics"
DOCKERFILE = ROOT / EXAMPLE / "Dockerfile"
DOCKER = shutil.which("docker")
SENTINEL = b"synthetic-secret-must-never-enter-an-image"
REQUIRED = (
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "requirements/base.txt",
    "requirements/production.txt",
    "src/general_manager/__init__.py",
    "src/general_manager/py.typed",
    "src/general_manager/chat/evals/datasets/basic_queries.yaml",
    f"{EXAMPLE}/pyproject.toml",
    f"{EXAMPLE}/README.md",
    f"{EXAMPLE}/__init__.py",
    f"{EXAMPLE}/manage.py",
    *(
        f"{EXAMPLE}/{app}/__init__.py"
        for app in ("orl", "crew", "maintenance", "mission", "supply")
    ),
    f"{EXAMPLE}/docker/entrypoint.sh",
    f"{EXAMPLE}/docker/entrypoint-celery.sh",
)
SENSITIVE = (
    ".env",
    ".env.production",
    ".git/config",
    ".venv/lib/local.py",
    f"{EXAMPLE}/.env",
    f"{EXAMPLE}/.env.production",
    *(
        f"{EXAMPLE}/docker/secrets/{name}.txt"
        for name in (
            "django_secret_key",
            "postgres_password",
            "meilisearch_api_key",
            "grafana_admin_password",
        )
    ),
    f"{EXAMPLE}/docker/certs/tls.key",
    f"{EXAMPLE}/docker/certs/tls.crt",
    f"{EXAMPLE}/docker/htpasswd/observability",
    f"{EXAMPLE}/logs/app.log",
    f"{EXAMPLE}/backups/database.sql",
)


def copy_instructions() -> list[list[str]]:
    instructions = []
    for line in DOCKERFILE.read_text().replace("\\\n", " ").splitlines():
        words = shlex.split(line, comments=True)
        if words and words[0].upper() in {"ADD", "COPY"}:
            instructions.append(words)
    return instructions


def copied(path: str) -> bool:
    candidate = PurePosixPath(path)
    return any(
        candidate.is_relative_to(PurePosixPath(source))
        for words in copy_instructions()
        for source in words[1:-1]
    )


class DockerCopyBoundaryTests(unittest.TestCase):
    def test_sensitive_files_are_outside_copy_sources(self) -> None:
        for path in SENSITIVE:
            with self.subTest(path=path):
                self.assertFalse(copied(path), f"COPY includes sensitive path: {path}")

    def test_runtime_files_are_inside_copy_sources(self) -> None:
        for path in REQUIRED:
            with self.subTest(path=path):
                self.assertTrue(copied(path), f"COPY omits runtime path: {path}")


@unittest.skipUnless(DOCKER, "Docker is required to inspect actual image layers")
class DockerIgnoreLayerTests(unittest.TestCase):
    def check_context(self, context_prefix: str) -> None:
        assert DOCKER is not None
        tag = f"gm-context-regression:{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "context"
            fixture.mkdir()
            required = [
                p.removeprefix(context_prefix)
                for p in REQUIRED
                if p.startswith(context_prefix)
            ]
            sensitive = [
                p.removeprefix(context_prefix)
                for p in SENSITIVE
                if p.startswith(context_prefix)
            ]
            for name in required + sensitive:
                target = fixture / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(SENTINEL if name in sensitive else b"runtime-file")
            shutil.copyfile(
                ROOT / context_prefix / ".dockerignore", fixture / ".dockerignore"
            )
            archive_path = Path(directory) / "image.tar"
            try:
                subprocess.run(  # noqa: S603
                    [DOCKER, "build", "--no-cache", "-t", tag, "-f", "-", str(fixture)],
                    input=b"FROM scratch\nCOPY . /context/\n",
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                subprocess.run(  # noqa: S603
                    [DOCKER, "image", "save", "-o", str(archive_path), tag],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
                names: set[str] = set()
                with tarfile.open(archive_path) as archive:
                    manifest_file = archive.extractfile("manifest.json")
                    assert manifest_file is not None
                    manifest = json.load(manifest_file)
                    layers = [name for image in manifest for name in image["Layers"]]
                    self.assertTrue(layers, "Expected at least one image layer")
                    for name in layers:
                        layer_file = archive.extractfile(name)
                        assert layer_file is not None
                        layer_bytes = layer_file.read()
                        self.assertNotIn(SENTINEL, layer_bytes, name)
                        with tarfile.open(fileobj=io.BytesIO(layer_bytes)) as layer:
                            names.update(
                                member.name.removeprefix("./") for member in layer
                            )
                for name in sensitive:
                    self.assertNotIn(f"context/{name}", names)
                for name in required:
                    self.assertIn(f"context/{name}", names)
            finally:
                subprocess.run(  # noqa: S603
                    [DOCKER, "image", "rm", "-f", tag],
                    check=False,
                    capture_output=True,
                    timeout=30,
                )

    def test_repository_root_context_excludes_runtime_secrets(self) -> None:
        self.check_context("")

    def test_example_context_excludes_runtime_secrets(self) -> None:
        self.check_context(f"{EXAMPLE}/")


if __name__ == "__main__":
    unittest.main()
