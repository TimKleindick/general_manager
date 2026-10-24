"""Execution and comparison use the same immutable actual eval cohort."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import json

import pytest

from general_manager.chat.evals import runner
from general_manager.chat.evals.baseline import compare_to_baseline, load_summary
from general_manager.chat.evals.fingerprints import build_run_fingerprint, stable_hash
from general_manager.chat.evals.readiness import build_summary, readiness_exit_code


@pytest.fixture
def dataset_files(tmp_path, monkeypatch):
    def case(name, tier, tags):
        return {
            "name": name,
            "conversation": [{"user": "read " + name}],
            "expectations": {},
            "tier": tier,
            "tags": tags,
        }

    (tmp_path / "alpha.yaml").write_text(
        json.dumps([case("a1", 1, ["keep"]), case("a2", 2, [])])
    )
    (tmp_path / "beta.yaml").write_text(json.dumps([case("b1", 0, ["keep"])]))
    (tmp_path / "planned_orchestration.yaml").write_text(
        json.dumps([case("deterministic", 1, ["keep"])])
    )
    monkeypatch.setattr(runner, "DATASETS_DIR", tmp_path)
    monkeypatch.setattr(runner, "get_tool_definitions", lambda: [])
    return tmp_path


def _metadata(selection, *, model="one", provider="Provider", fixture="toy"):
    return build_run_fingerprint(
        provider=provider,
        model=model,
        fixture=fixture,
        datasets=list(selection.datasets),
        tier=None,
        tags=None,
        prompt="prompt",
        tool_definitions=[],
        selection_manifest=selection.manifest(),
    )


def _summary(selection, **overrides):
    return build_summary(
        gate="demo",
        run_metadata=_metadata(selection, **overrides),
        results=[runner.EvalResult(case) for _dataset, case in selection.iter_cases()],
    )


def test_actual_selection_excludes_unexecuted_datasets_and_deterministic_defaults(
    dataset_files,
):
    selected = runner.select_eval_cases(tier=1, tags=["keep"])
    assert selected.datasets == ("alpha",)
    assert [(dataset, case.name) for dataset, case in selected.iter_cases()] == [
        ("alpha", "a1")
    ]
    manifest = selected.manifest()
    assert manifest["version"] == "chat-eval-selection/1"
    assert [(item["dataset"], item["name"]) for item in manifest["cases"]] == [
        ("alpha", "a1")
    ]
    assert len(manifest["hash"]) == 64
    assert len(manifest["cases"][0]["case_hash"]) == 64
    manifest["cases"].clear()
    assert selected.manifest()["cases"]


def test_frozen_case_selection_is_reused_across_models_and_file_changes(
    dataset_files, monkeypatch
):
    selected = runner.select_eval_cases(["alpha"], tier=1)
    expected = selected.manifest()
    observed = []

    async def execute(provider, case, *args, **kwargs):
        observed.append((provider, case.name, dict(case.expectations)))
        case.expectations["mutated"] = True
        return runner.EvalResult(case)

    monkeypatch.setattr(runner, "run_case", execute)
    asyncio.run(runner.run_eval_suite("model-one", selection=selected))
    (dataset_files / "alpha.yaml").write_text("[]")
    asyncio.run(runner.run_eval_suite("model-two", selection=selected))
    assert observed == [("model-one", "a1", {}), ("model-two", "a1", {})]
    assert selected.manifest() == expected


def test_empty_suite_is_rejected_before_any_provider_call(dataset_files, monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail("An empty selection must not reach a provider.")

    monkeypatch.setattr(runner, "run_case", unexpected)
    with pytest.raises(ValueError, match="No eval cases"):
        asyncio.run(runner.run_eval_suite(object(), ["alpha"], tags=["missing"]))


def test_explicit_selection_rejects_filters_and_mismatched_metadata(dataset_files):
    selected = runner.select_eval_cases(["alpha"])
    with pytest.raises(ValueError, match="selection"):
        asyncio.run(runner.run_eval_suite(object(), ["alpha"], selection=selected))
    with pytest.raises(ValueError, match="selection"):
        asyncio.run(
            runner.run_eval_suite(
                object(), selection=selected, run_metadata={"datasets": ["beta"]}
            )
        )


def test_same_actual_cohort_can_compare_different_models_and_providers(dataset_files):
    selected = runner.select_eval_cases(["alpha"])
    baseline = _summary(selected)
    current = _summary(selected, model="two", provider="OtherProvider")
    assert baseline.run_hash != current.run_hash
    comparison = compare_to_baseline(current, baseline)
    assert comparison.comparable and not comparison.regressed


@pytest.mark.parametrize(
    "difference", ["fixture", "gate", "case", "expectation", "contract"]
)
def test_changed_business_cohort_or_scoring_context_is_not_comparable(
    dataset_files, difference
):
    selected = runner.select_eval_cases(["alpha"])
    baseline = _summary(selected)
    current = _summary(selected)
    if difference == "fixture":
        current = replace(current, fixture="large")
    elif difference == "gate":
        current = replace(current, gate="other")
    elif difference == "contract":
        current = replace(current, evaluation_contract="chat-evaluation/future")
    else:
        values = json.loads((dataset_files / "alpha.yaml").read_text())
        if difference == "case":
            values.pop()
        else:
            values[0]["expectations"] = {"answer_contains": ["new requirement"]}
        (dataset_files / "alpha.yaml").write_text(json.dumps(values))
        current = _summary(runner.select_eval_cases(["alpha"]))
    assert compare_to_baseline(current, baseline).comparable is False


def test_empty_readiness_is_not_a_passing_measurement():
    summary = build_summary(
        gate="demo",
        run_metadata={
            "run_hash": "empty",
            "provider": "P",
            "model": "M",
            "fixture": "toy",
            "datasets": [],
            "tier": None,
        },
        results=[],
    )
    assert (
        readiness_exit_code(summary=summary, comparison=None, fail_on_regression=False)
        != 0
    )
    assert summary.pass_rate == 0
    assert summary.run_status == "empty"


def test_empty_report_is_explicitly_not_evaluated():
    report = runner.print_report([])
    assert "not evaluated" in report.lower()
    assert "100%" not in report


@pytest.mark.parametrize("damage", ["hash", "version", "case_hash", "datasets"])
def test_fingerprint_rejects_invalid_selection_proof(dataset_files, damage):
    selected = runner.select_eval_cases(["alpha"])
    manifest = selected.manifest()
    datasets = list(selected.datasets)
    if damage == "hash":
        manifest["hash"] = "0" * 64
    elif damage == "version":
        manifest["version"] = "unknown"
    elif damage == "case_hash":
        manifest["cases"][0]["case_hash"] = False
    else:
        datasets = ["beta"]
    with pytest.raises(ValueError, match="selection"):
        build_run_fingerprint(
            provider="P",
            model="M",
            fixture="toy",
            datasets=datasets,
            tier=None,
            tags=None,
            prompt="system",
            tool_definitions=[],
            selection_manifest=manifest,
        )


def test_readiness_rejects_a_manifest_that_does_not_describe_results(dataset_files):
    selected = runner.select_eval_cases(["alpha"])
    results = [runner.EvalResult(case) for _dataset, case in selected.iter_cases()]
    with pytest.raises(ValueError, match="selection"):
        build_summary(
            gate="demo", run_metadata=_metadata(selected), results=results[:-1]
        )


def test_unscored_result_stays_in_fixed_selected_denominator(dataset_files):
    selected = runner.select_eval_cases(["alpha"])
    results = [runner.EvalResult(case) for _dataset, case in selected.iter_cases()]
    results[0].error = "provider_failed"
    summary = build_summary(
        gate="demo", run_metadata=_metadata(selected), results=results
    )
    assert (summary.total, summary.passed, summary.unscored) == (2, 1, 1)
    assert summary.pass_rate == 0.5


def test_historical_summary_load_does_not_invent_cohort_or_rewrite_bytes(
    dataset_files, tmp_path
):
    current = _summary(runner.select_eval_cases(["alpha"]))
    old = asdict(current)
    old.pop("selection_manifest")
    old.pop("run_status")
    path = tmp_path / "historical.json"
    original = json.dumps(old).encode()
    path.write_bytes(original)
    loaded = load_summary(path)
    assert loaded.selection_manifest is None
    assert compare_to_baseline(current, loaded).comparable is False
    assert path.read_bytes() == original


def test_readiness_rejects_renamed_case_identity_with_original_case_hash(dataset_files):
    selected = runner.select_eval_cases(["alpha"])
    metadata = _metadata(selected)
    manifest = metadata["selection_manifest"]
    manifest["cases"][0]["name"] = "another-case"
    manifest["hash"] = stable_hash(manifest["cases"], length=64)
    results = [runner.EvalResult(case) for _dataset, case in selected.iter_cases()]
    with pytest.raises(ValueError, match="selection"):
        build_summary(gate="demo", run_metadata=metadata, results=results)
