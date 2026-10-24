"""A selected run preserves complete dialogues and makes overrides explicit."""

from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from experiments.gm_eval.catalog import load_catalog


def test_request_defaults_to_every_original_case_and_turn():
    from experiments.gm_eval.selection import build_run_request

    catalog = load_catalog()
    request = build_run_request(catalog)
    assert request["case_ids"] == [case["id"] for case in catalog]
    assert request["cases"] == catalog
    assert (request["denominator"], request["whole_turns"]) == (121, 149)
    assert request["first_message"] is None
    assert request["benchmark_comparable"] is True


@pytest.mark.parametrize("ids", [["E107"], ["E103"], ["E107", "E103"]])
def test_selection_preserves_catalog_order_and_whole_original_dialogues(ids):
    from experiments.gm_eval.selection import build_run_request

    catalog = load_catalog()
    before = deepcopy(catalog)
    request = build_run_request(catalog, ids)
    selected = [case for case in catalog if case["id"] in ids]
    assert request["cases"] == selected
    assert request["whole_turns"] == sum(len(case["turns"]) for case in selected)
    request["cases"][0]["turns"][0] = "caller mutation"
    assert catalog == before


@pytest.mark.parametrize(
    "ids,match", [(["E999"], "unknown case"), (["E107", "E107"], "duplicate case")]
)
def test_bad_selection_rejects_before_a_run(ids, match):
    from experiments.gm_eval.selection import build_run_request

    with pytest.raises(ValueError, match=match):
        build_run_request(load_catalog(), ids)


def test_override_replaces_only_first_message_for_each_selected_case():
    from experiments.gm_eval.selection import build_run_request, dialogue_input

    request = build_run_request(
        load_catalog(), ["E103", "E107"], first_message="  Explicit question.\n"
    )
    assert request["benchmark_comparable"] is False
    assert request["first_message"] == "  Explicit question.\n"
    for original in request["cases"]:
        effective, provenance = dialogue_input(original, request["first_message"])
        assert effective["turns"] == [request["first_message"], *original["turns"][1:]]
        assert provenance["original_turns"] == original["turns"]
        assert provenance["effective_turns"] == effective["turns"]
        assert provenance["original_case_sha256"] != provenance["effective_case_sha256"]
        assert provenance["benchmark_comparable"] is False


@pytest.mark.parametrize("message", ["", " \n", 1, False])
def test_invalid_override_rejected(message):
    from experiments.gm_eval.selection import build_run_request

    with pytest.raises(ValueError, match="first message"):
        build_run_request(load_catalog(), ["E107"], first_message=message)


def test_explicit_equal_text_is_still_recorded_as_an_override():
    from experiments.gm_eval.selection import build_run_request

    original = next(case for case in load_catalog() if case["id"] == "E107")
    request = build_run_request([original], first_message=original["turns"][0])
    assert request["benchmark_comparable"] is False


def test_run_cli_passes_one_bound_request_to_live_owner(monkeypatch, tmp_path):
    from experiments.gm_eval import cli, live

    calls = []
    monkeypatch.setattr(
        live,
        "run_reviewed",
        lambda request, **kwargs: calls.append((request, kwargs)) or 7,
    )
    assert (
        cli.main(
            [
                "run",
                "--case",
                "E107",
                "--case",
                "E103",
                "--first-message",
                "Question",
                "--start-record",
                str(tmp_path / "bound.json"),
                "--start-record-sha256",
                "a" * 64,
            ]
        )
        == 7
    )
    assert len(calls) == 1
    assert calls[0][0]["case_ids"] == ["E103", "E107"]
    assert calls[0][0]["whole_turns"] == 3
    assert calls[0][0]["first_message"] == "Question"


def test_run_cli_without_case_ids_requests_all_cases(monkeypatch):
    from experiments.gm_eval import cli, live

    requests = []
    monkeypatch.setattr(
        live, "run_reviewed", lambda request, **_kwargs: requests.append(request) or 0
    )
    assert cli.main(["run"]) == 0
    assert len(requests[0]["case_ids"]) == 121


