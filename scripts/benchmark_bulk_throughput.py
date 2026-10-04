"""Reproducibly compare repeated create with the supported bulk-create paths.

The harness measures a legacy repeated ``create`` call, ``create_many`` with
bulk SQL disabled, and, when requested, the supported optimized bulk-SQL
path.  It can also run unchanged code from a frozen source archive to retain a
fair baseline.  Each path exercises unique fields, shared foreign keys, a
local rule, permissions, simple-history records, workflow delivery, search
invalidation planning, a real Django-Redis cache write, and a real Channels
Redis group notification.

External search indexing is replaced with a no-op only after GeneralManager has
formed both direct and related invalidation plans.  The script reports that
limitation explicitly.  Every timed metric has a fresh database/cache/channel
fixture and correctness checks run after the measured call.  When the current
public batch-refresh helper is available, the cache callback runs in the
transaction and the notification callback runs on commit; frozen legacy
baselines use equivalent ``post_data_change`` receiver behaviour.

The default connection targets the dedicated local services:

    PostgreSQL: 127.0.0.1:55497, postgres/general_manager, database general_manager
    Redis:      127.0.0.1:56497 (cache DB 15, Channels DB 14)

Example baseline recording::

    .venv/bin/python scripts/benchmark_bulk_throughput.py \
      --rows 100 --batch-size 100 --existing-rows 1000 --repeats 2 \
      --output-prefix /private/tmp/gm-throughput-baseline

The Django test runner creates a disposable ``test_general_manager`` database
on that PostgreSQL service; the configured Redis logical databases are flushed
between passes.  Do not point these settings at a shared Redis service.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import hashlib
import importlib.util
import json
import os
import platform
import pstats
import sys
import tracemalloc
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, ClassVar, Literal, cast
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# A frozen baseline run prepends its archived ``src`` through PYTHONPATH.  Keep
# that package ahead of the working tree while still exposing our settings
# module from the working-tree ``scripts`` namespace.
if str(PROJECT_ROOT) not in sys.path:
    if importlib.util.find_spec("general_manager") is None:
        sys.path.insert(0, str(PROJECT_ROOT))
    else:
        sys.path.append(str(PROJECT_ROOT))

PathKind = Literal[
    "repeated_create",
    "create_many_disabled",
    "optimized_bulk_sql",
]
MetricKind = Literal["elapsed", "profile", "sql_redis", "memory"]


@dataclass(frozen=True)
class PassMetrics:
    """Metrics from one isolated execution of one path."""

    elapsed_seconds: float | None = None
    sql_commands: int | None = None
    redis_commands: int | None = None
    redis_command_breakdown: dict[str, int] | None = None
    peak_python_bytes: int | None = None
    profile_total_calls: int | None = None
    profile_primitive_calls: int | None = None
    profile_total_seconds: float | None = None
    profile_path: str | None = None


@dataclass(frozen=True)
class SemanticResult:
    """Correctness facts intentionally checked outside a measurement window."""

    rows_created: int
    history_rows: int
    permission_checks: int
    workflow_events: int
    applied_cache_refreshes: int
    applied_cache_identifiers: int
    external_notifications: int
    related_search_plans: int
    notification_delivered: bool


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--existing-rows", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=Path("/private/tmp/gm-throughput-baseline"),
        help="Prefix for JSON report and cProfile .pstats files.",
    )
    parser.add_argument(
        "--redis-url",
        default="redis://127.0.0.1:56497/15",
        help="Dedicated Django-cache Redis URL (default: %(default)s).",
    )
    parser.add_argument(
        "--channel-redis-url",
        default="redis://127.0.0.1:56497/14",
        help="Dedicated Channels Redis URL (default: %(default)s).",
    )
    parser.add_argument(
        "--require-optimized-bulk-sql",
        action="store_true",
        help=(
            "Run an unmeasured preflight that requires the opt-in configuration, "
            "a QuerySet bulk insert, and no canonical create calls."
        ),
    )
    parser.add_argument(
        "--include-optimized-path",
        action="store_true",
        help="Include the enabled BulkCreate path without enforcing its preflight.",
    )
    parser.add_argument(
        "--metrics",
        nargs="+",
        choices=("elapsed", "profile", "sql_redis", "memory"),
        default=("elapsed", "profile", "sql_redis", "memory"),
        help="Independent passes to run (default: all four).",
    )
    parser.add_argument(
        "--append-report",
        action="store_true",
        help="Merge selected metric passes into an existing JSON report prefix.",
    )
    return parser


def _validate_options(options: argparse.Namespace) -> None:
    for option in ("rows", "batch_size", "repeats"):
        if getattr(options, option) <= 0:
            raise SystemExit(  # noqa: TRY003 - CLI diagnostics are user-facing.
                f"--{option.replace('_', '-')} must be positive"
            )
    if options.existing_rows < 0:
        raise SystemExit(  # noqa: TRY003 - CLI diagnostics are user-facing.
            "--existing-rows must be zero or positive"
        )


def _configure_environment(options: argparse.Namespace) -> None:
    """Set safe dedicated-service defaults before Django imports settings."""
    defaults = {
        "GENERAL_MANAGER_TEST_DATABASE": "postgresql",
        "GENERAL_MANAGER_TEST_DATABASE_NAME": "general_manager",
        "GENERAL_MANAGER_TEST_DATABASE_USER": "postgres",
        "GENERAL_MANAGER_TEST_DATABASE_PASSWORD": "general_manager",
        "GENERAL_MANAGER_TEST_DATABASE_HOST": "127.0.0.1",
        "GENERAL_MANAGER_TEST_DATABASE_PORT": "55497",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    os.environ["GENERAL_MANAGER_BENCHMARK_REDIS_URL"] = options.redis_url
    os.environ["GENERAL_MANAGER_BENCHMARK_CHANNEL_REDIS_URL"] = (
        options.channel_redis_url
    )
    # Canonical paths emit one external notification per row.  Size the real
    # channel layer before Django constructs it so delivery checks never pass
    # after Redis silently drops a full channel queue.
    os.environ["GENERAL_MANAGER_BENCHMARK_CHANNEL_CAPACITY"] = str(
        max(options.rows + 10, 100)
    )
    # The repeated path can take longer than Channels Redis's 60-second
    # default expiry before the post-measurement delivery drain begins.
    os.environ.setdefault("GENERAL_MANAGER_BENCHMARK_CHANNEL_EXPIRY", "3600")
    os.environ["DJANGO_SETTINGS_MODULE"] = "scripts.benchmark_bulk_throughput_settings"


def _positive_value(item: Any) -> bool:
    """A local rule whose inputs are entirely row/shared-FK data."""
    return item.value >= 0 and item.owner_id is not None


def _path_order(repeat: int, *, include_optimized: bool) -> tuple[PathKind, ...]:
    paths: tuple[PathKind, ...] = (
        "repeated_create",
        "create_many_disabled",
    )
    if include_optimized:
        paths += ("optimized_bulk_sql",)
    return paths if repeat % 2 == 0 else tuple(reversed(paths))


def _mean(values: list[int | float]) -> float:
    return float(sum(values)) / len(values)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _loaded_source_hash(general_manager: Any) -> str:
    """Keep the historical manager-module fingerprint for report comparison."""
    source = (
        Path(general_manager.__file__).resolve().parent
        / "manager"
        / "general_manager.py"
    )
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _loaded_package_tree_hash(general_manager: Any) -> str:
    """Fingerprint every loaded GeneralManager Python source file and its path."""
    package_root = Path(general_manager.__file__).resolve().parent
    digest = hashlib.sha256()
    source_paths = sorted(path for path in package_root.rglob("*.py") if path.is_file())
    for source_path in source_paths:
        relative_path = source_path.relative_to(package_root).as_posix().encode()
        contents = source_path.read_bytes()
        digest.update(len(relative_path).to_bytes(8, byteorder="big"))
        digest.update(relative_path)
        digest.update(len(contents).to_bytes(8, byteorder="big"))
        digest.update(contents)
    return digest.hexdigest()


def _harness_source_hash() -> str:
    """Identify the harness and its settings independently of the loaded package."""
    script_path = Path(__file__).resolve()
    digest = hashlib.sha256()
    for path in (
        script_path,
        script_path.with_name("benchmark_bulk_throughput_settings.py"),
    ):
        contents = path.read_bytes()
        digest.update(path.name.encode())
        digest.update(len(contents).to_bytes(8, byteorder="big"))
        digest.update(contents)
    return digest.hexdigest()


def _redact_redis_url(url: str) -> str:
    """Retain endpoint identity without serializing Redis authentication secrets."""
    parts = urlsplit(url)
    query = urlencode(
        [
            (
                key,
                "REDACTED"
                if any(
                    secret in key.lower()
                    for secret in (
                        "password",
                        "username",
                        "token",
                        "credential",
                        "secret",
                    )
                )
                else value,
            )
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit(
        (parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, query, "")
    )


def main() -> None:
    options = _parser().parse_args()
    _validate_options(options)
    _configure_environment(options)

    import django

    django.setup()

    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from django import get_version as django_version
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.core.cache import cache
    from django.db import connection, models
    from django.test import override_settings
    from django.test.runner import DiscoverRunner
    import general_manager
    from general_manager.api.property import graph_ql_property
    from general_manager.cache.signals import post_data_change
    from general_manager.interface import DatabaseInterface
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta
    from general_manager.permission.manager_based_permission import (
        ManagerBasedPermission,
    )
    from general_manager.rule.rule import Rule
    from general_manager.search.config import IndexConfig, SearchInvalidationRule
    from general_manager.search.models import SearchIndexState
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

    connect_batch_refresh_receiver: Callable[..., Any] | None
    bulk_create_eligibility: Callable[[Any], Any] | None
    try:
        from general_manager.cache.batch_refresh import (
            connect_batch_refresh_receiver as batch_refresh_connector,
        )
        from general_manager.interface.capabilities.orm.bulk import (
            bulk_create_eligibility as eligibility_checker,
        )

        connect_batch_refresh_receiver = batch_refresh_connector
        bulk_create_eligibility = eligibility_checker
    except ImportError:
        # The immutable pre-optimization archive does not provide the public
        # helper.  Its baseline intentionally keeps the legacy signal receiver.
        connect_batch_refresh_receiver = None
        bulk_create_eligibility = None

    if connection.vendor != "postgresql":
        raise RuntimeError(  # noqa: TRY003 - startup diagnostics are user-facing.
            "This benchmark requires PostgreSQL. Set GENERAL_MANAGER_TEST_DATABASE="
            "postgresql and the dedicated service connection variables."
        )

    runner = DiscoverRunner(verbosity=0, interactive=False)
    old_config = runner.setup_databases()
    created_models: list[type[models.Model]] = []
    managers_before = list(GeneralManagerMeta.all_classes)
    try:
        user_model = get_user_model()

        class BenchTarget(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                label = models.CharField(max_length=120, unique=True)

            class SearchConfig:
                indexes = (IndexConfig(name="global", fields=("label",)),)

        permission_checks = [0]

        class BenchItem(GeneralManager):
            __module__ = "general_manager.models"

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=160, unique=True)
                external_id = models.CharField(max_length=160, unique=True)
                value = models.IntegerField(default=0)
                owner = models.ForeignKey(user_model, on_delete=models.CASCADE)
                target = models.ForeignKey(
                    BenchTarget.Interface._model,  # type: ignore[misc]
                    on_delete=models.CASCADE,
                )
                changed_by = models.ForeignKey(
                    user_model,
                    on_delete=models.PROTECT,
                    null=True,
                    blank=True,
                    related_name="+",
                )

                class Meta:
                    rules: ClassVar[list[Rule[Any]]] = [Rule(_positive_value)]

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

                @classmethod
                def check_create_permission(
                    cls,
                    data: dict[str, object],
                    manager: type[GeneralManager],
                    request_user: object,
                ) -> None:
                    permission_checks[0] += 1
                    super().check_create_permission(data, manager, request_user)

            class BulkCreate:
                enabled = True
                local_rules = True
                local_permissions = True
                local_search = True

            class SearchConfig:
                indexes = (IndexConfig(name="global", fields=("name",)),)

            @graph_ql_property(cache="dependency")
            def row_count(self) -> int:
                return type(self).all().count()

        def resolve_target(change: Any, owner: type[GeneralManager]) -> tuple[Any, ...]:
            del owner
            return (change.instance.target,)

        class BenchTargetSearchConfig:
            indexes = (IndexConfig(name="global", fields=("label",)),)
            invalidation_rules = (
                SearchInvalidationRule(source=BenchItem, resolve=resolve_target),
            )

        BenchTarget.SearchConfig = BenchTargetSearchConfig  # type: ignore[misc, assignment]
        # Search work stores manager import paths.  Expose these dynamic
        # classes at their declared module path even though external dispatch
        # is replaced during measurements.
        import general_manager.models as manager_models

        vars(manager_models)["BenchTarget"] = BenchTarget
        vars(manager_models)["BenchItem"] = BenchItem
        item_model = BenchItem.Interface._model  # type: ignore[misc]
        target_model = BenchTarget.Interface._model  # type: ignore[misc]
        created_models = create_test_models(
            connection,
            (
                target_model,
                item_model,
                target_model.history.model,
                item_model.history.model,
            ),
        )
        GeneralManagerMeta.all_classes = [BenchTarget, BenchItem]
        user = user_model.objects.create_user(username=f"throughput-{uuid4().hex[:12]}")
        target = BenchTarget.create(
            label=f"target-{uuid4().hex[:12]}", ignore_permission=True
        )
        target_id = int(cast(Any, target).identification["id"])

        redis_cache_client = _redis_client(settings.BENCHMARK_REDIS_URL)
        redis_channel_client = _redis_client(settings.BENCHMARK_CHANNEL_REDIS_URL)
        layer = get_channel_layer()
        if layer is None:
            raise RuntimeError(  # noqa: TRY003 - startup diagnostics are user-facing.
                "CHANNEL_LAYERS did not produce a Channels layer"
            )

        labels: dict[PathKind, str] = {
            "repeated_create": "baseline_repeated",
            "create_many_disabled": "baseline_create_many",
            "optimized_bulk_sql": "optimized_bulk_sql",
        }
        include_optimized = (
            options.include_optimized_path or options.require_optimized_bulk_sql
        )
        raw_results: dict[str, dict[str, list[Mapping[str, object]]]] = {
            labels[path]: {}
            for path in labels
            if include_optimized or path != "optimized_bulk_sql"
        }
        for metric in options.metrics:
            for repeat in range(options.repeats):
                for path in _path_order(repeat, include_optimized=include_optimized):
                    output_label = labels[path]
                    metrics, semantics = _run_isolated_pass(
                        metric=metric,
                        path=path,
                        output_label=output_label,
                        repeat=repeat,
                        rows=options.rows,
                        batch_size=options.batch_size,
                        existing_rows=options.existing_rows,
                        output_prefix=options.output_prefix,
                        manager=BenchItem,
                        model=item_model,
                        owner=user,
                        target_id=target_id,
                        cache=cache,
                        channel_layer=layer,
                        cache_redis=redis_cache_client,
                        channel_redis=redis_channel_client,
                        post_data_change=post_data_change,
                        connect_batch_refresh_receiver=connect_batch_refresh_receiver,
                        bulk_create_eligibility=bulk_create_eligibility,
                        search_state_model=SearchIndexState,
                        workflow_registry_factory=InMemoryEventRegistry,
                        configure_event_registry=configure_event_registry,
                        get_event_registry=get_event_registry,
                        connect_workflow_signal_bridge=connect_workflow_signal_bridge,
                        disconnect_workflow_signal_bridge=disconnect_workflow_signal_bridge,
                        async_to_sync=async_to_sync,
                        connection=connection,
                        override_settings=override_settings,
                        permission_checks=permission_checks,
                    )
                    raw_results[output_label].setdefault(metric, []).append(
                        {"metrics": asdict(metrics), "semantics": asdict(semantics)}
                    )
                    print(
                        f"metric={metric} repeat={repeat} path={output_label} "
                        f"elapsed={metrics.elapsed_seconds} sql={metrics.sql_commands} "
                        f"redis={metrics.redis_commands} memory={metrics.peak_python_bytes}"
                    )

        report = {
            "environment": {
                "python": platform.python_version(),
                "django": django_version(),
                "database_vendor": connection.vendor,
                "database_version": tuple(connection.get_database_version()),
                "general_manager_package_path": str(
                    Path(general_manager.__file__).resolve()
                ),
                "loaded_general_manager_sha256": _loaded_source_hash(general_manager),
                "loaded_general_manager_python_tree_sha256": _loaded_package_tree_hash(
                    general_manager
                ),
                "loaded_source_commit": os.environ.get(
                    "GENERAL_MANAGER_BENCHMARK_SOURCE_COMMIT"
                ),
                "benchmark_harness_sha256": _harness_source_hash(),
                "cache_redis_url": _redact_redis_url(settings.BENCHMARK_REDIS_URL),
                "channel_redis_url": _redact_redis_url(
                    settings.BENCHMARK_CHANNEL_REDIS_URL
                ),
            },
            "configuration": {
                "rows": options.rows,
                "batch_size": options.batch_size,
                "existing_rows": options.existing_rows,
                "repeats": options.repeats,
                "selected_paths": list(raw_results),
                "refresh_receiver": (
                    "public connect_batch_refresh_receiver: cache callback in transaction; "
                    "Channels callback on_commit"
                    if connect_batch_refresh_receiver is not None
                    else "legacy post_data_change: cache in transaction; Channels on_commit"
                ),
                "permissions": "public ManagerBasedPermission evaluated for every measured row",
                "redis_count_definition": (
                    "one INFO commandstats/stats snapshot before and after per unique "
                    "Redis server run_id; two observer commands subtracted per server"
                ),
                "channel_capacity": int(
                    os.environ["GENERAL_MANAGER_BENCHMARK_CHANNEL_CAPACITY"]
                ),
                "channel_expiry_seconds": int(
                    os.environ["GENERAL_MANAGER_BENCHMARK_CHANNEL_EXPIRY"]
                ),
                "channel_expiry_environment_variable": (
                    "GENERAL_MANAGER_BENCHMARK_CHANNEL_EXPIRY"
                ),
                "modes": {
                    "baseline_repeated": "repeated public GeneralManager.create",
                    "baseline_create_many": (
                        "current public create_many fallback; it is not an optimized result"
                    ),
                    "optimized_bulk_sql": (
                        "emitted only after every fixture proves eligibility, a bulk QuerySet "
                        "insert, and zero canonical create calls outside the timing window"
                    ),
                },
                "optimized_bulk_sql_required": options.require_optimized_bulk_sql,
                "search_limitation": (
                    "external search dispatch is no-op; direct and related plan "
                    "formation remains active"
                ),
            },
            "results": raw_results,
            "summary": _summarize(raw_results),
        }
        report_path = options.output_prefix.with_suffix(".json")
        if options.append_report and report_path.exists():
            previous = json.loads(report_path.read_text())
            _validate_append_compatibility(previous, report)
            previous_results = previous.get("results")
            if isinstance(previous_results, dict):
                for result_path, by_metric in raw_results.items():
                    existing_path = previous_results.setdefault(result_path, {})
                    if isinstance(existing_path, dict):
                        existing_path.update(by_metric)
                report["results"] = previous_results
                report["summary"] = _summarize(previous_results)
        _write_json(report_path, report)
        print(f"report={report_path}")
    finally:
        try:
            if created_models:
                with connection.schema_editor() as editor:
                    drop_test_models(editor, reversed(created_models))
        finally:
            GeneralManagerMeta.all_classes = managers_before
            runner.teardown_databases(old_config)


def _validate_append_compatibility(
    previous: Mapping[str, object], current: Mapping[str, object]
) -> None:
    """Reject mixing metric passes from different data, source, or path modes."""
    previous_environment = previous.get("environment")
    current_environment = current.get("environment")
    previous_configuration = previous.get("configuration")
    current_configuration = current.get("configuration")
    if not all(
        isinstance(value, Mapping)
        for value in (
            previous_environment,
            current_environment,
            previous_configuration,
            current_configuration,
        )
    ):
        raise RuntimeError(  # noqa: TRY003 - report diagnostics are user-facing.
            "existing report has no compatible benchmark identity"
        )
    assert isinstance(previous_environment, Mapping)
    assert isinstance(current_environment, Mapping)
    assert isinstance(previous_configuration, Mapping)
    assert isinstance(current_configuration, Mapping)
    environment_keys = (
        "python",
        "django",
        "database_vendor",
        "database_version",
        "general_manager_package_path",
        "loaded_general_manager_sha256",
        "loaded_general_manager_python_tree_sha256",
        "loaded_source_commit",
        "benchmark_harness_sha256",
        "cache_redis_url",
        "channel_redis_url",
    )
    configuration_keys = (
        "rows",
        "batch_size",
        "existing_rows",
        "repeats",
        "selected_paths",
        "refresh_receiver",
        "optimized_bulk_sql_required",
        "channel_capacity",
        "channel_expiry_seconds",
        "channel_expiry_environment_variable",
    )

    def differs(key: str, previous_value: object, current_value: object) -> bool:
        """Compare report values after JSON's tuple-to-list normalization."""
        return json.dumps(previous_value, sort_keys=True) != json.dumps(
            current_value, sort_keys=True
        )

    mismatches = [
        key
        for key in environment_keys
        if differs(key, previous_environment.get(key), current_environment.get(key))
    ] + [
        key
        for key in configuration_keys
        if differs(
            key,
            previous_configuration.get(key),
            current_configuration.get(key),
        )
    ]
    if mismatches:
        raise RuntimeError(
            "cannot append incompatible benchmark report: " + ", ".join(mismatches)
        )


