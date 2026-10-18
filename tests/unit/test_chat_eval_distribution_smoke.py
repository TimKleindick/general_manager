"""Keep installed CLI bootstrap smoke separate from eval success."""

from __future__ import annotations

import subprocess

import pytest

from tests.packaging import smoke_chat_eval_distribution as smoke

EMPTY_ERROR = "__main__.py: error: No eval cases selected; the run was not evaluated."


@pytest.mark.parametrize(
    ("returncode", "stderr", "accepted"),
    [
        (2, f"usage: eval\n{EMPTY_ERROR}\n", True),
        (0, f"{EMPTY_ERROR}\n", False),
        (1, f"{EMPTY_ERROR}\n", False),
        (2, "error: Django settings are required.\n", False),
    ],
)
def test_installed_cli_bootstrap_requires_specific_empty_rejection(
    monkeypatch, returncode, stderr, accepted
):
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "foreign_project.settings")
    monkeypatch.setenv("PYTHONPATH", "/foreign/source")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, returncode, "", stderr)

    monkeypatch.setattr(smoke.subprocess, "run", run)
    if accepted:
        smoke._check_cli_bootstrap()
    else:
        with pytest.raises(AssertionError):
            smoke._check_cli_bootstrap()

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:3] == [smoke.sys.executable, "-m", "general_manager.chat.evals"]
    assert command[-2:] == ["--tier", "999"]
    assert "DJANGO_SETTINGS_MODULE" not in kwargs["env"]
    assert kwargs["env"]["PYTHONPATH"] == str(smoke.ROOT / "tests/packaging")
