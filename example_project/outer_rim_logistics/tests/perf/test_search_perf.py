from __future__ import annotations

import pytest
from django.core.management import call_command

from general_manager.search.backend_registry import get_search_backend

pytestmark = pytest.mark.perf


def test_global_search_latency() -> None:
    call_command("seed_outer_rim")
    backend = get_search_backend()
    result = backend.search("global", "Corellian")
    if result.took_ms is None:
        pytest.skip("Search backend did not report latency timing")
    assert result.took_ms < 2000
    assert result.total > 0