def _redis_client(url: str) -> Any:
    import redis

    client = redis.Redis.from_url(url)
    client.ping()
    return client


def _clear_fixture(
    *,
    model: Any,
    search_state_model: Any,
    cache: Any,
    cache_redis: Any,
    channel_redis: Any,
    connection: Any,
) -> None:
    """Reset only generated benchmark tables without per-row history signals."""
    quote_name = connection.ops.quote_name
    tables = (model._meta.db_table, model.history.model._meta.db_table)
    statement = ", ".join(f"ONLY {quote_name(table)}" for table in tables)
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE TABLE {statement} RESTART IDENTITY")
    search_state_model.objects.all().delete()
    cache.clear()
    cache_redis.flushdb()
    channel_redis.flushdb()


def _seed_existing_rows(
    *, model: Any, rows: int, owner_id: int, target_id: int, pass_token: str
) -> Any | None:
    if rows == 0:
        return None
    model.objects.bulk_create(
        [
            model(
                name=f"existing-{pass_token}-{index}",
                external_id=f"existing-external-{pass_token}-{index}",
                value=index,
                owner_id=owner_id,
                target_id=target_id,
                changed_by_id=owner_id,
            )
            for index in range(rows)
        ],
        batch_size=500,
    )
    return model.objects.order_by("id").first()


