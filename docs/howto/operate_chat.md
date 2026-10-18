# Operate and troubleshoot chat

Use [installation](install_llm_chat.md) and [configuration](configure_chat.md) to
establish a known starting point. This guide covers normal application operation;
private experiment admission and diagnostic profiles are separate.

## Check the deployment boundary

Run `python manage.py check` with the deployed settings. Verify the native
GraphQL schema is initialized before chat, exposed managers are intentional,
provider extras are installed and all explicit role mappings share one trust
group. The system check IDs and public errors are listed in the
[API reference](../api/chat.md#django-system-checks).

Use authenticated request/session middleware, a deliberate chat `permission`
callable, Django CSRF protection for HTTP/SSE and configured WebSocket origins.
A missing permission callable adds no endpoint-level denial. Native GraphQL
permissions must still enforce actual record/field access. An exposure flag or
catalog entry is not authorization.

Use ASGI for incremental SSE/WebSocket delivery. A WSGI server can buffer an
async SSE iterator. Verify reverse-proxy streaming/timeouts with the actual
frontend and deployment; successfully receiving one complete HTTP response does
not prove incremental streaming works.

## Observe outcomes and usage

Consume all events through the terminal. `done` includes usage and planned
coverage; inspect `orchestration.status` and `unresolved` rather than treating
HTTP 200 as a complete answer. A partial result can contain valid evidence for
resolved work while reporting a blocked or timed-out task. An identity question
can correctly have zero resolved tasks and `clarification_required`.

Use stable public codes for client behavior and correlate server-side audit
records for diagnosis. Planned audit events allow lineage/call hashes, role,
progress, budgets, latency, observed usage, evidence counts, coverage and terminal
reason. They exclude raw results, plans, hidden manager data, profile names,
trust groups, credentials and provider exceptions. The generic audit hook also
has configured key redaction and truncation; free text can still be sensitive.
Choose retention/access rules for your own audit sink.

Django signals support application instrumentation for accepted messages, tool
calls, mutation outcomes and errors. Receivers should preserve request behavior
and avoid throwing into critical persistence/delivery callbacks. See
[signals](../api/chat.md#signals) for exact arguments.

## Diagnose common failures

| Observation | Check and next action |
| --- | --- |
| Startup `E001` | Initialize GeneralManager's executable GraphQL schema before chat. Do not create a private chat-only schema. |
| Startup `E002` | Check settings, exact mutation names, profile roles and trust groups. |
| Startup `E003`/`E004` | Install the selected optional provider extra or fix its import/configuration. |
| HTTP 403 / rejected socket | Check session/authentication, CSRF/origin policy and the configured chat permission callable. |
| Manager absent | Check exact registered name and `chat_exposed`; aliases do not expose a hidden manager. |
| `invalid_plan` | Inspect the custom provider's exact planner JSON contract and contextual envelope. Existing correction/fallback attempts are bounded. |
| `manager_unresolved` | Improve exact manager/catalog descriptions, or verify the requested data is exposed and exists. |
| `schema_snapshot_mismatch` | Obtain a fresh manager overview and then targeted details from that snapshot. |
| Repeated incompatible schema feedback | Compare manager, view, exact type set, links and current snapshot. Repeating the same selector does not widen it. |
| `invalid_category_binding` | Read its stable reason: link the correct same-task schema, observe filter definitions, and use the advertised argument/filter path. |
| Partial population | Fetch all required root/nested pages or clearly restrict the answer to the observed page. |
| Calculation rejected | Inspect actual numeric leaf paths, complete populations, required grouping and declared unit/identity witnesses. Do not substitute guessed paths. |
| `deadline_exceeded` | Separate model/network latency, schema exploration, query work and synthesis; inspect recorded stage settings and observed cancellation. |
| `provider_failed` | Check actual provider availability, configured destination and server-side technical evidence. Public wording alone cannot identify a timeout origin. |
| `synthesis_failed` | Check eligible evidence IDs, response shape, source-bound choices and fallback outcome. |
| `confirmation_unavailable` | The pending mutation was already claimed, resolved or expired; refresh state instead of replaying a write. |
| `rate_limited` | Honor `retry_after_seconds` and inspect the shared cache/budget configuration. |

If all planner attempts raise provider-stream exceptions, the terminal is
`provider_failed`. If an attempt instead has malformed content/stream shape,
request preparation failure or another rejected-plan condition, a mixed terminal
retains `invalid_plan`. A later valid response succeeds under the existing attempt
budget. Lower-level provider classification is opt-in; it is not an extra retry.

Tool schema errors are corrective feedback and create no factual evidence.
Unexpected failures keep generic public wording. Never expose credentials or raw
provider exceptions to make a client error more descriptive.

## Investigate deadlines honestly

The default stage settings are 90 seconds for planning/evidence and 30 seconds
for synthesis. A configured query timeout bounds supported PostgreSQL statement
work on the default connection. Other backend/thread cancellation is best effort.
A cancelled await alone does not prove remote model completion or database abort.

The Python prepared-turn cancellation observations distinguish actual scheduler
cancel requests from boundaries that observed cancellation. Captured executor
failures may attach a sanitized `gm_eval_interruption` diagnostic to the original
exception. Use that record when available; absence of a cause is unknown rather
than proof of a deadline. These diagnostics add no resumability, retry allowance
or permission to continue a failed experiment.

Before increasing a deadline, first inspect repeated invalid action cycles,
wrong schema selectors, missing filter details and duplicate incomplete reads.
Test changes under explicit configuration and retain the original results. An
extended diagnostic success does not establish compliance with production defaults.

## Manage conversations and cleanup

Chat persists conversations, messages and pending mutation confirmations in the
existing GeneralManager app. Authenticated users reuse their latest conversation;
anonymous conversations need a saved session key. `summarize_after` and
`max_recent_messages` control context summarization, not database deletion.

Run cleanup through the normal management command:

```bash
python manage.py chat_cleanup
```

Schedule it with the application's normal job runner; GeneralManager does not
schedule it automatically. `ttl_hours` controls the stale-conversation threshold.
Review application retention requirements and backups before changing that setting.

## Roll out or roll back

Validate the exact deployment source, configuration, schema and model on
representative whole conversations. Cover hidden relations, current permissions,
missing values, pagination, answered follow-up questions and every enabled write
confirmation. Read [evaluation concepts](../concepts/chat_evaluation.md) before
interpreting a green infrastructure or live score.

Deploy schema changes through a rebuilt executable schema/process generation;
do not mutate live metadata or clear caches per request. If the release regresses,
roll back the application or set global `CHAT.enabled=False`. Preserve existing
conversation data and historical evaluation results for diagnosis. No setting
restores the removed legacy read executor.
