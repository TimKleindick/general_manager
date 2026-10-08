"""The experiment defaults to offline validation, with complete catalog coverage."""

import importlib.util
import json

import pytest


def test_cli_exists():
    assert importlib.util.find_spec("experiments.gm_eval.cli") is not None


def test_case_selection_rejects_typos_and_preserves_catalog_order():
    import pytest
    from experiments.gm_eval.cli import select_cases

    cases = [{"id": "E001", "managers": 5}, {"id": "E002", "managers": 10}]
    assert select_cases(cases, ["E002", "E001"], None) == cases
    assert select_cases(cases, [], 5) == cases[:1]
    with pytest.raises(ValueError, match="unknown case"):
        select_cases(cases, ["E999"], None)
    with pytest.raises(ValueError, match="no cases"):
        select_cases(cases, ["E001"], 10)


def test_catalog_report_contains_149_distinct_oracles_and_no_model_accuracy():
    from experiments.gm_eval.cli import catalog_report

    report = catalog_report()
    assert report["counts"]["cases"] == 121
    assert report["counts"]["turns"] == 149
    assert report["counts"]["languages"] == {"DE": 41, "FR": 40, "EN": 40}
    assert report["counts"]["scales"] == {
        "5": 20,
        "10": 23,
        "50": 24,
        "100": 26,
        "250": 28,
    }
    assert len(report["oracles"]) == 149
    assert report["model_performance_measured"] is False
    assert any(r["case_id"] == "E082" for r in report["reference_corrections"])


def test_default_cli_has_no_live_option():
    from experiments.gm_eval.cli import parser

    parsed = parser().parse_args([])
    assert parsed.command == "catalog"
    assert "--live" not in parser().format_help()


def test_nonzero_report_status_for_nested_transport_and_failed_parity():
    from experiments.gm_eval.cli import report_has_failures

    assert report_has_failures(
        {
            "harness": {
                "results": [
                    {
                        "status": "infrastructure_check",
                        "parity_passed": True,
                        "scheduler": {"status": "transport_failure"},
                    }
                ]
            }
        }
    )
    assert report_has_failures(
        {
            "harness": {
                "results": [{"status": "infrastructure_check", "parity_passed": False}]
            }
        }
    )
    assert not report_has_failures(
        {"fixtures": {"results": [{"status": "interface_capability_gap"}]}}
    )


def test_parity_rejects_selection_without_any_parity_case(tmp_path):
    import pytest
    from experiments.gm_eval.cli import main

    with pytest.raises(ValueError, match="no default parity cases"):
        main(["parity", "--scale", "5", "--output", str(tmp_path)])


def test_worker_timeout_is_preserved_as_harness_failure(monkeypatch, tmp_path):
    import subprocess
    from experiments.gm_eval.cli import _isolated_worker

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="offline worker", timeout=300)

    monkeypatch.setattr(subprocess, "run", timeout)
    result = _isolated_worker(
        {"command": "harness", "case_id": "E001"}, tmp_path / "result.json"
    )
    assert result["status"] == "harness_failure"
    assert result["worker_timeout_seconds"] == 300
    assert result["case_ids"] == ["E001"]


def test_source_manifest_covers_the_executed_planner_contract():
    from experiments.gm_eval.cli import source_manifest

    manifest = source_manifest()
    required = {
        "src/general_manager/chat/planned/contract.py",
        "src/general_manager/chat/planned/models.py",
        "src/general_manager/chat/planned/planner.py",
        "src/general_manager/chat/planned/validation.py",
    }
    assert required <= manifest.keys()
    assert all(len(manifest[name]) == 64 for name in required)


def test_source_manifest_covers_native_read_contract_and_transports():
    from experiments.gm_eval.cli import source_manifest

    manifest = source_manifest()
    for name in (
        "graphql_contract.py",
        "schema_index.py",
        "tool_metadata.py",
        "system_prompt.py",
        "views.py",
    ):
        assert f"src/general_manager/chat/{name}" in manifest

    assert "src/general_manager/api/graphql_search.py" in manifest


@pytest.mark.parametrize("command", ["offline", "harness"])
@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        ("infrastructure_check", 0),
        ("interface_capability_gap", 0),
        ("model_task_failure", 1),
        ("fixture_invalid", 1),
        ("harness_failure", 1),
        ("transport_failure", 1),
        ("judge_failure", 1),
        ("budget_exhausted", 1),
    ],
)
def test_cli_persists_schema_status_metadata_and_preserves_outcome_exit(
    command, status, exit_code, monkeypatch, tmp_path
):
    from experiments.gm_eval import cli

    metadata = {
        "types": {
            "Project": {
                "fields": {
                    "status": {
                        "type": "String",
                        "description": "Independent business state",
                    }
                }
            }
        }
    }
    outcome = {"results": [{"status": status, "schema": metadata}]}
    monkeypatch.setattr(cli, "fixture_report", lambda *_args: outcome)
    monkeypatch.setattr(cli, "harness_report", lambda *_args: outcome)
    original = cli.report_has_failures
    checked_after_persistence = []

    def inspect_saved_report(value):
        assert (tmp_path / "report.json").is_file()
        checked_after_persistence.append(True)
        return original(value)

    monkeypatch.setattr(cli, "report_has_failures", inspect_saved_report)
    assert cli.main([command, "--case", "E001", "--output", str(tmp_path)]) == exit_code
    saved = json.loads((tmp_path / "report.json").read_text())
    assert checked_after_persistence
    assert saved["harness"] == outcome
    assert saved["harness"]["results"][0]["status"] == status
    assert saved["model_performance_measured"] is False
    assert saved["source_stable_during_run"] is True


@pytest.mark.parametrize(
    "failure",
    [
        "model_task_failure",
        "harness_failure",
        "transport_failure",
        "judge_failure",
        "fixture_invalid",
        "budget_exhausted",
    ],
)
def test_structured_schema_status_does_not_hide_a_later_real_failure(failure):
    from experiments.gm_eval.cli import report_has_failures

    assert report_has_failures(
        {
            "schema": {"fields": {"status": {"type": "String"}}},
            "results": [{"status": failure}],
        }
    )


@pytest.mark.parametrize(
    "metadata", [{"type": "String"}, ["model_task_failure"], None, False, 17]
)
def test_non_string_status_metadata_is_not_coerced_into_an_outcome(metadata):
    from experiments.gm_eval.cli import report_has_failures

    assert not report_has_failures({"status": metadata})
    assert report_has_failures({"status": metadata, "parity_passed": False})
