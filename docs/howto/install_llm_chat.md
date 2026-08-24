# Add LLM chat to a GeneralManager project

GeneralManager can expose selected managers to an LLM through a grounded chat
API. GeneralManager supplies the provider adapters, conversation persistence,
schema-discovery tools, query execution, and HTTP, SSE, and WebSocket
transports. Your project supplies the domain managers, GraphQL schema, model
credentials, access policy, and user interface.

This tutorial starts with a read-only chat. Mutations remain disabled until you
explicitly allow them.

## What you need to provide

Before enabling chat, have these pieces ready:

- a Django project with `general_manager` in `INSTALLED_APPS`
- an initialized GeneralManager GraphQL schema
- at least one manager with `chat_exposed = True`
- one supported LLM provider and a tool-capable model
- provider credentials or a reachable local Ollama server
- a project-specific permission policy before production use
- a frontend that consumes one of the chat transports

Chat is a backend API. GeneralManager does not install a chat widget or other
browser UI.

## 1. Install a provider

Install exactly one provider extra to start:

=== "Ollama"

    ```bash
    python -m pip install "GeneralManager[chat-ollama]"
    ```

=== "OpenAI or compatible"

    ```bash
    python -m pip install "GeneralManager[chat-openai]"
    ```

    The OpenAI adapter also works with OpenAI-compatible endpoints by setting
    `base_url` and the provider's model name.

=== "Anthropic"

    ```bash
    python -m pip install "GeneralManager[chat-anthropic]"
    ```

=== "Gemini"

    ```bash
    python -m pip install "GeneralManager[chat-google]"
    ```

The selected model must support tool or function calling. A model that only
generates text cannot reliably discover managers or query project data.

## 2. Prepare Django and GraphQL

Chat stores conversations, messages, and pending mutation confirmations in the
GeneralManager Django app. Apply its migrations:

```bash
python manage.py migrate
```

Chat tools execute against GeneralManager's generated GraphQL schema. If your
project uses automatic schema generation, keep these settings enabled:

```python
# `general_manager` was added during base installation. Add the project app
# once if it is not already installed.
INSTALLED_APPS += ["inventory"]

AUTOCREATE_GRAPHQL = True
GRAPHQL_URL = "graphql/"
```

GeneralManager initializes chat after automatic GraphQL generation during
application startup. Projects that build the GraphQL schema themselves must
finish that initialization before chat is enabled. Otherwise Django reports
`general_manager.chat.E001`.

## 3. Choose which managers the LLM can see

Managers are hidden from chat by default. Opt in only the managers whose
schema and records are safe for the intended users:

```python
from general_manager.manager.general_manager import GeneralManager


class PartManager(GeneralManager):
    chat_exposed = True

    # Keep the project's existing Interface and fields here.
```

Setting `chat_exposed = True` makes the manager available to schema discovery,
path finding, and chat queries. It does not bypass the permissions already
enforced by the manager and GraphQL execution context. A hidden manager is
rejected even if the model guesses its name.

## 4. Configure the provider

Read secrets from environment variables; do not commit them in Django
settings. Add one of these configurations to the project settings module.

=== "Ollama"

    ```python
    GENERAL_MANAGER = {
        "CHAT": {
            "enabled": True,
            "provider": "general_manager.chat.providers.OllamaProvider",
            "provider_config": {
                "model": "qwen3.5:9b",
                "base_url": "http://127.0.0.1:11434",
            },
        }
    }
    ```

=== "OpenAI"

    ```python
    import os


    GENERAL_MANAGER = {
        "CHAT": {
            "enabled": True,
            "provider": "general_manager.chat.providers.OpenAIProvider",
            "provider_config": {
                "model": "gpt-4.1-mini",
                "api_key": os.environ["OPENAI_API_KEY"],
                "timeout_seconds": 60,
            },
        }
    }
    ```

