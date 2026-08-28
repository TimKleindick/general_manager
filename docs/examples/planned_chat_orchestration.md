# Planned Chat Orchestration

Use planned chat for complex read requests that benefit from separate planning,
evidence gathering, and grounded synthesis. It is opt-in, read-only, and uses
the existing HTTP, SSE, and WebSocket chat routes. Mutations continue through
the legacy allow-list and confirmation flow.

## Configure one local profile

Install the provider extra and expose the managers that the catalog names:

```bash
python -m pip install "GeneralManager[chat-ollama]"
```

In the Django settings module:

```python
GENERAL_MANAGER = {
    "CHAT": {
        "enabled": True,
        "provider": "general_manager.chat.providers.OllamaProvider",
        "provider_config": {"model": "gemma4:e4b"},
        "planned": {
            "enabled": True,
            "catalog": "myproject.chat.catalog.catalog",
        },
    }
}
```

With no `provider_profiles`, planned chat assigns the legacy provider to all
five roles through the implicit `default` profile. For separate role models,
use the explicit profile mapping in the [rollout how-to](../howto/run_chat_evals.md#5-roll-out-planned-chat-safely).

Create the catalog callable named by `planned.catalog`:

```python
# myproject/chat/catalog.py
def catalog():
    return {
        "PartManager": {
            "domain": "manufacturing",
            "aliases": ["part", "component"],
            "use_when": "The question concerns designed or purchased components.",
            "distinguish_from": ["MaterialManager"],
        },
        "MaterialManager": {
            "domain": "manufacturing materials",
            "aliases": ["material", "substance"],
            "use_when": "The question concerns material definitions.",
            "distinguish_from": ["PartManager"],
        },
    }
```

The manager names must already be present in the chat-exposed schema. The
catalog helps rank candidates; it does not grant visibility or bypass
permissions. Run `python manage.py check` before sending traffic.

## Send a read over SSE

The stream endpoint is a POST endpoint, so use a streaming client rather than
the browser's GET-only `EventSource` constructor:

```bash
curl -N -X POST http://localhost:8000/chat/stream/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -H 'Cookie: csrftoken=<csrf-token>' \
  -H 'X-CSRFToken: <csrf-token>' \
  --data '{"text":"Which parts use aluminum?"}'
```

A representative successful stream keeps the normal event vocabulary while
adding the owning task ID to actual tool events:

```text
data: {"type":"tool_call","task_id":"<task-id>","id":"<call-id>","name":"query","args":{...}}

data: {"type":"tool_result","task_id":"<task-id>","id":"<call-id>","name":"query","result":{...}}

data: {"type":"text_chunk","content":"Aluminum is used by ..."}

data: {"type":"done","usage":{"input_tokens":12,"output_tokens":8},"orchestration":{"status":"complete","coverage":{"resolved":1,"total":1},"unresolved":[]}}
```

Independent roots may produce partial coverage. In that case `done.orchestration.status`
is `partial`, `coverage.resolved` is less than `coverage.total`, and
`unresolved` lists only stable task IDs and reasons. If no root resolves, the
stream ends with one error event and no synthesized answer. The stable planned
error codes are `invalid_plan`, `manager_unresolved`, `dependency_blocked`,
`budget_exhausted`, `deadline_exceeded`, `provider_failed`, and
`synthesis_failed`.

HTTP returns the same ordered events in an `{events, answer}` JSON envelope,
and WebSocket clients receive the same planned event shapes. Planned execution
is held in memory for one request; disconnects and process restarts do not
resume it. See the [concept model](../concepts/chat_prompting.md#planned-orchestration-bounds-and-grounding)
and [API reference](../api/chat.md#planned-read-orchestration) for the bounds,
settings, signatures, and exact error messages.
