from __future__ import annotations

import logging
import os
import threading
import time

from celery import Celery
from celery.signals import task_failure, task_postrun, task_prerun, task_success
from prometheus_client import CollectorRegistry, Counter, Histogram, push_to_gateway

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "orl.settings")

_logger = logging.getLogger(__name__)

app = Celery("orl")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
app.autodiscover_tasks(["orl"])

# Ensure local debug tasks are registered even if "orl" isn't in INSTALLED_APPS.
try:
    import orl.tasks  # noqa: F401
except Exception:  # pragma: no cover - avoid startup failure if tasks are absent
    _logger.exception("Failed to import orl.tasks for Celery registration")

_task_start_times: dict[str, float] = {}
_task_lock = threading.Lock()

_pushgateway_url = os.environ.get("CELERY_PUSHGATEWAY_URL", "http://pushgateway:9091")
_pushgateway_enabled = os.environ.get("CELERY_PUSHGATEWAY_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}

_celery_registry = CollectorRegistry()
_celery_task_duration = Histogram(
    "celery_task_duration_seconds",
    "Celery task execution duration in seconds.",
    ["task", "status", "worker"],
    registry=_celery_registry,
)
_celery_task_total = Counter(
    "celery_task_total",
    "Celery task outcomes.",
    ["task", "status", "worker"],
    registry=_celery_registry,
)


def _record_task_metrics(*, task_name: str, status: str, duration: float, worker: str) -> None:
    if not _pushgateway_enabled or not _pushgateway_url:
        return

    _celery_task_duration.labels(task=task_name, status=status, worker=worker).observe(
        duration
    )
    _celery_task_total.labels(task=task_name, status=status, worker=worker).inc()

    try:
        push_to_gateway(
            _pushgateway_url,
            job="celery_tasks",
            registry=_celery_registry,
            grouping_key={"worker": worker},
        )
    except Exception:  # pragma: no cover - avoid task failures on metrics push
        _logger.exception("Failed to push Celery metrics to Pushgateway")


@task_prerun.connect
def _celery_task_prerun(task_id: str, **_kwargs) -> None:
    with _task_lock:
        _task_start_times[task_id] = time.monotonic()


@task_success.connect
def _celery_task_success(sender=None, **_kwargs) -> None:
    task_id = getattr(getattr(sender, "request", None), "id", None)
    if task_id is None:
        return
    with _task_lock:
        start = _task_start_times.pop(task_id, None)
    if start is None:
        return
    duration = time.monotonic() - start
    worker = getattr(getattr(sender, "request", None), "hostname", "unknown")
    task_name = getattr(sender, "name", "unknown")
    _record_task_metrics(
        task_name=task_name,
        status="success",
        duration=duration,
        worker=worker,
    )


@task_failure.connect
def _celery_task_failure(sender=None, **_kwargs) -> None:
    task_id = getattr(getattr(sender, "request", None), "id", None)
    if task_id is None:
        return
    with _task_lock:
        start = _task_start_times.pop(task_id, None)
    if start is None:
        return
    duration = time.monotonic() - start
    worker = getattr(getattr(sender, "request", None), "hostname", "unknown")
    task_name = getattr(sender, "name", "unknown")
    _record_task_metrics(
        task_name=task_name,
        status="failure",
        duration=duration,
        worker=worker,
    )


@task_postrun.connect
def _celery_task_postrun(task_id: str | None = None, **_kwargs) -> None:
    if task_id is None:
        return
    with _task_lock:
        _task_start_times.pop(task_id, None)
