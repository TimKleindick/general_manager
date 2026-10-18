#!/usr/bin/env python3
"""Run the chat eval suite against a live LLM provider.

Usage:
    python scripts/run_chat_evals.py                          # all datasets, default model
    python scripts/run_chat_evals.py --dataset basic_queries   # single dataset
    python scripts/run_chat_evals.py --model qwen3.5:9b        # different model
    python scripts/run_chat_evals.py --base-url http://host.docker.internal:11434
    python scripts/run_chat_evals.py -v                        # verbose failure details
    python scripts/run_chat_evals.py -v --show-chat          # verbose + stream conversations to stdout
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# Ensure the project root is on sys.path so test settings are importable.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")

import django

django.setup()


from general_manager.chat.evals.runner import (
    list_datasets,
    print_report,
    run_eval_suite_sync,
    select_eval_cases,
)
from general_manager.chat.evals.fingerprints import build_run_fingerprint
from general_manager.chat.evals.fixtures import setup_large_schema, setup_toy_schema
from general_manager.chat.evals.traces import EvalTraceWriter
from general_manager.chat.providers.ollama import OllamaProvider
from general_manager.chat.system_prompt import build_system_prompt
from general_manager.chat.tools import get_tool_definitions


# ---------------------------------------------------------------------------
# Test schema setup
# ---------------------------------------------------------------------------


def setup_test_schema() -> None:
    """Register the canonical packaged toy fixture for source-checkout recipes."""
    setup_toy_schema()


def _patch_ollama_provider(
    *, model: str | None = None, base_url: str | None = None
) -> None:
    """Bind each override to the configuration callable it actually wraps."""
    if model is None and base_url is None:
        return
    original = OllamaProvider._provider_config

    @staticmethod  # type: ignore[misc]
    def _patched_config(configured: Mapping[str, Any] | None = None) -> dict[str, Any]:
        cfg = original(configured)
        if model is not None:
            cfg["model"] = model
        if base_url is not None:
            cfg["base_url"] = base_url
        return cfg

    OllamaProvider._provider_config = _patched_config  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        default=None,
        help=f"Run a single dataset ({', '.join(list_datasets())})",
    )
    parser.add_argument(
        "--fixture",
        choices=["toy", "large"],
        default="toy",
        help="Eval schema fixture to register before running cases.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Ollama model name (default: from settings, typically gemma4:e4b)",
    )
    parser.add_argument(
        "--tier",
        type=int,
        default=None,
        help="Run only cases in this tier.",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=None,
        help="Run only cases containing this tag. May be passed multiple times.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=(
            "Ollama base URL (default: from settings; in devcontainers localhost "
            "is automatically remapped to host.docker.internal when available)"
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed failure information",
    )
    parser.add_argument(
        "--show-chat",
        "--stdout",
        action="store_true",
        dest="show_chat",
        help="Stream each eval conversation to stdout while it runs",
    )
    parser.add_argument(
        "--trace-jsonl",
        default=None,
        help="Write per-case eval traces to the given JSONL file.",
    )
    args = parser.parse_args()
    dataset_names = [args.dataset] if args.dataset else None
    selection = select_eval_cases(dataset_names, tier=args.tier, tags=args.tag)
    if not selection.manifest()["cases"]:
        parser.error("No eval cases selected; the run was not evaluated.")

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
    selected_datasets = list(selection.datasets)
    run_metadata = build_run_fingerprint(
        provider="OllamaProvider",
        model=str(config["model"]),
        fixture=str(args.fixture),
        datasets=selected_datasets,
        tier=args.tier,
        tags=args.tag,
        prompt=build_system_prompt(),
        tool_definitions=get_tool_definitions(),
        selection_manifest=selection.manifest(),
    )

    print(f"Provider: OllamaProvider ({config['model']})")
    print(f"Base URL: {config['base_url']}")
    if remapped:
        print("Note: remapped localhost Ollama URL for container access")
    print(f"Datasets: {selected_datasets}")
    print()

    provider = OllamaProvider()
    trace_writer = EvalTraceWriter(args.trace_jsonl) if args.trace_jsonl else None
    results = run_eval_suite_sync(
        provider,
        stream=sys.stdout if args.show_chat else None,
        trace_writer=trace_writer,
        selection=selection,
        run_metadata=run_metadata,
    )
    report = print_report(results, verbose=args.verbose)
    print(report)

    if not results or not all(r.passed for r in results):
        sys.exit(1)


def _resolve_ollama_base_url(base_url: str) -> tuple[str, bool]:
    """Map localhost Ollama URLs to the Docker host when needed."""
    parsed = urlsplit(base_url)
    hostname = parsed.hostname
    if hostname not in {"127.0.0.1", "localhost"}:
        return base_url, False
    if not _host_port_reachable(hostname, parsed.port or 11434):
        docker_host = "host.docker.internal"
        if _host_port_reachable(docker_host, parsed.port or 11434):
            netloc = docker_host
            if parsed.port is not None:
                netloc = f"{netloc}:{parsed.port}"
            return urlunsplit(
                (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
            ), True
    return base_url, False


def _host_port_reachable(host: str, port: int) -> bool:
    """Check whether a TCP host/port pair is reachable quickly."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    main()
