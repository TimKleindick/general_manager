"""Opt-in PostgreSQL and Redis smoke for actual worker process loss."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from celery import Celery
from django.db import connection
from django.utils import timezone

from general_manager.workflow import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowStepContext,
)
from general_manager.workflow.backends.celery import CeleryWorkflowEngine
from general_manager.workflow.models import WorkflowStepRecord
from general_manager.workflow.tasks import execute_workflow_step, publish_durable_steps

_WORKER = """\
import django, os, sys
from celery import Celery
django.setup()
app = Celery("durable-workflow-test", broker=os.environ["GM_WORKFLOW_TEST_REDIS_URL"])
app.worker_main(["worker", "--pool=solo", "--without-gossip", "--without-mingle",
                 "--without-heartbeat", "--loglevel=WARNING", "-Q", sys.argv[1]])
"""


def crash_once(context: WorkflowStepContext) -> dict[str, object]:
    """Leave one synthetic external effect then abruptly end the first worker."""
    path = Path(str(context.input_data["effect_path"]))
    try:
        with path.open("x", encoding="utf-8") as effect:
            effect.write(context.idempotency_key)
    except FileExistsError:
        return {"same_key": path.read_text() == context.idempotency_key}
    os._exit(23)


@pytest.mark.django_db(transaction=True)
def test_postgres_redis_recovers_real_worker_loss(settings, tmp_path, monkeypatch):
    broker = os.environ.get("GM_WORKFLOW_TEST_REDIS_URL")
    if not broker or connection.vendor != "postgresql":
        pytest.skip("requires PostgreSQL and an isolated Redis broker")
    settings.GENERAL_MANAGER = {
        "WORKFLOW_MODE": "production",
        "WORKFLOW_ASYNC": True,
        "WORKFLOW_DURABLE_STEPS_ENABLED": True,
    }
    app = Celery("durable-workflow-test", broker=broker)
    queue = f"gm-workflow-test-{uuid4().hex}"
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "tests.test_settings",
        "GENERAL_MANAGER_TEST_DATABASE_NAME": connection.settings_dict["NAME"],
        "PYTHONPATH": os.pathsep.join(sys.path),
    }
    effect = tmp_path / "synthetic-effect"
    log = tmp_path / "worker.log"
    module = "tests.integration.test_workflow_live_recovery"

    def publish(step_id):
        return app.send_task(execute_workflow_step.name, (step_id,), queue=queue)

    def unavailable(_step_id):
        raise ConnectionError

    def worker():
        return subprocess.Popen(  # noqa: S603 - fixed Python program and generated queue
            [sys.executable, "-c", _WORKER, queue],
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )

    engine = CeleryWorkflowEngine()
    monkeypatch.setattr(execute_workflow_step, "delay", unavailable)
    run = engine.start(
        WorkflowDefinition(
            "broker-recovery", steps=(WorkflowStep("deliver", f"{module}.crash_once"),)
        ),
        {"effect_path": str(effect)},
    )
    assert run.state == "pending"
    assert not effect.exists()
    monkeypatch.setattr(execute_workflow_step, "delay", publish)
    steps = WorkflowStepRecord.objects.filter(execution__execution_id=run.execution_id)
    steps.update(dispatch_until=timezone.now() - timedelta(seconds=1))
    assert publish_durable_steps() == 1

    with log.open("wb") as output:
        first = worker()
        try:
            assert first.wait(timeout=35) == 23, log.read_text()
        finally:
            if first.poll() is None:
                first.kill()
                first.wait(timeout=10)
        assert engine.status(run.execution_id).state == "running"
        assert steps.get().attempts == 1
        steps.update(claim_until=timezone.now() - timedelta(seconds=1))
        assert publish_durable_steps() == 1

        second = worker()
        try:
            import time

            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                execution = engine.status(run.execution_id)
                if execution.state == "completed":
                    break
                assert second.poll() is None, log.read_text()
                time.sleep(0.15)
            assert execution.state == "completed", log.read_text()
            assert execution.output_data == {"same_key": True}
            assert steps.get().attempts == 2
            assert effect.read_text() == f"{run.execution_id}:deliver"
        finally:
            if second.poll() is None:
                second.kill()
                second.wait(timeout=10)
            with (
                app.connection() as broker_connection,
                broker_connection.channel() as channel,
            ):
                channel.queue_delete(queue)
