from __future__ import annotations

import random
import time

from celery import shared_task


@shared_task
def debug_sleep(seconds: float = 0.5) -> float:
    """Simple task to generate duration metrics."""
    time.sleep(seconds)
    return seconds


@shared_task
def debug_maybe_fail(seconds: float = 0.2, fail_rate: float = 0.1) -> str:
    """Task that occasionally fails to generate failure metrics."""
    time.sleep(seconds)
    if random.random() < fail_rate:
        raise RuntimeError("debug task failure")
    return "ok"