def _records(
    rows: int, owner: Any, target_id: int, prefix: str
) -> Iterator[dict[str, object]]:
    for index in range(rows):
        yield {
            "name": f"run-{prefix}-{index}",
            "external_id": f"external-{prefix}-{index}",
            "value": index,
            "owner": owner,
            "target_id": target_id,
        }


def _execute_path(
    *,
    path: PathKind,
    manager: Any,
    rows: int,
    batch_size: int,
    owner: Any,
    target_id: int,
    prefix: str,
) -> None:
    records = _records(rows, owner, target_id, prefix)
    if path == "repeated_create":
        for record in records:
            manager.create(
                creator_id=owner.pk,
                history_comment="bulk-throughput benchmark",
                **record,
            )
        return
    for _batch in manager.create_many(
        records,
        creator_id=owner.pk,
        history_comment="bulk-throughput benchmark",
        batch_size=batch_size,
    ):
        pass


def _assert_optimized_bulk_sql_preflight(
    *,
    manager: Any,
    owner: Any,
    target_id: int,
    rows: int,
    prefix: str,
    bulk_create_eligibility: Callable[[Any], Any] | None,
) -> None:
    """Prove SQL execution on this fixture before timing the optimized path."""
    if bulk_create_eligibility is None:
        raise RuntimeError(  # noqa: TRY003 - benchmark diagnostics are user-facing.
            "optimized SQL benchmarking requires bulk_create_eligibility"
        )
    bulk_config = getattr(manager, "BulkCreate", None)
    assert bulk_config is not None
    assert bulk_config.enabled is True
    eligibility = bulk_create_eligibility(manager)
    assert eligibility.eligible, eligibility.reasons
    from django.db.models.query import QuerySet
    from general_manager.manager.general_manager import GeneralManager

    create_calls = [0]
    bulk_insert_calls = [0]
    original_create = cast(
        Callable[..., Any], cast(Any, GeneralManager.create).__func__
    )
    original_bulk_create = cast(Callable[..., Any], QuerySet.bulk_create)

    def counted_create(cls: Any, /, *args: object, **kwargs: object) -> Any:
        create_calls[0] += 1
        return original_create(cls, *args, **kwargs)

    def counted_bulk_create(queryset: Any, *args: object, **kwargs: object) -> Any:
        bulk_insert_calls[0] += 1
        return original_bulk_create(queryset, *args, **kwargs)

    with (
        patch.object(GeneralManager, "create", new=classmethod(counted_create)),
        patch.object(QuerySet, "bulk_create", new=counted_bulk_create),
        patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch",
            new=lambda *_args, **_kwargs: 0,
        ),
    ):
        _execute_path(
            path="optimized_bulk_sql",
            manager=manager,
            rows=rows,
            batch_size=rows,
            owner=owner,
            target_id=target_id,
            prefix=prefix,
        )
    assert create_calls[0] == 0, "optimized path called canonical create"
    assert bulk_insert_calls[0] > 0, "optimized path made no QuerySet bulk insert"


