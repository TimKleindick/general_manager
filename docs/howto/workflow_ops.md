# Workflow Operations

This guide covers production operations for the workflow outbox and dead-letter flows.

## Drain outbox

Run:

```bash
python manage.py workflow_drain_outbox
```

This claims and routes pending outbox records.

In production, run a dedicated Celery Beat process to trigger periodic drain:

```bash
celery -A <your_project> beat -l info
```

The workflow library registers a Beat schedule for `publish_outbox_batch` when:
- `WORKFLOW_BEAT_ENABLED=True`
- Celery is installed and configured in the host app

## Replay dead letters

Run:

```bash
python manage.py workflow_replay_dead_letters --limit 500
```

This moves dead-letter outbox rows back to `pending`.

## Recommended metrics

- pending outbox count
- oldest pending outbox age
- failed/dead-letter outbox count
- workflow execution state totals
- outbox claim batch size
- outbox process duration
- delivery attempt status totals
- duplicate suppression totals