def test_prepare_run_saves_exact_request_without_auth_or_launch(tmp_path, monkeypatch):
    from experiments.gm_eval import cli, live
    from experiments.gm_eval.selection import build_run_request

    monkeypatch.setattr(
        live,
        "run_reviewed",
        lambda *_args, **_kwargs: pytest.fail("prepare must not launch"),
    )
    assert (
        cli.main(["run", "--prepare-only", "--case", "E107", "--output", str(tmp_path)])
        == 0
    )
    assert json.loads((tmp_path / "run-request.json").read_text()) == build_run_request(
        load_catalog(), ["E107"]
    )


@pytest.mark.parametrize(
    "command", ["catalog", "fixtures", "offline", "parity", "harness"]
)
def test_unrelated_offline_commands_do_not_silently_ignore_an_override(command):
    from experiments.gm_eval.cli import main

    with pytest.raises(ValueError, match="run command"):
        main([command, "--first-message", "Question"])


def test_modified_dialogue_cannot_build_an_original_gold_judge_request():
    from experiments.gm_eval.adjudication import build_adjudication_request

    with pytest.raises(ValueError, match="modified_dialogue_has_no_reference"):
        build_adjudication_request(
            {"case_id": "E107", "turn_index": 0},
            {"case_id": "E107", "dialogue_input": {"benchmark_comparable": False}},
            0,
        )


@pytest.mark.parametrize("case_id", ["E001", "E101"])
@pytest.mark.parametrize("first_message", [None, "Show me the customers, please."])
def test_native_harness_executes_all_effective_turns_and_records_provenance(
    case_id, first_message
):
    from tests.experiments.test_gm_eval_harness import _child

    result = _child(
        f"case_id={case_id!r}\nfirst_message={first_message!r}\n"
        + """
import asyncio, json
from copy import deepcopy
from experiments.gm_eval.catalog import load_catalog
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.execution.scripted import make_script
case = next(case for case in load_catalog() if case['id'] == case_id)
original = deepcopy(case)
runtime = bootstrap(manager_count=case['managers'], snapshot=case['snapshot'], variant=case['fixture_variant'])
try:
    run = asyncio.run(run_case(runtime, case, make_profile('weak-only'), make_script(case, runtime), first_message=first_message))
    assert case == original
    print(json.dumps({'original': original['turns'], 'actual': [turn['user'] for turn in run['turns']], 'dialogue': run['dialogue_input'], 'history': [turn['history'] for turn in run['turns']]}))
finally:
    runtime.close()
"""
    )
    expected = list(result["original"])
    if first_message is not None:
        expected[0] = first_message
    assert result["actual"] == expected
    assert result["dialogue"]["effective_turns"] == expected
    assert result["dialogue"]["benchmark_comparable"] is (first_message is None)
    if len(expected) > 1:
        assert any(
            message["role"] == "user" and message["content"] == expected[0]
            for message in result["history"][1]
        )


def _bound_start(tmp_path, request):
    def freeze(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        path.chmod(0o444)
        return {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}

    item = freeze("request.json", request)
    launcher = tmp_path / "root_r26_launcher.py"
    launcher.write_text("# A fixture; execution is intercepted at subprocess.run.\n")
    launcher.chmod(0o444)
    manifest = freeze(
        "control-manifest.json",
        {launcher.name: sha256(launcher.read_bytes()).hexdigest()},
    )
    return freeze(
        "start.json",
        {
            "run_request": item,
            "control_root": str(tmp_path),
            "control_manifest_sha256": manifest["sha256"],
        },
    )


def test_live_entry_calls_existing_supervisor_exactly_once_and_returns_its_stop(
    monkeypatch, tmp_path
):
    from pathlib import Path
    from experiments.gm_eval import live
    from experiments.gm_eval.selection import build_run_request

    request = build_run_request(load_catalog(), ["E107"])
    record = _bound_start(tmp_path, request)
    monkeypatch.setenv(
        "R26_EXPECTED_CONTROL_MANIFEST_SHA256",
        json.loads((tmp_path / "start.json").read_text())["control_manifest_sha256"],
    )
    calls = []
    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda argv, **kwargs: calls.append((argv, kwargs))
        or SimpleNamespace(returncode=3),
    )
    assert (
        live.run_reviewed(
            request,
            start_record=Path(record["path"]),
            start_record_sha256=record["sha256"],
        )
        == 3
    )
    assert len(calls) == 1
    assert calls[0][0][2] == str(tmp_path / "root_r26_launcher.py")
    assert calls[0][1] == {"check": False}