async def _drain_messages(
    channel_layer: Any, channel_name: str, expected_count: int
) -> list[Mapping[str, object]]:
    """Read every expected external refresh after a measured mutation."""
    return [
        await asyncio.wait_for(channel_layer.receive(channel_name), timeout=5)
        for _ in range(expected_count)
    ]


@contextmanager
def _workflow_registry(
    *,
    registry_factory: Callable[[], Any],
    configure: Callable[[Any], None],
    get: Callable[[], Any],
    connect: Callable[..., None],
    disconnect: Callable[[], None],
    event_counter: list[int],
) -> Iterator[None]:
    prior = get()
    registry = registry_factory()
    registry.register(
        "manager_created",
        handler=lambda _event: event_counter.__setitem__(0, event_counter[0] + 1),
        registration_id="bulk-throughput-benchmark-created",
    )
    connect(registry=registry)
    try:
        yield
    finally:
        disconnect()
        configure(prior)


def _redis_server_key(client: Any) -> str:
    """Return a stable identity shared by clients on one Redis server."""
    server = client.info("server")
    run_id = server.get("run_id")
    if isinstance(run_id, str) and run_id:
        return run_id
    pool = client.connection_pool.connection_kwargs
    return f"{pool.get('host')}:{pool.get('port')}"


def _unique_redis_server_clients(clients: Iterator[Any]) -> dict[str, Any]:
    """Resolve server identities outside a measurement window."""
    servers: dict[str, Any] = {}
    for client in clients:
        key = _redis_server_key(client)
        servers.setdefault(key, client)
    return servers


