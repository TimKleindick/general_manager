from __future__ import annotations

import os
from collections.abc import Iterator, Sequence, Set as AbstractSet
from typing import Protocol, cast

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
    selection_arguments: Sequence[str] = (),
    failed: bool,
) -> bool:
    return (
        not failed
        and not keyword_expression
        and not any("::" in argument for argument in selection_arguments)
        and REQUIRED_BUDGET_WORKLOAD_MODULES <= selected_modules
    )


class TerminalReporter(Protocol):
    def write_line(self, message: str, **markup: bool) -> None: ...


class PerfManifestValidationPlugin:
    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._selected_modules: frozenset[str] = frozenset()
        self._keyword_expression = ""
        self._selection_arguments: tuple[str, ...] = ()
        self._failed = False
        self._perf_budgets: PerfBudgets | None = None

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self._reset()
        self._selected_modules = frozenset(item.path.name for item in session.items)
        keyword_expression = session.config.getoption("keyword")
        assert isinstance(keyword_expression, str)
        self._keyword_expression = keyword_expression
        self._selection_arguments = tuple(
            str(argument) for argument in session.config.invocation_params.args
        )

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when in {"setup", "call", "teardown"} and report.failed:
            self._failed = True

    def capture_perf_budgets(self, perf_budgets: PerfBudgets) -> None:
        self._perf_budgets = perf_budgets

    def should_validate_manifest(self) -> bool:
        return should_validate_perf_manifest(
            self._selected_modules,
            keyword_expression=self._keyword_expression,
            selection_arguments=self._selection_arguments,
            failed=self._failed,
        )

    def pytest_sessionfinish(
        self,
        session: pytest.Session,
        exitstatus: int | pytest.ExitCode,
    ) -> None:
        if (
            exitstatus != pytest.ExitCode.OK
            or self._perf_budgets is None
            or not self.should_validate_manifest()
        ):
            return
        try:
            self._perf_budgets.validate_manifest(set(self._perf_budgets.observations))
        except AssertionError as error:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            if reporter is not None:
                cast(TerminalReporter, reporter).write_line(
                    f"performance budget manifest validation failed: {error}",
                    red=True,
                )


_perf_manifest_validation = PerfManifestValidationPlugin()


def pytest_collection_finish(session: pytest.Session) -> None:
    _perf_manifest_validation.pytest_collection_finish(session)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _perf_manifest_validation.pytest_runtest_logreport(report)


def pytest_sessionfinish(
    session: pytest.Session,
    exitstatus: int | pytest.ExitCode,
) -> None:
    _perf_manifest_validation.pytest_sessionfinish(session, exitstatus)


@pytest.fixture(scope="session")
def perf_budgets() -> PerfBudgets:
    return PerfBudgets(
        PERF_CEILINGS,
        record=os.environ.get("GENERAL_MANAGER_RECORD_PERF") == "1",
    )


@pytest.fixture(scope="session", autouse=True)
def capture_perf_budget_observations(
    perf_budgets: PerfBudgets,
) -> Iterator[None]:
    yield
    _perf_manifest_validation.capture_perf_budgets(perf_budgets)
