from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings

from general_manager.workflow.event_registry import DatabaseEventRegistry, WorkflowEvent
from general_manager.workflow.models import WorkflowOutbox


@pytest.mark.perf
@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={
        "WORKFLOW_MODE": "production",
        "WORKFLOW_ASYNC": True,
        "WORKFLOW_OUTBOX_PROCESS_CHUNK_SIZE": 50,
    }
)
def test_workflow_outbox_batch_claim_perf_smoke() -> None:
    registry = DatabaseEventRegistry()
    registry.register("invoice.created", handler=lambda _event: None)

    total_events = 200
    with patch.object(
        DatabaseEventRegistry, "_enqueue_publish_task", return_value=None
    ):
        for idx in range(total_events):
            event = WorkflowEvent(
                event_id=f"evt-perf-{idx}",
                event_type="invoice.created",
                payload={"invoice_id": idx},
            )
            assert registry.publish(event) is False

    claimed_total = 0
    while True:
        claims = registry.claim_outbox_batch(batch_size=50)
        if not claims:
            break
        claimed_total += len(claims)
        for outbox_id, claim_token in claims:
            registry.process_outbox_entry(outbox_id, claim_token=claim_token)

    assert claimed_total == total_events
    assert (
        WorkflowOutbox.objects.filter(status=WorkflowOutbox.STATUS_PROCESSED).count()
        == total_events
    )