@pytest.mark.parametrize(
    "changed", ["selection", "override", "record_hash", "launcher_hash"]
)
def test_mismatched_live_request_never_reaches_supervisor(
    monkeypatch, tmp_path, changed
):
    from pathlib import Path
    from experiments.gm_eval import live
    from experiments.gm_eval.selection import build_run_request

    request = build_run_request(load_catalog(), ["E107"])
    record = _bound_start(tmp_path, request)
    monkeypatch.setenv(
        "R26_EXPECTED_CONTROL_MANIFEST_SHA256",
        json.loads((tmp_path / "start.json").read_text())["control_manifest_sha256"],
    )
    if changed == "selection":
        request = build_run_request(load_catalog())
    elif changed == "override":
        request = build_run_request(
            load_catalog(), ["E107"], first_message="New question"
        )
    elif changed == "record_hash":
        record["sha256"] = "0" * 64
    else:
        launcher = tmp_path / "root_r26_launcher.py"
        launcher.chmod(0o644)
        launcher.write_text("changed\n")
        launcher.chmod(0o444)
    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("mismatch must reject before launch"),
    )
    with pytest.raises(ValueError):
        live.run_reviewed(
            request,
            start_record=Path(record["path"]),
            start_record_sha256=record["sha256"],
        )


@pytest.mark.parametrize(
    "option",
    [["--seed", "18"], ["--seed=17"], ["--profile", "weak-fallback"], ["--scale", "5"]],
)
def test_live_does_not_ignore_offline_configuration(option):
    from experiments.gm_eval.cli import main

    with pytest.raises(ValueError, match="offline-only"):
        main(["run", *option])


def test_explicit_supported_reference_round_trips():
    from experiments.gm_eval.selection import build_run_request, validate_run_request

    catalog = load_catalog(reference_version="1.16")
    request = build_run_request(catalog, ["E107"], reference_version="1.16")
    assert validate_run_request(request, catalog) == request


@pytest.mark.parametrize("option", ["--see", "--prof"])
def test_cli_rejects_abbreviated_options(option):
    from experiments.gm_eval.cli import main

    with pytest.raises(SystemExit) as error:
        main(["run", option, "18"])
    assert error.value.code == 2


def test_private_derived_workspace_rejects_before_read(tmp_path, monkeypatch):
    from pathlib import Path
    from experiments.gm_eval import live

    monkeypatch.setattr(
        Path, "read_bytes", lambda _path: pytest.fail("private root must not be read")
    )
    with pytest.raises(ValueError, match="private paths"):
        live.run_reviewed(
            {},
            start_record=tmp_path
            / ".config"
            / "credential-store"
            / "run"
            / "start.json",
            start_record_sha256="a" * 64,
        )


@pytest.mark.parametrize("mutation", ["no_pin", "wrong_pin", "private", "symlink"])
def test_live_indirect_artifacts_require_public_paths_and_independent_pin(
    tmp_path, monkeypatch, mutation
):
    from pathlib import Path
    from experiments.gm_eval import live
    from experiments.gm_eval.selection import build_run_request

    request = build_run_request(load_catalog(), ["E107"])
    item = _bound_start(tmp_path, request)
    record_path = Path(item["path"])
    record = json.loads(record_path.read_text())
    monkeypatch.setenv(
        "R26_EXPECTED_CONTROL_MANIFEST_SHA256", record["control_manifest_sha256"]
    )
    if mutation == "no_pin":
        monkeypatch.delenv("R26_EXPECTED_CONTROL_MANIFEST_SHA256")
    elif mutation == "wrong_pin":
        monkeypatch.setenv("R26_EXPECTED_CONTROL_MANIFEST_SHA256", "0" * 64)
    else:
        if mutation == "private":
            record["run_request"]["path"] = str(
                tmp_path / "credentials" / "request.json"
            )
        else:
            alias = tmp_path / "alias"
            alias.symlink_to(tmp_path, target_is_directory=True)
            record["run_request"]["path"] = str(alias / "request.json")
        record_path.chmod(0o644)
        record_path.write_text(json.dumps(record))
        record_path.chmod(0o444)
        item["sha256"] = sha256(record_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("must reject before launch"),
    )
    with pytest.raises(ValueError):
        live.run_reviewed(
            request, start_record=record_path, start_record_sha256=item["sha256"]
        )