def _redis_server_snapshots(
    servers: Mapping[str, Any],
) -> dict[str, tuple[int, dict[str, int]]]:
    """Snapshot each already-identified Redis server exactly once."""
    snapshots: dict[str, tuple[int, dict[str, int]]] = {}
    for key, client in servers.items():
        snapshots[key] = _redis_server_snapshot(client)
    return snapshots


def _redis_server_snapshot(client: Any) -> tuple[int, dict[str, int]]:
    """Read one Redis server's total and per-command counts.

    The snapshot performs ``INFO commandstats`` and ``INFO stats`` itself.
    :func:`_redis_server_delta` removes those two observation commands, leaving
    actual server commands, including packed pipelines and Lua internals.
    """
    commandstats = client.info("commandstats")
    total = int(client.info("stats")["total_commands_processed"])
    calls = {
        name.removeprefix("cmdstat_"): int(values["calls"])
        for name, values in commandstats.items()
        if isinstance(values, Mapping) and "calls" in values
    }
    return total, calls


def _redis_server_delta(
    before: tuple[int, Mapping[str, int]], after: tuple[int, Mapping[str, int]]
) -> tuple[int, dict[str, int]]:
    """Return workload-only Redis commands from two server snapshots."""
    total = max(after[0] - before[0] - 2, 0)
    names = set(before[1]) | set(after[1])
    command_calls = {
        name: after[1].get(name, 0) - before[1].get(name, 0)
        for name in names
        if name != "info" and after[1].get(name, 0) != before[1].get(name, 0)
    }
    return total, dict(sorted(command_calls.items()))


