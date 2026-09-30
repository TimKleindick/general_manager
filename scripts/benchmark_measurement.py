"""Reproducible Measurement workloads; timings exclude imports and fixture setup.

Run each sample in a fresh process, alternating --source checkouts for A/B runs.
Cold means empty GM metadata caches after setup, not a cold OS or Pint registry.
Profile and memory modes are separate diagnostics, never timing samples.
"""

from __future__ import annotations

import argparse
import cProfile
import gc
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import pstats
import sys
from time import perf_counter
import tracemalloc
from decimal import Decimal
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--case",
        choices=(
            "zeros",
            "construction",
            "scalar",
            "mixed_arithmetic",
            "aggregation",
            "price",
            "quantity",
            "first_quantity",
            "construction_quantity",
        ),
        required=True,
    )
    parser.add_argument("--count", type=int, default=12_000)
    parser.add_argument("--mode", choices=("cold", "warm"), default="warm")
    parser.add_argument(
        "--diagnostic", choices=("timing", "profile", "memory"), default="timing"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.source.resolve() / "src"))
    import general_manager.measurement.measurement as module
    import numpy as np

    measurement = module.Measurement
    values: tuple[Any, ...] = (
        0,
        42,
        -7,
        1.25,
        "12.3400",
        Decimal("123456789.01234567890123456789"),
        Decimal("-0.00"),
        Decimal("1e-20"),
        np.int64(17),
        np.float64(2.125),
    )
    units = ("kg", "EUR", "count", "EUR/kg", "kg / m ** 3", "percent", "", "degC")
    inputs = tuple((values[i % len(values)], units[i % len(units)]) for i in range(80))
    weights = tuple(measurement(Decimal(i) / 8, "kg") for i in range(-8, 24))
    prices = tuple(measurement(Decimal(i) / 4, "EUR/kg") for i in range(1, 17))
    savings = (None, Decimal("0"), Decimal("0.05"), Decimal("-0.025"))
    quantity_count = args.count if args.case == "first_quantity" else len(inputs)
    quantities = tuple(
        measurement(*inputs[i % len(inputs)]) for i in range(quantity_count)
    )
    scalar = Decimal("1.125")
    grams = measurement("125.50", "g")
    length = measurement(2, "m")
    dataset_sha256 = hashlib.sha256(
        repr((inputs, weights, prices, savings, scalar, grams, length)).encode()
    ).hexdigest()

    def workload() -> list[Any]:
        if args.case == "zeros":
            return [
                measurement(0, ("EUR", "kg", "count")[i % 3]) for i in range(args.count)
            ]
        if args.case == "construction":
            return [measurement(*inputs[i % len(inputs)]) for i in range(args.count)]
        if args.case == "scalar":
            return [(weights[i % 32] * scalar) / 3 for i in range(args.count)]
        if args.case == "mixed_arithmetic":
            return [
                ((weights[i % 32] + grams).to("g"), weights[i % 32] / length)
                for i in range(args.count)
            ]
        if args.case == "aggregation":
            return [sum(weights, measurement(0, "kg")) for _ in range(args.count)]
        if args.case == "quantity":
            return [quantities[i % len(quantities)].quantity for i in range(args.count)]
        if args.case == "first_quantity":
            return [value.quantity for value in quantities]
        if args.case == "construction_quantity":
            return [
                measurement(*inputs[i % len(inputs)]).quantity
                for i in range(args.count)
            ]
        results = []
        for i in range(args.count):
            # Six independent result fields, including required zeros. This is
            # a GM-only price-like workload, not the private KnowledgeHub core.
            fields = [measurement(0, "EUR") for _ in range(6)]
            fields[0] = fields[0] + prices[i % 16] * weights[i % 32]
            rate = savings[i % 4]
            if rate is not None:
                fields[1] = fields[0] * rate
            fields[2] = fields[0] - fields[1]
            fields[3] = fields[2] * (i % 101)
            results.append(tuple(fields))
        return results

    def fingerprint(results: list[Any]) -> str:
        digest = hashlib.sha256()
        for row in results:
            for value in row if isinstance(row, tuple) else (row,):
                unit = (
                    value.units
                    if args.case
                    in ("quantity", "first_quantity", "construction_quantity")
                    else value.unit
                )
                digest.update(f"{value.magnitude!s}|{unit!s}\n".encode())
        return digest.hexdigest()

    for value in vars(module).values():
        clear = getattr(value, "cache_clear", None)
        if clear is not None:
            clear()
    warm_digest = None
    if args.mode == "warm":
        warm_result = workload()
        warm_digest = fingerprint(warm_result)
        del warm_result
        if args.case == "first_quantity":
            # Warm metadata, but every timed access must still be the first
            # access to its Measurement. Construction remains outside timing.
            quantities = tuple(
                measurement(*inputs[i % len(inputs)]) for i in range(quantity_count)
            )
    gc.collect()
    profile = cProfile.Profile() if args.diagnostic == "profile" else None
    if args.diagnostic == "memory":
        tracemalloc.start()
    if profile is not None:
        profile.enable()
    started = perf_counter()
    result = workload()
    elapsed = perf_counter() - started
    if profile is not None:
        profile.disable()
    memory: dict[str, int] = {}
    if args.diagnostic == "memory":
        current, peak = tracemalloc.get_traced_memory()
        memory.update(retained_bytes=current, peak_bytes=peak)
        memory["retained_blocks"] = sum(
            s.count for s in tracemalloc.take_snapshot().statistics("filename")
        )
        tracemalloc.stop()
    observation: dict[str, Any] = {
        "case": args.case,
        "count": args.count,
        "mode": args.mode,
        "diagnostic": args.diagnostic,
        "elapsed_seconds": elapsed,
        "source": str(args.source.resolve()),
        "module_file": module.__file__,
        "source_sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dataset_sha256": dataset_sha256,
        "python": sys.version,
        "platform": platform.platform(),
        "versions": {
            p: importlib.metadata.version(p)
            for p in ("GeneralManager", "Pint", "numpy")
        },
    }
    observation.update(memory)
    observation["result_sha256"] = fingerprint(result)
    if warm_digest is not None and warm_digest != observation["result_sha256"]:
        message = "cold/warm result mismatch"
        raise RuntimeError(message)
    if profile is not None:
        profile.dump_stats(str(args.output.with_suffix(".prof")))
        # The runtime mapping is omitted from some versions of the pstats stub.
        statistics: dict[
            tuple[str, int, str], tuple[int, int, float, float, object]
        ] = vars(pstats.Stats(profile))["stats"]
        records = [
            {
                "function": f"{key[0]}:{key[1]}:{key[2]}",
                "calls": entry[1],
                "self_seconds": entry[2],
                "cumulative_seconds": entry[3],
            }
            for key, entry in statistics.items()
        ]
        observation["profile"] = sorted(
            records, key=lambda r: r["self_seconds"], reverse=True
        )
    args.output.write_text(json.dumps(observation, indent=2) + "\n")
    print(json.dumps({k: v for k, v in observation.items() if k != "profile"}))


if __name__ == "__main__":
    main()
