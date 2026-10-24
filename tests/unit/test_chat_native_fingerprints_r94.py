"""Native instruction changes must invalidate both actual CLI fingerprints."""

from dataclasses import asdict, replace
from itertools import product
import json
import sys

import pytest

from general_manager.chat.evals import fingerprints, runner
from general_manager.chat.evals.baseline import compare_to_baseline, load_summary
from general_manager.chat.evals.readiness import build_summary
from general_manager.chat.planned import (
    choice_context,
    contract,
    deferred_calculations,
    deferred_schema,
    planner,
    scheduler,
    schema_projection,
    synthesis,
)
from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import (
    EvidenceRequirement,
    PlannedTask,
    SchemaBinding,
)
from general_manager.chat.providers.base import Message


def _fingerprint(**overrides):
    arguments = {
        "provider": "Provider",
        "model": "offline",
        "fixture": "toy",
        "datasets": ["basic_queries"],
        "tier": 0,
        "tags": ["read"],
        "prompt": "unchanged legacy system prompt",
        "tool_definitions": [],
        "native_prompt_contract": fingerprints.build_native_prompt_contract(),
    }
    arguments.update(overrides)
    return fingerprints.build_run_fingerprint(**arguments)


def _capture_cli(module, monkeypatch, tmp_path):
    observed = []

    def execute(provider, *, selection, run_metadata, **kwargs):
        observed.append(run_metadata)
        return [runner.EvalResult(case) for _dataset, case in selection.iter_cases()]

    monkeypatch.setattr(module, "setup_test_schema", lambda: None)
    monkeypatch.setattr(module, "get_tool_definitions", lambda: [])
    monkeypatch.setattr(module, "build_system_prompt", lambda: "same system")
    monkeypatch.setattr(module, "_resolve_ollama_base_url", lambda url: (url, False))
    monkeypatch.setattr(
        module.OllamaProvider,
        "_provider_config",
        staticmethod(
            lambda _configured=None: {
                "model": "offline",
                "base_url": "http://localhost:11434",
            }
        ),
    )
    monkeypatch.setattr(module, "run_eval_suite_sync", execute)
    argv = [module.__name__, "--dataset", "basic_queries", "--tier", "0"]
    readiness = module.__name__.endswith("run_chat_readiness_loop")
    if readiness:
        argv += ["--skip-tests", "--output-dir", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", argv)
    if readiness:
        with pytest.raises(SystemExit) as stopped:
            module.main()
        assert stopped.value.code == 0
    else:
        module.main()
    assert len(observed) == 1
    return observed[0]


@pytest.mark.parametrize("entrypoint", ["evals", "readiness"])
@pytest.mark.parametrize(
    "owner,attribute",
    [
        (planner, "_PLANNER_INSTRUCTION"),
        (scheduler, "READ_TOOL_GUIDANCE"),
        (synthesis, "_SYNTHESIS_INSTRUCTION"),
    ],
)
def test_each_cli_binds_actual_native_phase_instructions(
    entrypoint, owner, attribute, monkeypatch, tmp_path
):
    from scripts import run_chat_evals, run_chat_readiness_loop

    module = run_chat_evals if entrypoint == "evals" else run_chat_readiness_loop
    before = _capture_cli(module, monkeypatch, tmp_path)
    monkeypatch.setattr(owner, attribute, getattr(owner, attribute) + " Updated rule.")
    after = _capture_cli(module, monkeypatch, tmp_path)
    assert after["prompt_hash"] != before["prompt_hash"]
    assert after["run_hash"] != before["run_hash"]
    assert (
        after["evaluation_contract"]
        == before["evaluation_contract"]
        == "chat-evaluation/5"
    )
    assert after["selection_manifest"] == before["selection_manifest"]


def test_both_clis_bind_same_versioned_native_contract(monkeypatch, tmp_path):
    from scripts import run_chat_evals, run_chat_readiness_loop

    evals = _capture_cli(run_chat_evals, monkeypatch, tmp_path)
    readiness = _capture_cli(run_chat_readiness_loop, monkeypatch, tmp_path)
    assert evals == readiness
    assert evals["fingerprint_contract"] == "chat-run-fingerprint/2"
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["fingerprint_contract"] == "chat-run-fingerprint/2"


@pytest.mark.parametrize(
    "owner,attribute",
    [
        (planner, "CHOICE_INSTRUCTION"),
        (planner, "DEFINITION_CHOICE_INSTRUCTION"),
        (scheduler, "CATEGORY_BINDING_INSTRUCTION"),
        (deferred_calculations, "INSTRUCTION"),
        (deferred_schema, "INSTRUCTION"),
        (schema_projection, "INSTRUCTION"),
        (schema_projection, "REFERENCE_INSTRUCTION"),
        (schema_projection, "SYNTHESIS_REFERENCE_INSTRUCTION"),
    ],
)
def test_conditional_native_instructions_invalidate_prompt_and_run(
    owner, attribute, monkeypatch
):
    before = _fingerprint()
    monkeypatch.setattr(owner, attribute, getattr(owner, attribute) + " Updated rule.")
    after = _fingerprint()
    assert after["prompt_hash"] != before["prompt_hash"]
    assert after["run_hash"] != before["run_hash"]


@pytest.mark.parametrize(
    "owner,attribute",
    [(contract, "PLAN_INSTRUCTION"), (choice_context, "CHOICE_INSTRUCTION")],
)
def test_unused_original_import_does_not_override_owner_consumed_value(
    owner, attribute, monkeypatch
):
    before = _fingerprint()
    monkeypatch.setattr(
        owner, attribute, getattr(owner, attribute) + " Unconsumed rule."
    )
    assert _fingerprint() == before


def test_planner_contract_covers_real_choice_definition_and_repair_variants():
    native = fingerprints.build_native_prompt_contract()
    for choices, definitions, correction in product((False, True), repeat=3):
        history = [Message("user", "Assess these records.")]
        if choices:
            history += [Message("assistant", "Which metric should I use?")]
        configured = "Application definitions." if definitions else ""
        for rejection in (None, {"code": "invalid_contract"}):
            messages = planner._request_messages(
                "Assess records",
                history,
                {},
                configured_context=configured,
                correction=correction,
                rejection=rejection,
            )
            actual = tuple(
                message.content
                for message in messages
                if message.role == "system" and message.content != configured
            )
            assert actual in native["planner"]


def test_executor_contract_covers_real_deferred_and_schema_variants():
    native = fingerprints.build_native_prompt_contract()
    for deferred, pending_schemas in product((False, True), repeat=2):
        requirements = [EvidenceRequirement("rows", "query", "Read records", None)]
        if deferred:
            requirements += [
                EvidenceRequirement(
                    "total", "calculation", "Sum", "sum", binding_required=True
                )
            ]
        if pending_schemas:
            requirements += [
                EvidenceRequirement(
                    "schema",
                    "schema",
                    "Inspect definitions",
                    None,
                    schema=SchemaBinding("Shipment", "detail", (), "current"),
                )
            ]
        task = PlannedTask("read", "Assess records", (), tuple(requirements), (), ())
        messages = scheduler._executor_messages("Assess records", task, EvidenceStore())
        assert (
            next(m.content for m in messages if m.role == "system")
            in native["executor"]
        )


def test_native_fingerprint_is_stable_and_retains_configuration_semantics():
    before = _fingerprint()
    assert _fingerprint() == before
    for field, value in (
        ("provider", "OtherProvider"),
        ("model", "other-model"),
        ("fixture", "large"),
        ("datasets", ["other_dataset"]),
        ("tier", 1),
        ("tags", ["other_tag"]),
        ("prompt", "changed system prompt"),
        ("tool_definitions", [{"name": "query"}]),
    ):
        assert _fingerprint(**{field: value})["run_hash"] != before["run_hash"]
    assert _fingerprint(tags=["z", "a"], datasets=["z", "a"]) == _fingerprint(
        tags=["a", "z"], datasets=["a", "z"]
    )


def test_legacy_fingerprints_stay_unchanged_without_native_contract():
    legacy = _fingerprint(native_prompt_contract=None)
    assert "fingerprint_contract" not in legacy
    assert legacy["prompt_hash"] == fingerprints.stable_hash(
        "unchanged legacy system prompt"
    )
    material = {key: value for key, value in legacy.items() if key != "run_hash"}
    assert legacy["run_hash"] == fingerprints.stable_hash(material)
    assert legacy["run_hash"] != _fingerprint()["run_hash"]


def test_readiness_does_not_mix_legacy_and_native_fingerprint_versions(tmp_path):
    selection = runner.select_eval_cases(["basic_queries"], tier=0)
    results = [runner.EvalResult(case) for _dataset, case in selection.iter_cases()]
    metadata = _fingerprint(selection_manifest=selection.manifest())
    current = build_summary(gate="demo", run_metadata=metadata, results=results)
    old = asdict(current)
    old.pop("fingerprint_contract", None)
    path = tmp_path / "historical.json"
    original = json.dumps(old).encode()
    path.write_bytes(original)
    legacy = load_summary(path)
    assert compare_to_baseline(current, legacy).comparable is False
    assert compare_to_baseline(legacy, current).comparable is False
    assert compare_to_baseline(legacy, legacy).comparable is True
    assert path.read_bytes() == original
    assert (
        current.evaluation_contract == legacy.evaluation_contract == "chat-evaluation/5"
    )
    other = replace(
        current, model="other", provider="OtherProvider", run_hash="other-prompt"
    )
    assert compare_to_baseline(current, other).comparable is True