def _redis_servers_delta(
    before: Mapping[str, tuple[int, Mapping[str, int]]],
    after: Mapping[str, tuple[int, Mapping[str, int]]],
) -> tuple[int, dict[str, int]]:
    """Combine workload deltas from distinct Redis server identities."""
    assert before.keys() == after.keys()
    total = 0
    breakdown: dict[str, int] = {}
    for key in before:
        server_total, server_breakdown = _redis_server_delta(before[key], after[key])
        total += server_total
        for command, calls in server_breakdown.items():
            breakdown[command] = breakdown.get(command, 0) + calls
    return total, dict(sorted(breakdown.items()))


def _run_isolated_pass(
    *,
    metric: MetricKind,
    path: PathKind,
    output_label: str,
    repeat: int,
    rows: int,
    batch_size: int,
    existing_rows: int,
    output_prefix: Path,
    manager: Any,
    model: Any,
    owner: Any,
    target_id: int,
    cache: Any,
    channel_layer: Any,
    cache_redis: Any,
    channel_redis: Any,
    post_data_change: Any,
    connect_batch_refresh_receiver: Callable[..., Any] | None,
    bulk_create_eligibility: Callable[[Any], Any] | None,
    search_state_model: Any,
    workflow_registry_factory: Callable[[], Any],
    configure_event_registry: Callable[[Any], None],
    get_event_registry: Callable[[], Any],
    connect_workflow_signal_bridge: Callable[..., None],
    disconnect_workflow_signal_bridge: Callable[[], None],
    async_to_sync: Callable[[Any], Any],
    connection: Any,
    override_settings: Callable[..., Any],
    permission_checks: list[int],
) -> tuple[PassMetrics, SemanticResult]:
    """Build a clean fixture, measure one operation, then verify its effects."""
    permission_checks[0] = 0
    manager.BulkCreate.enabled = path == "optimized_bulk_sql"
    data_token = f"{metric}-repeat-{repeat}"
    helper_available = connect_batch_refresh_receiver is not None
    if path == "optimized_bulk_sql" and not helper_available:
        raise RuntimeError(  # noqa: TRY003 - benchmark diagnostics are user-facing.
            "optimized SQL path requires connect_batch_refresh_receiver"
        )
    expected_refreshes = (
        (rows + batch_size - 1) // batch_size
        if path == "optimized_bulk_sql" and helper_available
        else rows
    )
    applied_refreshes = [0]
    applied_identifiers = [0]
    external_notifications = [0]
    workflow_events = [0]
    search_plan_counts = {"direct": 0, "related": 0}
    group_state: dict[str, str | None] = {"group": None}
    cache_key = f"bulk-throughput:generated:{data_token}"

    def apply_cache_refresh(
        sender: Any,
        identifiers: tuple[object, ...],
        action: str,
        database_alias: str,
    ) -> None:
        del database_alias
        if sender is not manager or action != "create":
            return
        applied_refreshes[0] += 1
        applied_identifiers[0] += len(identifiers)
        cache.set(cache_key, applied_refreshes[0], 300)

    def publish_notification(
        sender: Any,
        identifiers: tuple[object, ...],
        action: str,
        database_alias: str,
    ) -> None:
        del database_alias
        if sender is not manager or action != "create":
            return
        external_notifications[0] += 1
        group = group_state["group"]
        if group is None:
            return
        async_to_sync(channel_layer.group_send)(
            group,
            {
                "type": "gm.throughput.refresh",
                "cache_key": cache_key,
                # Frozen baselines may expose the former scalar-ID callback API.
                "identifiers": tuple(
                    dict(identity)
                    if isinstance(identity, Mapping)
                    else {"id": identity}
                    for identity in identifiers
                ),
            },
        )

    def legacy_receiver(
        sender: Any,
        identification: Mapping[str, object] | None = None,
        instance: Any | None = None,
        action: str | None = None,
        database_alias: str = "default",
        **_: object,
    ) -> None:
        if sender is not manager or action != "create":
            return
        values = identification or getattr(instance, "identification", {})
        identity = dict(values)
        apply_cache_refresh(sender, (identity,), action, database_alias)
        from django.db import transaction

        transaction.on_commit(
            lambda: publish_notification(sender, (identity,), action, database_alias),
            using=database_alias,
        )

    def record_search_dispatch(*args: object, **kwargs: object) -> int:
        del kwargs
        manager_path = str(args[0])
        if manager_path.endswith("BenchTarget"):
            search_plan_counts["related"] += 1
        elif manager_path.endswith("BenchItem"):
            search_plan_counts["direct"] += 1
        return 0

    def execute() -> None:
        _execute_path(
            path=path,
            manager=manager,
            rows=rows,
            batch_size=batch_size,
            owner=owner,
            target_id=target_id,
            prefix=data_token,
        )

    profile_path = output_prefix.parent / (
        f"{output_prefix.name}.{metric}.{output_label}.repeat-{repeat}.pstats"
    )
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    value: PassMetrics
    with ExitStack() as stack:
        stack.enter_context(
            _workflow_registry(
                registry_factory=workflow_registry_factory,
                configure=configure_event_registry,
                get=get_event_registry,
                connect=connect_workflow_signal_bridge,
                disconnect=disconnect_workflow_signal_bridge,
                event_counter=workflow_events,
            )
        )
        if helper_available:
            assert connect_batch_refresh_receiver is not None
            stack.callback(
                connect_batch_refresh_receiver(apply_cache_refresh).disconnect
            )
            stack.callback(
                connect_batch_refresh_receiver(
                    publish_notification,
                    on_commit=True,
                ).disconnect
            )
        else:
            post_data_change.connect(legacy_receiver, weak=False)
            stack.callback(post_data_change.disconnect, legacy_receiver)
        stack.enter_context(
            patch(
                "general_manager.search.invalidation.dispatch_index_manager_batch",
                new=record_search_dispatch,
            )
        )
        stack.enter_context(override_settings(DEBUG=False))
        if path == "optimized_bulk_sql":
            _clear_fixture(
                model=model,
                search_state_model=search_state_model,
                cache=cache,
                cache_redis=cache_redis,
                channel_redis=channel_redis,
                connection=connection,
            )
            _assert_optimized_bulk_sql_preflight(
                manager=manager,
                owner=owner,
                target_id=target_id,
                rows=min(2, rows),
                prefix=f"proof-{data_token}",
                bulk_create_eligibility=bulk_create_eligibility,
            )
            applied_refreshes[0] = 0
            applied_identifiers[0] = 0
            external_notifications[0] = 0
            workflow_events[0] = 0
            permission_checks[0] = 0
            search_plan_counts["direct"] = 0
            search_plan_counts["related"] = 0
        _clear_fixture(
            model=model,
            search_state_model=search_state_model,
            cache=cache,
            cache_redis=cache_redis,
            channel_redis=channel_redis,
            connection=connection,
        )
        existing = _seed_existing_rows(
            model=model,
            rows=existing_rows,
            owner_id=owner.pk,
            target_id=target_id,
            pass_token=data_token,
        )
        if existing is not None:
            cached_manager = manager(id=existing.pk)
            assert cached_manager.row_count == existing_rows
            assert cached_manager.row_count == existing_rows

        group = f"gm-throughput-{uuid4().hex[:16]}"
        group_state["group"] = group
        channel_name = async_to_sync(channel_layer.new_channel)()
        async_to_sync(channel_layer.group_add)(group, channel_name)
        redis_servers = _unique_redis_server_clients(iter((cache_redis, channel_redis)))
        if metric == "elapsed":
            started = perf_counter()
            execute()
            value = PassMetrics(elapsed_seconds=perf_counter() - started)
        elif metric == "profile":
            profiler = cProfile.Profile()
            profiler.enable()
            execute()
            profiler.disable()
            profiler.dump_stats(str(profile_path))
            stats = pstats.Stats(profiler)
            value = PassMetrics(
                profile_total_calls=cast(Any, stats).total_calls,
                profile_primitive_calls=cast(Any, stats).prim_calls,
                profile_total_seconds=cast(Any, stats).total_tt,
                profile_path=str(profile_path),
            )
        elif metric == "sql_redis":
            sql_count = [0]

            def sql_counter(
                execute_sql: Callable[..., object],
                sql: str,
                params: object,
                many: bool,
                context: object,
            ) -> object:
                sql_count[0] += 1
                return execute_sql(sql, params, many, context)

            redis_before = _redis_server_snapshots(redis_servers)
            with connection.execute_wrapper(sql_counter):
                execute()
            redis_commands, redis_breakdown = _redis_servers_delta(
                redis_before,
                _redis_server_snapshots(redis_servers),
            )
            value = PassMetrics(
                sql_commands=sql_count[0],
                redis_commands=redis_commands,
                redis_command_breakdown=redis_breakdown,
            )
        else:
            tracemalloc.start()
            try:
                execute()
                _current, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            value = PassMetrics(peak_python_bytes=peak)

    measured = model.objects.filter(name__startswith=f"run-{data_token}-")
    assert measured.count() == rows
    assert measured.values("name").distinct().count() == rows
    assert measured.values("external_id").distinct().count() == rows
    assert set(measured.values_list("owner_id", flat=True)) == {owner.pk}
    assert set(measured.values_list("target_id", flat=True)) == {target_id}
    assert set(measured.values_list("changed_by_id", flat=True)) == {owner.pk}
    history = model.history.filter(name__startswith=f"run-{data_token}-")
    assert history.count() == rows
    assert set(history.values_list("history_user_id", flat=True)) == {owner.pk}
    assert set(history.values_list("history_change_reason", flat=True)) == {
        "bulk-throughput benchmark"
    }
    assert workflow_events[0] == rows
    assert permission_checks[0] == rows
    assert applied_refreshes[0] == expected_refreshes
    assert applied_identifiers[0] == rows
    assert cache.get(cache_key) == expected_refreshes
    assert external_notifications[0] == expected_refreshes
    delivered = async_to_sync(_drain_messages)(
        channel_layer,
        channel_name,
        expected_refreshes,
    )
    assert all(message["type"] == "gm.throughput.refresh" for message in delivered)
    assert search_plan_counts["direct"] > 0
    assert search_plan_counts["related"] > 0
    if existing is not None:
        assert manager(id=existing.pk).row_count == existing_rows + rows
    async_to_sync(channel_layer.group_discard)(group, channel_name)
    return value, SemanticResult(
        rows_created=rows,
        history_rows=history.count(),
        permission_checks=permission_checks[0],
        workflow_events=workflow_events[0],
        applied_cache_refreshes=applied_refreshes[0],
        applied_cache_identifiers=applied_identifiers[0],
        external_notifications=external_notifications[0],
        related_search_plans=search_plan_counts["related"],
        notification_delivered=True,
    )


def _summarize(
    raw_results: Mapping[str, Mapping[str, list[Mapping[str, object]]]],
) -> dict[str, object]:
    summary: dict[str, object] = {}
    for path, by_metric in raw_results.items():
        path_summary: dict[str, object] = {}
        for metric, samples in by_metric.items():
            metrics = [sample["metrics"] for sample in samples]
            numeric = {
                key: [
                    value
                    for metric_value in metrics
                    if isinstance(metric_value, Mapping)
                    if isinstance((value := metric_value.get(key)), int | float)
                ]
                for key in PassMetrics.__dataclass_fields__
            }
            path_summary[metric] = {
                key: _mean(values) for key, values in numeric.items() if values
            }
        summary[path] = path_summary
    return summary


if __name__ == "__main__":
    main()
