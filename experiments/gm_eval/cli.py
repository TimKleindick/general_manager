"""Offline-only entry point; Django fixtures execute in isolated child processes."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


class ValidationError(ValueError):
    """A local experimental validation request is inconsistent."""


def select_cases(
    cases: list[dict[str, Any]], case_ids: Sequence[str], scale: int | None
) -> list[dict[str, Any]]:
    unknown = set(case_ids) - {c["id"] for c in cases}
    if unknown:
        message = f"unknown case IDs: {sorted(unknown)}"
        raise ValidationError(message)
    selected = [
        c
        for c in cases
        if (not case_ids or c["id"] in case_ids)
        and (scale is None or c["managers"] == scale)
    ]
    if not selected:
        message = "no cases match selection"
        raise ValidationError(message)
    return selected


def catalog_report() -> dict[str, Any]:
    from .catalog import load_catalog, validate_catalog
    from .oracle import expected_turn

    cases = load_catalog()
    validation = validate_catalog(cases)
    oracles = [
        expected_turn(case, turn)
        for case in cases
        for turn in range(len(case["turns"]))
    ]
    return {
        "mode": "offline",
        "model_performance_measured": False,
        "counts": {
            "cases": len(cases),
            "turns": len(oracles),
            "languages": dict(Counter(c["language"] for c in cases)),
            "scales": dict(Counter(str(c["managers"]) for c in cases)),
            "core_intents": len({c["core_id"] for c in cases if c["core_id"]}),
            "core_instances": sum(c["core_id"] is not None for c in cases),
        },
        "catalog_validation": validation,
        "oracles": oracles,
        "reference_corrections": [
            {"case_id": oracle["case_id"], **correction}
            for oracle in oracles
            for correction in oracle.get("reference_corrections", [])
        ],
        "reference_issues": [
            {"case_id": oracle["case_id"], **issue}
            for oracle in oracles
            for issue in oracle.get("reference_issues", [])
        ],
    }


def source_manifest() -> dict[str, str]:
    """Hash only experimental sources and named production integration modules."""
    import importlib.util

    package = Path(__file__).parent
    files = {
        f"experiments/gm_eval/{path.relative_to(package)}": path
        for path in package.rglob("*")
        if path.suffix in {".py", ".json"}
        and path.is_file()
        and "__pycache__" not in path.parts
    }
    spec = importlib.util.find_spec("general_manager")
    if spec and spec.submodule_search_locations:
        product = Path(next(iter(spec.submodule_search_locations)))
        for rel in (
            "api/graphql_search.py",
            "chat/consumer.py",
            "chat/tools.py",
            "chat/graphql_contract.py",
            "chat/schema_index.py",
            "chat/tool_metadata.py",
            "chat/system_prompt.py",
            "chat/views.py",
            "chat/context.py",
            "chat/planned/config.py",
            "chat/planned/contract.py",
            "chat/planned/synthesis.py",
            "chat/planned/clarification.py",
            "chat/planned/selector_clarification.py",
            "chat/planned/models.py",
            "chat/planned/planner.py",
            "chat/planned/validation.py",
            "chat/planned/scheduler.py",
            "chat/planned/provider_calls.py",
            "chat/providers/base.py",
        ):
            path = product / rel
            if path.is_file():
                files[f"src/general_manager/{rel}"] = path
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in sorted(files.items())
    }


def _isolated_worker(payload: Mapping[str, Any], output: Path) -> dict[str, Any]:
    """Fixed executable/module only; no company settings are inherited."""
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [root, env.get("PYTHONPATH", "")]))
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed Python module, no shell or executable input
            [
                sys.executable,
                "-m",
                "experiments.gm_eval",
                "_worker",
                json.dumps(dict(payload)),
                str(output),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "harness_failure",
            "worker_timeout_seconds": 300,
            "diagnostic": "Isolated offline worker exceeded its wall-clock limit.",
            "case_ids": payload.get("case_ids", [payload.get("case_id")]),
        }
    if completed.returncode or not output.exists():
        return {
            "status": "fixture_invalid"
            if payload["command"] == "fixtures"
            else "harness_failure",
            "worker_exit_code": completed.returncode,
            "diagnostic": completed.stderr[-6000:],
            "case_ids": payload.get("case_ids", []),
        }
    result = json.loads(output.read_text())
    if not isinstance(result, dict):
        message = "worker output must be an object"
        raise ValidationError(message)
    return result


def fixture_report(cases: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    groups: dict[tuple[int, str, str], list[str]] = {}
    for case in cases:
        key = (case["managers"], case["snapshot"], case["fixture_variant"])
        groups.setdefault(key, []).append(case["id"])
    results = []
    with tempfile.TemporaryDirectory(prefix="gm-eval-fixtures-") as temporary:
        for index, ((scale, snapshot, variant), ids) in enumerate(groups.items()):
            payload = {
                "command": "fixtures",
                "manager_count": scale,
                "snapshot": snapshot,
                "variant": variant,
                "seed": seed,
                "case_ids": ids,
            }
            result = _isolated_worker(payload, Path(temporary) / f"{index}.json")
            result.update(
                manager_count=scale, snapshot=snapshot, variant=variant, case_ids=ids
            )
            results.append(result)
    return {
        "mode": "offline",
        "model_performance_measured": False,
        "selected_cases": len(cases),
        "fixture_groups": len(groups),
        "results": results,
    }


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "command",
        nargs="?",
        default="catalog",
        choices=("catalog", "fixtures", "harness", "parity", "offline"),
    )
    command.add_argument(
        "--case",
        action="append",
        default=[],
        help="Repeat to select case IDs; catalog order is retained.",
    )
    command.add_argument("--scale", type=int, choices=(5, 10, 50, 100, 250))
    command.add_argument("--seed", type=int, default=17)
    command.add_argument(
        "--profile",
        choices=("weak-only", "weak-fallback", "strong-only"),
        default="weak-fallback",
    )
    command.add_argument(
        "--output", type=Path, help="New report directory outside product commits."
    )
    return command


def _worker_main(args: list[str]) -> int:
    payload = json.loads(args[0])
    from .runtime import bootstrap

    runtime = bootstrap(
        manager_count=payload["manager_count"],
        snapshot=payload["snapshot"],
        variant=payload["variant"],
        seed=payload["seed"],
    )
    try:
        if payload["command"] == "fixtures":
            result = runtime.verify()
            result["status"] = (
                "interface_capability_gap"
                if result.get("gaps")
                else "infrastructure_check"
            )
            result["schema_index_sha256"] = hashlib.sha256(
                json.dumps(runtime.schema_index, sort_keys=True).encode()
            ).hexdigest()
            result["schema_index_bytes"] = len(
                json.dumps(runtime.schema_index, sort_keys=True).encode()
            )
            result["case_censuses"] = {
                case_id: runtime.census() for case_id in payload["case_ids"]
            }
        else:
            result = _harness_worker(runtime, payload)
        Path(args[1]).write_text(
            json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
        )
        return 0
    finally:
        runtime.close()


def _harness_worker(runtime: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    # The production adapter and scripted-profile factory own all scheduling.
    from .harness import offline_check

    return offline_check(
        runtime,
        payload["case_id"],
        profile=payload["profile"],
        parity=payload["command"] == "parity",
    )


def harness_report(
    cases: list[dict[str, Any]], seed: int, profile: str, parity: bool
) -> dict[str, Any]:
    results = []
    with tempfile.TemporaryDirectory(prefix="gm-eval-harness-") as temporary:
        for index, case in enumerate(cases):
            payload = {
                "command": "parity" if parity else "harness",
                "case_id": case["id"],
                "manager_count": case["managers"],
                "snapshot": case["snapshot"],
                "variant": case["fixture_variant"],
                "seed": seed,
                "profile": profile,
            }
            result = _isolated_worker(payload, Path(temporary) / f"{index}.json")
            result["case_id"] = case["id"]
            from .efficiency import measure_run

            for run in (result.get("scheduler"), result.get("consumer"), result):
                if isinstance(run, dict) and "trace" in run:
                    run["measurements"] = measure_run(run)
            results.append(result)
    return {"mode": "offline", "model_performance_measured": False, "results": results}


def report_has_failures(value: Any) -> bool:
    """Infrastructure failures and unequal parity must affect the command exit."""
    if isinstance(value, Mapping):
        status = value.get("status")
        if (
            isinstance(status, str)
            and status
            in {
                "fixture_invalid",
                "harness_failure",
                "transport_failure",
                "judge_failure",
                "budget_exhausted",
                "model_task_failure",
            }
        ) or value.get("parity_passed") is False:
            return True
        return any(report_has_failures(item) for item in value.values())
    if isinstance(value, list):
        return any(report_has_failures(item) for item in value)
    return False


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "_worker":
        return _worker_main(args[1:])
    options = parser().parse_args(args)
    from .catalog import load_catalog

    selected = select_cases(load_catalog(), options.case, options.scale)
    output = options.output or Path(tempfile.mkdtemp(prefix="gm-eval-report-"))
    output.mkdir(parents=True, exist_ok=True)
    target = output / "report.json"
    if target.exists():
        message = f"report exists; use a fresh output directory: {target}"
        raise ValidationError(message)
    report: dict[str, Any] = {
        "schema_version": "gm-eval-offline-v1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline",
        "model_performance_measured": False,
        "command": options.command,
        "source_hashes": source_manifest(),
        "fixture_clock": "2026-10-03T12:00:00Z",
        "seed": options.seed,
    }
    if options.command in {"catalog", "offline"}:
        report["catalog"] = catalog_report()
    if options.command in {"fixtures", "offline"}:
        report["fixtures"] = fixture_report(selected, options.seed)
    if options.command in {"harness", "parity", "offline"}:
        parity_ids = {"E007", "E008", "E009", "E010", "E100"}
        harness_cases = (
            [c for c in selected if c["id"] in parity_ids]
            if options.command in {"parity", "offline"} and not options.case
            else selected
        )
        if not harness_cases:
            message = "no default parity cases match; select an explicit --case"
            raise ValidationError(message)
        report["harness"] = harness_report(
            harness_cases, options.seed, options.profile, options.command != "harness"
        )
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    final_hashes = source_manifest()
    report["source_stable_during_run"] = final_hashes == report["source_hashes"]
    if not report["source_stable_during_run"]:
        report["status"] = "harness_failure"
        report["source_hashes_after"] = final_hashes
    target.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(
        json.dumps(
            {
                "report": str(target),
                "mode": "offline",
                "model_performance_measured": False,
            }
        )
    )
    return 1 if report_has_failures(report) else 0
