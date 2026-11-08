"""Opt-in GraphQL search-routing benchmark on a deterministic native fixture.

The benchmark intentionally lives beside the other performance tests, but is
not part of the normal suite: it creates a sizable fixture only when
``GENERAL_MANAGER_RUN_SEARCH_ROUTING_BENCHMARK=1`` is set.  The same module can
be copied into source archives and run with each archive's ``src`` first on
``PYTHONPATH``.

This is a synthetic routing diagnostic.  It does not replace the KnowledgeHub
fixture, its production-shaped queries, or the original booking/calendar
release gates.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import statistics
from time import perf_counter
from typing import ClassVar

import pytest
from django.db import connection, models
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

import general_manager
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from general_manager.utils.testing import GeneralManagerTransactionTestCase


pytestmark = pytest.mark.perf

_RUN_ENV = "GENERAL_MANAGER_RUN_SEARCH_ROUTING_BENCHMARK"
_ROWS_ENV = "GENERAL_MANAGER_SEARCH_BENCHMARK_ROWS"
_REPETITIONS_ENV = "GENERAL_MANAGER_SEARCH_BENCHMARK_REPETITIONS"
_ALLOW_FAILURE_ENV = "GENERAL_MANAGER_SEARCH_BENCHMARK_ALLOW_FAILURE"
_RAISE_LIMIT_ENV = "GENERAL_MANAGER_SEARCH_BENCHMARK_RAISE_TOTAL_LIMIT"
_OUTPUT_ENV = "GENERAL_MANAGER_SEARCH_BENCHMARK_OUTPUT"
_SOURCE_ENV = "GENERAL_MANAGER_SEARCH_BENCHMARK_SOURCE"

_DEFAULT_ROWS = 200
_DEFAULT_REPETITIONS = 7
_PAGE_SIZE = 10
_INDEX_NAME = "search_routing_benchmark"

_QUERY = """
query SearchRoutingBenchmark {
  search(
    index: "search_routing_benchmark"
    query: "needle"
    filters: "{\\"category\\":\\"public\\"}"
    orderBy: [{field: name, direction: ASC}]
    page: 1
    pageSize: 10
  ) {
    total
    totalIsExact
    pageInfo { totalCount pageSize currentPage totalPages }
    results {
      __typename
      ... on SearchRoutingRecordType { id name category }
    }
  }
}
"""


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Read one positive integer benchmark setting with a useful error."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(  # noqa: TRY003 - benchmark setting diagnostics.
            f"{name} must be an integer, got {raw!r}"
        ) from error
    if value < minimum:
        raise ValueError(  # noqa: TRY003 - benchmark setting diagnostics.
            f"{name} must be >= {minimum}, got {value}"
        )
    return value


def _enabled() -> bool:
    """Return whether the opt-in benchmark was requested."""
    return os.environ.get(_RUN_ENV) == "1"


def _allow_failure() -> bool:
    """Return whether expected source-limit failures should be recorded."""
    return os.environ.get(_ALLOW_FAILURE_ENV) == "1"


def _payload_digest(payload: object) -> str:
    """Return a stable digest for equality checks and compact reports."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_metadata() -> dict[str, object]:
    """Report imported source paths and both distribution/source versions."""
    package_path = Path(general_manager.__file__).resolve()
    project_root = Path(__file__).resolve().parents[2]
    source_version: str | None = None
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.is_file():
        import tomllib

        with pyproject_path.open("rb") as stream:
            source_version = str(tomllib.load(stream)["project"]["version"])
    try:
        distribution_version: str | None = importlib.metadata.version("GeneralManager")
    except importlib.metadata.PackageNotFoundError:
        distribution_version = None
    return {
        "benchmark_file": str(Path(__file__).resolve()),
        "package_file": str(package_path),
        "graphql_search_file": str(
            Path(
                __import__(
                    "general_manager.api.graphql_search", fromlist=["__file__"]
                ).__file__
            ).resolve()
        ),
        "source_label": os.environ.get(_SOURCE_ENV),
        "source_version": source_version,
        "distribution_version": distribution_version,
    }


