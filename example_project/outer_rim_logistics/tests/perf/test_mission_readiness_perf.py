from __future__ import annotations

import time
from datetime import date

import pytest
from django.core.management import call_command

from outer_rim_logistics.maintenance.managers import Ship
from outer_rim_logistics.mission.managers import MissionReadiness

pytestmark = [pytest.mark.perf, pytest.mark.django_db]


def test_mission_readiness_cache_perf() -> None:
    call_command("seed_outer_rim")
    as_of = date(2222, 9, 1)
    ship = Ship.all().first()

    start = time.perf_counter()
    _ = MissionReadiness(as_of=as_of, ship=ship).readiness
    cold = time.perf_counter() - start

    start = time.perf_counter()
    _ = MissionReadiness(as_of=as_of, ship=ship).readiness
    warm = time.perf_counter() - start

    assert warm <= cold
    assert warm < 2.0
