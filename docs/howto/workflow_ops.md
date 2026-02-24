# Workflow Operations

This guide covers production operations for the workflow outbox and dead-letter flows.

## Drain outbox

Run:

```bash
python manage.py workflow_drain_outbox
```

This claims and routes pending outbox records.

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