=== "OpenAI-compatible"

    ```python
    import os


    GENERAL_MANAGER = {
        "CHAT": {
            "enabled": True,
            "provider": "general_manager.chat.providers.OpenAIProvider",
            "provider_config": {
                "model": os.environ["LLM_MODEL"],
                "base_url": os.environ["LLM_BASE_URL"],
                "api_key": os.environ["LLM_API_KEY"],
                "timeout_seconds": 60,
            },
        }
    }
    ```

=== "Anthropic"

    ```python
    import os


    GENERAL_MANAGER = {
        "CHAT": {
            "enabled": True,
            "provider": "general_manager.chat.providers.AnthropicProvider",
            "provider_config": {
                "model": "claude-3-5-haiku-latest",
                "api_key": os.environ["ANTHROPIC_API_KEY"],
                "max_tokens": 1024,
                "timeout_seconds": 60,
            },
        }
    }
    ```

=== "Gemini"

    ```python
    import os


    GENERAL_MANAGER = {
        "CHAT": {
            "enabled": True,
            "provider": "general_manager.chat.providers.GeminiProvider",
            "provider_config": {
                "model": "gemini-2.5-flash",
                "api_key": os.environ["GOOGLE_API_KEY"],
            },
        }
    }
    ```

When chat is enabled, GeneralManager automatically appends these routes using
the configured `url`, which defaults to `/chat/`:

| Route | Transport | Purpose |
| --- | --- | --- |
| `POST /chat/` | JSON over HTTP | Return one complete response |
| `POST /chat/stream/` | Server-sent events | Stream one response |
| `POST /chat/confirm/` | JSON over HTTP | Resolve a pending SSE mutation |
| `/chat/` | WebSocket | Stream messages and mutation confirmations |

HTTP and SSE require `ROOT_URLCONF`. WebSocket chat also requires a valid
`ASGI_APPLICATION` and an ASGI server. GeneralManager adds its authenticated,
origin-validated WebSocket route to an inspectable Channels router during
startup.

## 5. Add an access policy

The default `permission` is `None`, which does not reject callers. Require
authentication before deploying chat:

```python
# myproject/chat_permissions.py
def can_use_chat(user, scope):
    return bool(user and user.is_authenticated)
```

Reference the callable and restrict WebSocket origins:

```python
GENERAL_MANAGER["CHAT"].update(
    {
        "permission": "myproject.chat_permissions.can_use_chat",
        "allowed_origins": ["https://app.example.com"],
        "rate_limit": {
            "requests": 60,
            "window_seconds": 3600,
            "tokens": 100_000,
        },
    }
)
```

The permission callable receives `(user, scope)`. The scope contains the
current `user`, `session`, and client address for every transport. Return
`False` to deny access.

## 6. Verify startup

Run Django's system checks after setting credentials and before starting the
server:

```bash
python manage.py check
```

The checks catch a missing GraphQL schema, an unavailable provider extra, an
invalid permission callable, and mutation names that are not present in the
GraphQL schema.

Start the project with its normal ASGI development command. For example, when
Daphne is installed:

```bash
daphne myproject.asgi:application
```

Use the project's normal WSGI development server only for the non-streaming
HTTP endpoint. Incremental SSE and WebSocket delivery require ASGI; a WSGI
server buffers the async SSE iterator instead of delivering each event live.

## 7. Send the first message

For a same-origin browser client, send JSON to the non-streaming endpoint. The
view is CSRF-protected, so include the normal Django CSRF token. This example
reads a token rendered by `{% csrf_token %}` in the surrounding Django
template:

```javascript
const csrfToken = document.querySelector(
  "[name=csrfmiddlewaretoken]",
).value;

const response = await fetch("/chat/", {
  method: "POST",
  credentials: "same-origin",
  headers: {
    "Content-Type": "application/json",
    "X-CSRFToken": csrfToken,
  },
  body: JSON.stringify({text: "Which parts are made of aluminum?"}),
});

const payload = await response.json();
console.log(payload.answer);
console.log(payload.events);
```

