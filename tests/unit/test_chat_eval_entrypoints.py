"""Source entrypoints share configuration and run concrete offline test targets."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from general_manager.chat.evals.runner import EvalCase, EvalResult


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("remap", [False, True])
def test_source_helper_combines_override_and_remapping(monkeypatch, override, remap):
    from scripts import run_chat_evals as helper

    monkeypatch.setattr(helper, "setup_test_schema", lambda: None)
    monkeypatch.setattr(helper, "get_tool_definitions", lambda: [])
    monkeypatch.setattr(helper, "build_system_prompt", lambda: "system")
    monkeypatch.setattr(
        helper.OllamaProvider,
        "_provider_config",
        staticmethod(
            lambda _configured=None: {
                "model": "default",
                "base_url": "http://localhost:11434",
            }
        ),
    )
    resolved = (
        "http://host.docker.internal:11434" if remap else "http://localhost:11434"
    )
    monkeypatch.setattr(
        helper, "_resolve_ollama_base_url", lambda _url: (resolved, remap)
    )
    observed = []

    def execute(provider, *args, **kwargs):
        observed.append(dict(provider.provider_config))
        return [EvalResult(EvalCase("helper", "", [{"user": "x"}], {}))]

    monkeypatch.setattr(helper, "run_eval_suite_sync", execute)
    monkeypatch.setattr(
        sys, "argv", ["run_chat_evals.py", *(["--model", "chosen"] if override else [])]
    )
    helper.main()
    assert observed == [
        {"model": "chosen" if override else "default", "base_url": resolved}
    ]


def test_readiness_passes_real_test_paths_and_propagates_failure(monkeypatch):
    from scripts import run_chat_readiness_loop as readiness

    observed = []

    def execute(argv, *, cwd, check):
        observed.append((argv, Path(cwd), check))
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(readiness.subprocess, "run", execute)
    monkeypatch.setattr(sys, "argv", ["run_chat_readiness_loop.py"])
    with pytest.raises(SystemExit) as stopped:
        readiness.main()
    assert stopped.value.code == 1
    assert len(observed) == 1
    argv, cwd, check = observed[0]
    assert argv[:3] == [sys.executable, "-m", "pytest"]
    assert argv[3:]
    assert not any("*" in argument for argument in argv[3:])
    assert all((cwd / argument).is_file() for argument in argv[3:])
    assert check is False


def test_readiness_does_not_expand_empty_test_targets_into_full_suite(
    monkeypatch, tmp_path
):
    from scripts import run_chat_readiness_loop as readiness

    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_chat_readiness_loop.py"])

    def unexpected(*args, **kwargs):
        pytest.fail("An empty chat-test selection must not launch pytest.")

    monkeypatch.setattr(readiness.subprocess, "run", unexpected)
    with pytest.raises(SystemExit, match="No chat test files"):
        readiness.main()


def test_empty_readiness_writes_explicit_failure_without_tests_or_provider(
    monkeypatch, tmp_path
):
    import json
    from scripts import run_chat_readiness_loop as readiness

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_chat_readiness_loop.py",
            "--tier",
            "999999",
            "--output-dir",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(readiness, "setup_test_schema", lambda: None)
    monkeypatch.setattr(readiness, "get_tool_definitions", lambda: [])
    monkeypatch.setattr(readiness, "build_system_prompt", lambda: "system")
    monkeypatch.setattr(readiness, "_resolve_ollama_base_url", lambda url: (url, False))

    def unexpected(*args, **kwargs):
        pytest.fail("An empty eval must not start tests or a provider.")

    monkeypatch.setattr(readiness.subprocess, "run", unexpected)
    monkeypatch.setattr(readiness.OllamaProvider, "__init__", unexpected)
    with pytest.raises(SystemExit) as stopped:
        readiness.main()
    assert stopped.value.code == 1
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["run_status"] == "empty"
    assert summary["total"] == summary["passed"] == 0
    assert summary["selection_manifest"]["cases"] == []
