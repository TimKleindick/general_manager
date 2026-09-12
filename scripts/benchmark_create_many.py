"""Measure canonical ORM creation against bounded ``create_many``.

The script creates a temporary Django test database through Django's test
runner. Configure ``GENERAL_MANAGER_TEST_DATABASE`` and its connection
variables as documented in ``tests.test_settings``. Search dispatch is
patched identically for both paths so broker/backend latency does not dominate
the ORM comparison; search configuration discovery and invalidation planning
remain enabled.

Example::

    python scripts/benchmark_create_many.py --rows 1000 --batch-size 100 --repeats 3
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import tracemalloc
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable, Iterator
from unittest.mock import patch
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")

import django

django.setup()

from django import get_version as django_version
from django.contrib.auth import get_user_model
from django.db import connection, models
from django.test import override_settings
from django.test.runner import DiscoverRunner

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import IndexConfig
from general_manager.workflow.event_registry import (
    InMemoryEventRegistry,
    configure_event_registry,
    get_event_registry,
)
from general_manager.workflow.signal_bridge import (
    connect_workflow_signal_bridge,
    disconnect_workflow_signal_bridge,
)
from tests.utils.database import create_test_models, drop_test_models


@dataclass(frozen=True)
class Metrics:
    """One independent measurement pass for one creation path."""

    elapsed_seconds: float
    sql_count: int
    peak_python_bytes: int


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=3)
    return parser


def _validate_options(rows: int, batch_size: int, repeats: int) -> None:
    if rows <= 0 or batch_size <= 0 or repeats <= 0:
        raise SystemExit


def _noop_search_dispatch(*_args: object, **_kwargs: object) -> int:
    """Keep external search transport out of the local benchmark."""
    return 0


@contextmanager
def _fresh_workflow_registry(
    record_event: Callable[[object], None],
) -> Iterator[None]:
    """Install a fresh deduplicating registry for one measured execution."""
    prior_registry = get_event_registry()
    registry = InMemoryEventRegistry()
    registry.register(
        "manager_created",
        handler=record_event,
        registration_id="create-many-benchmark-manager-created",
    )
    connect_workflow_signal_bridge(registry=registry)
    try:
        yield
    finally:
        disconnect_workflow_signal_bridge()
        configure_event_registry(prior_registry)


def _payloads(
    rows: int,
    owner: models.Model,
    prefix: str,
) -> Iterator[dict[str, object]]:
    """Yield the same bounded payload stream for both compared paths."""
    for index in range(rows):
        yield {"name": f"{prefix}-{index}", "owner": owner}


def _clear_rows(model: type[models.Model]) -> None:
    model.objects.all().delete()
    model.history.all().delete()  # type: ignore[attr-defined]
    from general_manager.search.models import SearchIndexState

    SearchIndexState.objects.all().delete()


def _run_path(
    path: str,
    manager: type[GeneralManager],
    rows: int,
    batch_size: int,
    owner: models.Model,
    creator_id: int,
    prefix: str,
) -> None:
    payloads = _payloads(rows, owner, prefix)
    if path == "canonical":
        for payload in payloads:
            manager.create(
                creator_id=creator_id,
                history_comment="create-many benchmark",
                ignore_permission=True,
                **payload,
            )
        return
    for _result in manager.create_many(
        payloads,
        creator_id=creator_id,
        history_comment="create-many benchmark",
        ignore_permission=True,
        batch_size=batch_size,
    ):
        pass


def _assert_semantics(
    model: type[models.Model],
    rows: int,
    owner_id: int,
    workflow_event_count: int,
    creator_id: int,
) -> None:
    stored = model.objects.order_by("name")
    assert stored.count() == rows
    assert stored.values("name").distinct().count() == rows
    assert set(stored.values_list("owner_id", flat=True)) == {owner_id}
    assert set(stored.values_list("changed_by_id", flat=True)) == {creator_id}
    history = model.history  # type: ignore[attr-defined]
    assert history.count() == rows
    assert set(history.values_list("history_user_id", flat=True)) == {creator_id}
    assert set(history.values_list("history_change_reason", flat=True)) == {
        "create-many benchmark"
    }
    assert workflow_event_count == rows


def _measure(
    metric: str,
    execute: Callable[[], None],
) -> float | int:
    if metric == "elapsed":
        started = perf_counter()
        execute()
        return perf_counter() - started
    if metric == "sql":
        count = 0

        def wrapper(
            execute_sql: Callable[..., object],
            sql: str,
            params: object,
            many: bool,
            context: object,
        ) -> object:
            nonlocal count
            count += 1
            return execute_sql(sql, params, many, context)

        with connection.execute_wrapper(wrapper):
            execute()
        return count
    tracemalloc.start()
    try:
        execute()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def _mean(values: list[float | int]) -> float:
    return float(sum(values)) / len(values)


def _run(
    *,
    rows: int,
    batch_size: int,
    repeats: int,
    manager: type[GeneralManager],
    model: type[models.Model],
    owner: models.Model,
    creator_id: int,
    reset_workflow_events: Callable[[], None],
    workflow_event_count: Callable[[], int],
    record_workflow_event: Callable[[object], None],
) -> dict[str, dict[str, Metrics]]:
    observations: dict[str, dict[str, Metrics]] = {"canonical": {}, "create_many": {}}
    for metric in ("elapsed", "sql", "memory"):
        values_by_path: dict[str, list[float | int]] = {
            "canonical": [],
            "create_many": [],
        }
        for repeat in range(repeats):
            path_order = (
                ("canonical", "create_many")
                if repeat % 2 == 0
                else ("create_many", "canonical")
            )
            for path in path_order:
                _clear_rows(model)
                reset_workflow_events()
                prefix = f"run-{metric}-{repeat}"

                def execute(path: str = path, prefix: str = prefix) -> None:
                    _run_path(
                        path,
                        manager,
                        rows,
                        batch_size,
                        owner,
                        creator_id,
                        prefix,
                    )

                with (
                    _fresh_workflow_registry(record_workflow_event),
                    override_settings(DEBUG=False),
                    patch(
                        "general_manager.search.invalidation.dispatch_index_manager_batch",
                        new=_noop_search_dispatch,
                    ),
                ):
                    value = _measure(metric, execute)
                    _assert_semantics(
                        model,
                        rows,
                        owner.pk,
                        workflow_event_count(),
                        creator_id,
                    )
                values_by_path[path].append(value)
        for path, values in values_by_path.items():
            elapsed = _mean(values) if metric == "elapsed" else 0.0
            sql_count = int(_mean(values)) if metric == "sql" else 0
            peak_bytes = int(_mean(values)) if metric == "memory" else 0
            observations[path][metric] = Metrics(elapsed, sql_count, peak_bytes)
    return observations


def _print_report(
    observations: dict[str, dict[str, Metrics]],
    *,
    rows: int,
    batch_size: int,
    repeats: int,
) -> None:
    elapsed_canonical = observations["canonical"]["elapsed"].elapsed_seconds
    elapsed_many = observations["create_many"]["elapsed"].elapsed_seconds
    ratio = elapsed_canonical / elapsed_many if elapsed_many else float("inf")
    print(f"python={platform.python_version()} django={django_version()}")
    print(
        f"database_vendor={connection.vendor} "
        f"database_version={connection.get_database_version()}"
    )
    print(f"rows={rows} batch_size={batch_size} repeats={repeats}")
    for path in ("canonical", "create_many"):
        metrics = observations[path]
        print(
            f"{path}: elapsed_seconds={metrics['elapsed'].elapsed_seconds:.6f} "
            f"sql_count={metrics['sql'].sql_count} "
            f"peak_python_bytes={metrics['memory'].peak_python_bytes}"
        )
    print(f"elapsed_ratio_canonical_over_create_many={ratio:.3f}")
    print("peak_python_bytes excludes database/server memory.")
    print("Timing is diagnostic only; no threshold is enforced.")


def main() -> None:
    """Create a safe test database, run both paths, and print diagnostics."""
    options = _parser().parse_args()
    _validate_options(options.rows, options.batch_size, options.repeats)

    runner = DiscoverRunner(verbosity=0, interactive=False)
    old_config = runner.setup_databases()
    created_models: list[type[models.Model]] = []
    manager_classes_before = list(GeneralManagerMeta.all_classes)
    try:
        user = get_user_model()

        class BenchItem(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=200, unique=True)
                owner = models.ForeignKey(user, on_delete=models.CASCADE)
                changed_by = models.ForeignKey(
                    user,
                    on_delete=models.PROTECT,
                    null=True,
                    blank=True,
                    related_name="+",
                )

        class BenchSearchConfig:
            indexes = (IndexConfig(name="global", fields=("name",)),)

        BenchItem.SearchConfig = BenchSearchConfig  # type: ignore[attr-defined]
        model = BenchItem.Interface._model  # type: ignore[misc]
        created_models = create_test_models(
            connection,
            (model, model.history.model),
        )
        GeneralManagerMeta.all_classes = [BenchItem]
        creator = user.objects.create_user(
            username=f"create-many-benchmark-{uuid4().hex[:10]}"
        )
        workflow_event_count = 0

        def record_workflow_event(_event: object) -> None:
            nonlocal workflow_event_count
            workflow_event_count += 1

        def reset_workflow_events() -> None:
            nonlocal workflow_event_count
            workflow_event_count = 0

        def get_workflow_event_count() -> int:
            return workflow_event_count

        observations = _run(
            rows=options.rows,
            batch_size=options.batch_size,
            repeats=options.repeats,
            manager=BenchItem,
            model=model,
            owner=creator,
            creator_id=creator.pk,
            reset_workflow_events=reset_workflow_events,
            workflow_event_count=get_workflow_event_count,
            record_workflow_event=record_workflow_event,
        )
        _print_report(
            observations,
            rows=options.rows,
            batch_size=options.batch_size,
            repeats=options.repeats,
        )
    finally:
        if created_models:
            with connection.schema_editor() as editor:
                drop_test_models(editor, reversed(created_models))
        GeneralManagerMeta.all_classes = manager_classes_before
        runner.teardown_databases(old_config)


if __name__ == "__main__":
    main()
