from __future__ import annotations

import os
from collections.abc import Iterator, Set as AbstractSet

import pytest

from tests.perf.budgets import PERF_CEILINGS
from tests.perf.support import PerfBudgets


REQUIRED_BUDGET_WORKLOAD_MODULES = frozenset(
    {
        "test_calculation_bucket_perf.py",
        "test_database_bucket_perf.py",
        "test_group_bucket_perf.py",
    }
)


def should_validate_perf_manifest(
    selected_modules: AbstractSet[str],
    *,
    keyword_expression: str,
    failed: bool,
) -> bool:
    return (
        not failed
        and not keyword_expression
        and REQUIRED_BUDGET_WORKLOAD_MODULES <= selected_modules
    )


class PerfManifestValidationPlugin:
    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._selected_modules: frozenset[str] = frozenset()
        self._keyword_expression = ""
        self._failed = False

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self._reset()

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self._selected_modules = frozenset(item.path.name for item in session.items)
        keyword_expression = session.config.getoption("keyword")
        assert isinstance(keyword_expression, str)
        self._keyword_expression = keyword_expression

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when in {"setup", "call", "teardown"} and report.failed:
            self._failed = True

    def should_validate_manifest(self) -> bool:
        return should_validate_perf_manifest(
            self._selected_modules,
            keyword_expression=self._keyword_expression,
            failed=self._failed,
        )


_perf_manifest_validation = PerfManifestValidationPlugin()


def pytest_sessionstart(session: pytest.Session) -> None:
    _perf_manifest_validation.pytest_sessionstart(session)


def pytest_collection_finish(session: pytest.Session) -> None:
    _perf_manifest_validation.pytest_collection_finish(session)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _perf_manifest_validation.pytest_runtest_logreport(report)


def validate_perf_budget_manifest(
    validation: PerfManifestValidationPlugin,
    perf_budgets: PerfBudgets,
) -> None:
    if validation.should_validate_manifest():
        perf_budgets.validate_manifest(set(perf_budgets.observations))


@pytest.fixture(scope="session")
def perf_budgets() -> PerfBudgets:
    return PerfBudgets(
        PERF_CEILINGS,
        record=os.environ.get("GENERAL_MANAGER_RECORD_PERF") == "1",
    )


@pytest.fixture(scope="session", autouse=True)
def validate_complete_perf_budget_manifest(
    perf_budgets: PerfBudgets,
) -> Iterator[None]:
    yield
    validate_perf_budget_manifest(_perf_manifest_validation, perf_budgets)
