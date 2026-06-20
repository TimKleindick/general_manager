"""Run the chat production-readiness loop."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")

import django

django.setup()

from general_manager.chat.evals.fingerprints import build_run_fingerprint
from general_manager.chat.evals.fixtures import setup_large_schema
from general_manager.chat.evals.readiness import (
    ReadinessConfig,
    build_summary,
    compare_summary_if_requested,
    readiness_exit_code,
    write_readiness_artifacts,
)
from general_manager.chat.evals.runner import (
    list_datasets,
    print_report,
    run_eval_suite_sync,
)
from general_manager.chat.evals.traces import EvalTraceWriter
from general_manager.chat.providers.ollama import OllamaProvider
from general_manager.chat.system_prompt import build_system_prompt
from general_manager.chat.tools import get_tool_definitions
from scripts.run_chat_evals import _resolve_ollama_base_url, setup_test_schema


def main() -> None:
    """Run selected readiness gates and write artifacts."""
    args = _parse_args()
    output_dir = Path(args.output_dir)
    trace_path = output_dir / "trace.jsonl"

    if args.run_tests:
        test_result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_chat*.py",
                "tests/integration/test_chat*.py",
            ],
            cwd=PROJECT_ROOT,
            check=False,
        )
        if test_result.returncode != 0:
            sys.exit(1)

    if args.fixture == "large":
        setup_large_schema()
    else:
        setup_test_schema()

    _patch_ollama_provider(model=args.model, base_url=args.base_url)
    config = OllamaProvider._provider_config()
    resolved_base_url, remapped = _resolve_ollama_base_url(str(config["base_url"]))
    if resolved_base_url != config["base_url"]:
        _patch_ollama_provider(base_url=resolved_base_url)
        config = OllamaProvider._provider_config()

    datasets = args.dataset or _datasets_for_gate(args.gate)
    tier = _tier_for_gate(args.gate) if args.tier is None else args.tier
    tags = args.tag
    run_metadata = build_run_fingerprint(
        provider="OllamaProvider",
        model=str(config["model"]),
        fixture=args.fixture,
        datasets=datasets,
        tier=tier,
        tags=tags,
        prompt=build_system_prompt(),
        tool_definitions=get_tool_definitions(),
    )

    print(f"Provider: OllamaProvider ({config['model']})")
    print(f"Base URL: {config['base_url']}")
    if remapped:
        print("Note: remapped localhost Ollama URL for container access")
    print(f"Gate: {args.gate}")
    print(f"Datasets: {datasets}")
    print(f"Tier: {tier}")
    print(f"Artifacts: {output_dir}")
    print()

    results = run_eval_suite_sync(
        OllamaProvider(),
        datasets,
        trace_writer=EvalTraceWriter(trace_path),
        tier=tier,
        tags=tags,
        run_metadata=run_metadata,
        recover_missing_tools=True,
    )
    report = print_report(results, verbose=True)
    summary = build_summary(gate=args.gate, run_metadata=run_metadata, results=results)
    readiness_config = ReadinessConfig(
        gate=args.gate,
        provider="OllamaProvider",
        model=str(config["model"]),
        fixture=args.fixture,
        datasets=datasets,
        tier=tier,
        tags=tags or [],
        output_dir=output_dir,
        baseline_json=Path(args.baseline_json) if args.baseline_json else None,
        fail_on_regression=args.fail_on_regression,
        live=True,
    )
    comparison = compare_summary_if_requested(
        config=readiness_config,
        summary=summary,
    )
    write_readiness_artifacts(
        config=readiness_config,
        summary=summary,
        report=report,
        comparison=comparison,
    )
    print(report)
    if comparison is not None and comparison.messages:
        print()
        print("Baseline comparison:")
        for message in comparison.messages:
            print(f"- {message}")
    sys.exit(
        readiness_exit_code(
            summary=summary,
            comparison=comparison,
            fail_on_regression=args.fail_on_regression,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", choices=["tier0", "demo", "large"], default="demo")
    parser.add_argument("--fixture", choices=["toy", "large"], default=None)
    parser.add_argument("--dataset", action="append", default=None)
    parser.add_argument("--tier", type=int, default=None)
    parser.add_argument("--tag", action="append", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--output-dir",
        default=str(Path(tempfile.gettempdir()) / "gm-chat-readiness"),
    )
    parser.add_argument("--baseline-json", default=None)
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--skip-tests", action="store_false", dest="run_tests")
    parser.set_defaults(run_tests=True)
    args = parser.parse_args()
    if args.fixture is None:
        args.fixture = "large" if args.gate == "large" else "toy"
    return args


def _patch_ollama_provider(
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> None:
    if model is None and base_url is None:
        return
    original = OllamaProvider._provider_config

    @staticmethod  # type: ignore[misc]
    def _patched_config() -> dict:
        cfg = original()
        if model is not None:
            cfg["model"] = model
        if base_url is not None:
            cfg["base_url"] = base_url
        return cfg

    OllamaProvider._provider_config = _patched_config  # type: ignore[assignment]


def _datasets_for_gate(gate: str) -> list[str]:
    if gate == "tier0":
        return list_datasets()
    if gate == "large":
        return ["large_schema"]
    return ["demo_readiness"]


def _tier_for_gate(gate: str) -> int | None:
    if gate == "tier0":
        return 0
    if gate == "large":
        return 2
    if gate == "demo":
        return 1
    return None


if __name__ == "__main__":
    main()
