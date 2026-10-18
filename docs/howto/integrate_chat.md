# Integrate HTTP, SSE and WebSocket clients

Install/configure chat first, including authentication, endpoint admission and
manager exposure. All read transports use the same scheduler and event contract.
Choose a transport for delivery and confirmation needs, not a different model
or permission path. Exact payloads are in the [Chat API](../api/chat.md#routes-and-request-contracts).

## Choose a transport

| Transport | Request | Delivery | Confirmed writes |
| --- | --- | --- | --- |
| HTTP | `POST /chat/` with `{"text": "..."}` | `{events, answer}` after the turn | A pause requiring confirmation returns `confirmation_required_transport` |
| SSE | `POST /chat/stream/` with the same body | JSON `data:` frames | `POST /chat/confirm/` with the same Django session |
| WebSocket | `/chat/`, message `{type:"message", text:"..."}` | JSON events | Send `type:"confirm"` on that socket |

Paths follow the configured chat base URL. HTTP/SSE are CSRF-protected. Browser
WebSocket connections use the session authentication and configured origin policy.
SSE requires streaming `fetch` or another POST-capable SSE client; the browser's
GET-only `EventSource` constructor does not send this request shape.

## Send an HTTP message

A Django template can render `{% csrf_token %}` before this browser code:

```javascript
const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]").value;
const response = await fetch("/chat/", {
  method: "POST",
  credentials: "same-origin",
  headers: {
    "Content-Type": "application/json",
    "X-CSRFToken": csrfToken,
  },
  body: JSON.stringify({text: "List the available materials"}),
});
const payload = await response.json();
for (const event of payload.events ?? []) handleChatEvent(event);
```

`handleChatEvent` is your application's renderer/dispatcher. Render model text as
text or sanitized Markdown according to your UI's security policy. Tool payloads
are server observations, never browser commands to execute.

## Consume SSE

Use an SSE parser that supports POST, incremental UTF-8, frame boundaries,
multiline data, cancellation and the deployment's buffering behavior. Send the
same body and CSRF/session headers as HTTP plus `Accept: text/event-stream`.
Each event is JSON in an SSE `data:` frame. A diagnostic curl request is shown
in the [cookbook](../examples/planned_chat_orchestration.md#inspect-a-stream).

Keep reading through `done` or `error`. A connection closing before the terminal
is not proof of a complete answer. SSE cancellation/disconnect ends turn-local
execution; it does not resume automatically from the last tool event.

## Consume WebSocket events

```javascript
const scheme = location.protocol === "https:" ? "wss" : "ws";
const socket = new WebSocket(`${scheme}://${location.host}/chat/`);
socket.addEventListener("open", () => {
  socket.send(JSON.stringify({type: "message", text: "List materials"}));
});
socket.addEventListener("message", ({data}) => {
  handleChatEvent(JSON.parse(data));
});
```

Do not start another turn while one is streaming or while its confirmation is
pending. Handle `turn_in_progress` and `confirmation_pending` explicitly.
Reconnect establishes a transport; it does not resume the old scheduler.

## Handle events and coverage

| Event | Client action |
| --- | --- |
| `tool_call` | Optionally show server-side work; preserve call/task IDs for diagnosis. |
| `tool_result` | Match by call ID; never infer task completion from success alone. |
| `text_chunk` | Append answer/question text. |
| `confirm_mutation` | Present exact proposed mutation/input and obtain a user decision. |
| `done` | Finish the turn, inspect usage and `orchestration` coverage/unresolved reasons. |
| `error` | Finish the failed turn and show its stable public message/code. |

A partial `done` can carry a useful answer with unresolved tasks. A rendered
record-choice question may report `clarification_required` and zero resolved
tasks. Keep that distinction visible rather than displaying every terminal as a
fully answered request. Internal plans, calculation graphs and evidence stores
are not automatically appended to the public answer.

## Resolve a confirmation

Only names present in `allowed_mutations` may execute, and only authenticated
users may mutate. Names in `confirm_mutations` pause before execution. Show the
actual proposal, preserve its ID and send the actual user's decision:

```javascript
function confirmOnSocket(confirmationId, confirmed) {
  socket.send(JSON.stringify({
    type: "confirm",
    confirmation_id: confirmationId,
    confirmed,
  }));
}
```

For SSE, POST the decision to the confirmation route using the same session:

```javascript
async function confirmSse(confirmationId, confirmed, streamResponseUrl) {
  const base = new URL("../", streamResponseUrl);
  return fetch(new URL("confirm/", base), {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken,
    },
    body: JSON.stringify({confirmation_id: confirmationId, confirmed}),
  });
}
```

Deriving the base from the stream request preserves a customized chat URL. A
confirmation times out after its configured lifetime and is transactionally
claimed so competing requests cannot execute it twice. Do not automatically
replay confirmations or uncertain writes after transport failure. A user's
analytical choice or chat answer is not mutation approval.

## Own a custom Python transport

Normal integrations should use the existing routes. Lower-level integrations
use `prepare_planned_turn` and `iter_planned_read_events` with the appropriate
context and callbacks; consult their generated
[API signatures](../api/chat.md#planned-orchestration-helpers). Preserve validated
choice context, conversation persistence, native GraphQL user/request context,
rate accounting, terminal cleanup and mutation handoff. Calling a query helper
alone does not reproduce these endpoint responsibilities.
