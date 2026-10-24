"""Relocation keeps provenance truthful and historical entry points usable offline."""

import asyncio
from hashlib import sha256
import importlib
import os
from pathlib import Path
import subprocess
import sys


def test_source_manifest_hashes_actual_relocated_implementations():
    from experiments.gm_eval.cli import source_manifest

    root = Path(__file__).resolve().parents[2]
    manifest = source_manifest()
    for relative in (
        "assessment/evidence_rows.py",
        "assessment/presentation_normalization.py",
        "assessment/query_completeness.py",
        "execution/scripted.py",
        "execution/history_parity.py",
    ):
        path = f"experiments/gm_eval/{relative}"
        assert manifest[path] == sha256((root / path).read_bytes()).hexdigest()


def test_historical_calibration_alias_preserves_patched_function_globals(
    monkeypatch, tmp_path
):
    historical = importlib.import_module("experiments.siwc_eval.calibrate_answer_judge")
    archived = importlib.import_module(
        "experiments.siwc_eval.archive.calibrate_answer_judge"
    )
    assert historical is archived
    saved = []
    monkeypatch.setattr(
        historical, "save_report", lambda path, report: saved.append((path, report))
    )

    class Judge:
        requests = 0

        async def grade_turn(self, **kwargs):
            assert kwargs == {
                "questions": ["Synthetic calibration question"],
                "answer": "Synthetic calibration answer",
                "reference": {"synthetic": True},
            }
            self.requests += 1
            return {"verdict": "correct"}

    item = {
        "id": "synthetic/offline",
        "questions": ["Synthetic calibration question"],
        "answer": "Synthetic calibration answer",
        "reference": {"synthetic": True},
        "expected_verdict": "correct",
    }
    report = {"results": []}
    path = tmp_path / "not-written.json"
    asyncio.run(archived.run_calibration(Judge(), [item], report, path))
    assert saved == [(path, report)]
    assert report["completed"] is True
    assert report["results"][0]["matches_expected"] is True
    assert report["results"][0]["judge_requests"] == 1
    assert not path.exists()


def test_historical_and_archived_calibration_help_match_without_setup():
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    results = []
    for module in (
        "experiments.siwc_eval.calibrate_answer_judge",
        "experiments.siwc_eval.archive.calibrate_answer_judge",
    ):
        program = (
            "import runpy, socket, sys; "
            "socket.socket.connect = lambda *args: "
            "(_ for _ in ()).throw(AssertionError('external_network_forbidden')); "
            f"sys.argv = [{module!r}, '--help']; "
            "\ntry:\n"
            f"    runpy.run_module({module!r}, run_name='__main__', alter_sys=True)\n"
            "except SystemExit as error:\n"
            "    assert error.code == 0\n"
            "from django.conf import settings; assert not settings.configured\n"
        )
        result = subprocess.run(  # noqa: S603 -- fixed offline module/help only
            [sys.executable, "-c", program],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "--only" in result.stdout
        results.append(result.stdout.replace(module, "calibration"))
    assert results[0] == results[1]
