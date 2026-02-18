# MCP Chat Gateway

This guide shows how to expose a permission-safe, read-only AI query gateway in a Django project using GeneralManager.

## 1. Enable the gateway

Add a domain allowlist in `settings.py`:

```python
GENERAL_MANAGER = {
    "MCP_GATEWAY": {
        "ENABLED": True,
        "DOMAINS": {
            "Project": {
                "manager": "Project",
                "readable_fields": ["id", "name", "status", "budget"],
                "filterable_fields": ["status", "name"],
                "sortable_fields": ["name", "status"],
                "aggregate_fields": ["budget"],
            }
        },
    }
}
```

## 2. Expose HTTP endpoint for website chat

No `urls.py` change is required when the gateway is enabled.
GeneralManager auto-registers the MCP route include during app startup.

Optional URL prefix:

```python
MCP_GATEWAY_URL = ""  # default, exposes /ai/query
```

Example:

```python
MCP_GATEWAY_URL = "assistant/"  # exposes /assistant/ai/query
```

## 3. Use stdio MCP server for external clients

Run:

```bash
python -m general_manager.mcp.server_stdio
```

Example Codex config:

```toml
[mcp_servers.general_manager]
command = "python"
args = ["-m", "general_manager.mcp.server_stdio"]
```

## 4. Request contract

Structured requests are required. Freeform text is not accepted by the gateway.

```json
{
  "domain": "Project",
  "operation": "query",
  "select": ["id", "name", "status"],
  "filters": [{"field": "status", "op": "eq", "value": "active"}],
  "sort": [{"field": "name", "direction": "asc"}],
  "page": 1,
  "page_size": 50,
  "group_by": [],
  "metrics": []
}
```

## 5. Authentication

- HTTP adapter uses `request.user` (or optional custom `AUTH_RESOLVER`).
- MCP stdio adapter expects `auth.user_id` by default.
- All query execution runs through GraphQL resolvers with existing GeneralManager permission checks.