class TestSearchRoutingBenchmark(GeneralManagerTransactionTestCase):
    """Measure one real GraphQL search request over native scalar rows."""

    index_name: ClassVar[str] = _INDEX_NAME

    @classmethod
    def setUpClass(cls) -> None:
        class SearchRoutingRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=80)
                category = models.CharField(max_length=32)
                ordinal = models.IntegerField()

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

            class SearchConfig:
                indexes: ClassVar[list[IndexConfig]] = [
                    IndexConfig(
                        name=_INDEX_NAME,
                        fields=["name", "category"],
                        filters=["category"],
                        sorts=["name"],
                    )
                ]

        cls.Record = SearchRoutingRecord
        cls.general_manager_classes = [SearchRoutingRecord]

    def setUp(self) -> None:
        super().setUp()
        if not _enabled():
            self.skipTest(
                f"set {_RUN_ENV}=1 to run the opt-in search-routing benchmark"
            )

        self.row_count = _env_int(_ROWS_ENV, _DEFAULT_ROWS)
        self.repetitions = _env_int(
            _REPETITIONS_ENV,
            _DEFAULT_REPETITIONS,
            minimum=7,
        )
        self._settings_override = None
        if os.environ.get(_RAISE_LIMIT_ENV) == "1":
            # This is a separately labelled diagnostic.  It does not raise the
            # field-decision ceiling in graphql_query_permissions.py.
            self._settings_override = override_settings(
                GENERAL_MANAGER={
                    "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": self.row_count + 1,
                }
            )
            self._settings_override.enable()

        model = self.Record.Interface._model
        model.objects.bulk_create(
            [
                model(
                    name=f"needle row {ordinal:04d}",
                    category="public",
                    ordinal=ordinal,
                )
                for ordinal in range(self.row_count)
            ]
        )
        self.backend = DevSearchBackend()
        configure_search_backend(self.backend)
        # Indexing is fixture setup and is deliberately outside every measured
        # request.  The backend is not configured until after row creation so
        # lifecycle invalidation cannot enter the timing boundary either.
        SearchIndexer(self.backend).reindex_manager(self.Record)

    def tearDown(self) -> None:
        configure_search_backend(None)
        settings_override = getattr(self, "_settings_override", None)
        if settings_override is not None:
            settings_override.disable()
        super().tearDown()

    def _request(self) -> dict[str, object]:
        """Execute the real GraphQL test-client request and count its SQL."""
        with CaptureQueriesContext(connection) as queries:
            started = perf_counter()
            response = self.query(_QUERY)
            elapsed_ms = (perf_counter() - started) * 1000
        body = response.json()
        errors = body.get("errors")
        error_text = None
        payload: object | None = None
        if errors:
            error_text = "; ".join(
                str(error.get("message", error))
                for error in errors
                if isinstance(error, dict)
            )
            if not error_text:
                error_text = str(errors)
        else:
            payload = body.get("data", {}).get("search")
        return {
            "elapsed_ms": elapsed_ms,
            "sql_count": len(queries),
            "status_code": response.status_code,
            "payload": payload,
            "payload_sha256": _payload_digest(payload) if payload is not None else None,
            "error": error_text,
        }

    def _assert_functional_payload(self, payload: object) -> None:
        """Check the complete expected response for a successful sample."""
        assert isinstance(payload, dict)
        self.assertEqual(payload["total"], self.row_count)
        self.assertIs(payload["totalIsExact"], True)
        self.assertEqual(
            payload["pageInfo"],
            {
                "totalCount": self.row_count,
                "pageSize": _PAGE_SIZE,
                "currentPage": 1,
                "totalPages": (self.row_count + _PAGE_SIZE - 1) // _PAGE_SIZE,
            },
        )
        results = payload["results"]
        assert isinstance(results, list)
        self.assertEqual(len(results), _PAGE_SIZE)
        self.assertEqual(
            [row["name"] for row in results],
            [f"needle row {ordinal:04d}" for ordinal in range(_PAGE_SIZE)],
        )
        self.assertTrue(all(row["category"] == "public" for row in results))

    def _write_report(self, report: dict[str, object]) -> None:
        """Print a machine-readable line and optionally persist a JSON report."""
        encoded = json.dumps(report, sort_keys=True, default=str)
        print(f"SEARCH_ROUTING_BENCHMARK {encoded}")
        output = os.environ.get(_OUTPUT_ENV)
        if output:
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded + "\n", encoding="utf-8")

    def test_search_routing_benchmark(self) -> None:
        """Run one warmup plus at least seven measured GraphQL requests."""
        warmup = self._request()
        samples = [self._request() for _ in range(self.repetitions)]
        warmup_payload = warmup["payload"]
        successful_payloads = [
            sample["payload"]
            for sample in samples
            if sample["error"] is None and sample["payload"] is not None
        ]
        all_payloads = (
            [warmup_payload, *successful_payloads]
            if warmup["error"] is None and warmup_payload is not None
            else successful_payloads
        )
        warmup_successful = warmup["error"] is None and warmup_payload is not None
        all_successful = warmup_successful and len(successful_payloads) == len(samples)
        payloads_equal = bool(all_payloads) and all(
            payload == all_payloads[0] for payload in all_payloads[1:]
        )
        functional_payload_verified = False
        if all_successful and payloads_equal:
            self._assert_functional_payload(all_payloads[0])
            functional_payload_verified = True

        elapsed_values = [float(sample["elapsed_ms"]) for sample in samples]
        sql_counts = [int(sample["sql_count"]) for sample in samples]

        report: dict[str, object] = {
            "status": "ok" if all_successful and payloads_equal else "failed",
            "source": _source_metadata(),
            "rows": self.row_count,
            "index": _INDEX_NAME,
            "repetitions": self.repetitions,
            "warmup": warmup,
            "samples": samples,
            "summary": {
                "median_ms": statistics.median(elapsed_values),
                "min_ms": min(elapsed_values),
                "max_ms": max(elapsed_values),
                "sql_counts": sql_counts,
            },
            "payloads_equal_within_version": payloads_equal,
            "functional_payload_verified": functional_payload_verified,
            "measurement_boundary": (
                "real GraphQL test-client request; fixture creation and DevSearchBackend "
                "indexing are outside the measured requests"
            ),
        }
        self._write_report(report)

        if report["status"] != "ok" and not _allow_failure():
            errors = [sample["error"] for sample in samples if sample["error"]]
            self.fail(
                "search-routing benchmark failed; rerun with "
                f"{_ALLOW_FAILURE_ENV}=1 only to record an expected source-limit "
                f"diagnostic: {errors!r}"
            )
