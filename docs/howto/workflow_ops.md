# Workflow Operations

This guide covers production operations for the workflow outbox and dead-letter flows.

## Drain outbox

Run:

```bash
python manage.py workflow_drain_outbox
```

This claims and routes pending outbox records.

Operator notes:
- claiming an outbox row does not consume its retry budget by itself.
- retry counters advance only when routing or handler execution actually fails.
- stale claims can be reclaimed after the configured claim TTL without dead-lettering the event just because a worker lease expired.

In production, run a dedicated Celery Beat process to trigger periodic drain:

```bash
celery -A <your_project> beat -l info
```

The workflow library registers a Beat schedule for `publish_outbox_batch` when:
- `WORKFLOW_BEAT_ENABLED=True`
- Celery is installed and configured in the host app

Backlog semantics:
- retry-ready rows move back to `failed` with a future `available_at` timestamp and are reclaimed when that backoff window expires.
- dead-lettering happens after repeated real processing failures, not after repeated claim churn.

## Replay dead letters

Run:

```bash
python manage.py workflow_replay_dead_letters --limit 500
```

This moves dead-letter outbox rows back to `pending`.

Use replay when the underlying handler or dependency issue is fixed; replay does not bypass normal retry and claim rules.

## Recommended metrics

- pending outbox count
- oldest pending outbox age
- failed/dead-letter outbox count
- workflow execution state totals
- outbox claim batch size
- outbox process duration
- delivery attempt status totals
- duplicate suppression totals
