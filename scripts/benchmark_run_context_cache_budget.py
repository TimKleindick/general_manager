"""Benchmark run-context cache budget accounting overhead."""

from __future__ import annotations

import argparse
import gc
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")

import django
from django.test import override_settings

from general_manager.cache.run_context import CalculationRunContext

DEFAULT_CAP_BYTES = 1024 * 1024 * 1024
POSITIVE_INTEGER_ERROR_MESSAGE = "must be a positive integer"


def positive_integer(value: str) -> int:
    """Parse an integer command-line value greater than zero."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(POSITIVE_INTEGER_ERROR_MESSAGE) from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError(POSITIVE_INTEGER_ERROR_MESSAGE)
    return parsed


def measure(callback: Callable[[], None], repeats: int) -> float:
    """Return the median runtime in seconds for repeated callback runs."""
    samples: list[float] = []
    for _ in range(repeats):
        gc.collect()
        gc.disable()
        started = perf_counter()
        try:
            callback()
        finally:
            samples.append(perf_counter() - started)
            gc.enable()
    return median(samples)


def run_hits(*, cap_bytes: int | None, count: int) -> None:
    """Exercise hot-key run-context cache hits."""
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": cap_bytes}),
        CalculationRunContext() as context,
    ):
        context.set("hot-key", 1)
        for _ in range(count):
            context.get("hot-key")


def run_scalar_inserts(*, cap_bytes: int | None, count: int) -> None:
    """Exercise scalar run-context cache inserts."""
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": cap_bytes}),
        CalculationRunContext() as context,
    ):
        for value in range(count):
            context.set(value, value)


def run_container_inserts(
    *,
    cap_bytes: int | None,
    payloads: tuple[list[int], ...],
) -> None:
    """Exercise run-context cache inserts of prebuilt container payloads."""
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": cap_bytes}),
        CalculationRunContext() as context,
    ):
        for key, payload in enumerate(payloads):
            context.set(key, payload)


def parse_args() -> argparse.Namespace:
    """Parse benchmark workload sizes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=positive_integer, default=5)
    parser.add_argument("--hits", type=positive_integer, default=300_000)
    parser.add_argument("--scalar-inserts", type=positive_integer, default=20_000)
    parser.add_argument("--container-inserts", type=positive_integer, default=200)
    parser.add_argument("--container-width", type=positive_integer, default=2_000)
    return parser.parse_args()


def print_result(name: str, uncapped: float, capped: float) -> None:
    """Print one stable benchmark result row."""
    print(f"{name:<20}{uncapped:>12.6f}{capped:>12.6f}{capped / uncapped:>10.2f}x")


def main() -> None:
    """Run each workload with uncapped and capped run-context caches."""
    args = parse_args()
    django.setup()
    payloads = tuple(
        list(range(args.container_width)) for _ in range(args.container_inserts)
    )

    print(f"{'workload':<20}{'uncapped_s':>12}{'capped_s':>12}{'ratio':>11}")

    uncapped = measure(lambda: run_hits(cap_bytes=None, count=args.hits), args.repeats)
    capped = measure(
        lambda: run_hits(cap_bytes=DEFAULT_CAP_BYTES, count=args.hits), args.repeats
    )
    print_result("hits", uncapped, capped)

    uncapped = measure(
        lambda: run_scalar_inserts(cap_bytes=None, count=args.scalar_inserts),
        args.repeats,
    )
    capped = measure(
        lambda: run_scalar_inserts(
            cap_bytes=DEFAULT_CAP_BYTES,
            count=args.scalar_inserts,
        ),
        args.repeats,
    )
    print_result("scalar_inserts", uncapped, capped)

    uncapped = measure(
        lambda: run_container_inserts(cap_bytes=None, payloads=payloads),
        args.repeats,
    )
    capped = measure(
        lambda: run_container_inserts(
            cap_bytes=DEFAULT_CAP_BYTES,
            payloads=payloads,
        ),
        args.repeats,
    )
    print_result("container_inserts", uncapped, capped)


if __name__ == "__main__":
    main()
