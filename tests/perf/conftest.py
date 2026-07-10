from __future__ import annotations

import os

import pytest

from tests.perf.budgets import PERF_CEILINGS
from tests.perf.support import PerfBudgets


@pytest.fixture(scope="session")
def perf_budgets() -> PerfBudgets:
    return PerfBudgets(
        PERF_CEILINGS,
        record=os.environ.get("GENERAL_MANAGER_RECORD_PERF") == "1",
    )