The response has an assembled `answer` and the ordered event list:

```json
{
  "events": [
    {"type": "tool_call", "id": "call-1", "name": "query", "args": {}},
    {"type": "tool_result", "id": "call-1", "name": "query", "result": {}},
    {"type": "text_chunk", "content": "The aluminum part is ..."},
    {"type": "done", "usage": {"input_tokens": 120, "output_tokens": 24}}
  ],
  "answer": "The aluminum part is ..."
}
```

The exact tool arguments and results depend on the project schema. Render
assistant text from `text_chunk` events and treat `done` as the end of the
turn.

## 8. Stream over SSE

The SSE endpoint accepts the same JSON body and emits one JSON event per SSE
frame. Because the request is POST, consume it with streaming `fetch` instead
of the browser's GET-only `EventSource` API:

```javascript
const response = await fetch("/chat/stream/", {
  method: "POST",
  credentials: "same-origin",
  headers: {
    "Accept": "text/event-stream",
    "Content-Type": "application/json",
    "X-CSRFToken": csrfToken,
  },
  body: JSON.stringify({text: "List all materials"}),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const {value, done} = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, {stream: true});
  const frames = buffer.split("\n\n");
  buffer = frames.pop();
  for (const frame of frames) {
    if (!frame.startsWith("data: ")) continue;
    const event = JSON.parse(frame.slice(6));
    if (event.type === "text_chunk") renderAssistantText(event.content);
  }
}
```

## 9. Stream over WebSocket

Use WebSocket when the UI should receive tokens and tool activity as they
happen, or when confirmed mutations are enabled:

```javascript
const scheme = location.protocol === "https:" ? "wss" : "ws";
const socket = new WebSocket(`${scheme}://${location.host}/chat/`);

socket.addEventListener("open", () => {
  socket.send(JSON.stringify({
    type: "message",
    text: "List the available part managers",
  }));
});

socket.addEventListener("message", ({data}) => {
  const event = JSON.parse(data);
  if (event.type === "text_chunk") {
    renderAssistantText(event.content);
  } else if (event.type === "error") {
    renderChatError(event.message);
  }
});
```

Sessions and Django authentication identify the conversation. Authenticated
users reuse their latest conversation; anonymous users require a valid Django
session key.

## 10. Add writes only when required

Read-only chat is the safe default: `allowed_mutations` and
`confirm_mutations` are empty. To permit a generated GraphQL mutation, add its
exact schema name to the allow-list:

```python
GENERAL_MANAGER["CHAT"].update(
    {
        "allowed_mutations": ["createPart"],
        "confirm_mutations": ["createPart"],
        "confirm_timeout_seconds": 30,
    }
)
```

Every confirmed mutation must also be allowed. Mutations require an
authenticated user. When the model requests `createPart`, WebSocket or SSE
emits a `confirm_mutation` event instead of executing it. A WebSocket client
answers with:

```javascript
socket.send(JSON.stringify({
  type: "confirm",
  confirmation_id: event.id,
  confirmed: true,
}));
```

The plain HTTP endpoint cannot pause for confirmation. Use WebSocket or SSE for
workflows that include `confirm_mutations`.

## 11. Prepare for production

Before serving real users:

- expose the minimum set of managers
- require a permission callable and test it for every user role
- keep mutations disabled or explicitly allow and confirm each mutation
- set `allowed_origins` for WebSockets and preserve Django CSRF protection
- use a shared Django cache so rate limits work across processes
- set request and token budgets appropriate for the provider account
- configure `max_results` and `query_timeout_seconds`
- decide whether chat audit logging is appropriate for the data involved
- schedule `python manage.py chat_cleanup` using the configured `ttl_hours`
- run the [installed chat eval suite](run_chat_evals.md) against the chosen model

For the runtime mental model, continue with [How LLM chat works](../concepts/chat.md).
For every setting, endpoint, and event shape, see the [Chat API reference](../api/chat.md).
