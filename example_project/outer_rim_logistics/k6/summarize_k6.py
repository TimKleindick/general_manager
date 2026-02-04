from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def summarize(path: Path) -> str:
    events = load_events(path)
    points_by_op: dict[str, list[float]] = defaultdict(list)
    total_req = 0
    failed_req = 0

    for event in events:
        if event.get("type") != "Point":
            continue
        metric = event.get("metric")
        if metric != "http_req_duration":
            continue
        tags = event.get("data", {}).get("tags", {})
        if tags.get("kind") != "graphql":
            continue
        op = tags.get("op", "unknown")
        value = event.get("data", {}).get("value")
        if isinstance(value, (int, float)):
            points_by_op[op].append(float(value))
            total_req += 1
        if tags.get("status") == "error" or tags.get("error") == "true":
            failed_req += 1

    lines = []
    lines.append(f"file: {path}")
    lines.append(f"graphql requests: {total_req}")
    if total_req:
        lines.append(f"estimated error rate: {failed_req / total_req:.4f}")

    for op, values in sorted(points_by_op.items()):
        values.sort()
        p95 = values[int(0.95 * (len(values) - 1))]
        p99 = values[int(0.99 * (len(values) - 1))]
        lines.append(f"{op}: count={len(values)} p95={p95 * 1000:.2f}ms p99={p99 * 1000:.2f}ms")

    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python summarize_k6.py <k6-json-file>")
        return 1
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"file not found: {path}")
        return 1
    print(summarize(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
