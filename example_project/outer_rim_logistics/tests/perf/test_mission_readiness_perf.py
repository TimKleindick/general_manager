from __future__ import annotations

import time
from datetime import date

import pytest
from django.core.management import call_command

from mission.managers import MissionReadiness

pytestmark = pytest.mark.perf


def test_mission_readiness_cache_perf() -> None:
    call_command("seed_outer_rim")
    as_of = date(2222, 9, 1)

    start = time.perf_counter()
    _ = MissionReadiness(as_of=as_of).score
    cold = time.perf_counter() - start

    start = time.perf_counter()
    _ = MissionReadiness(as_of=as_of).score
    warm = time.perf_counter() - start

    assert warm <= cold
    assert warm < 2.0
