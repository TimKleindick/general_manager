"""Stdio MCP server exposing the GeneralManager AI gateway tools."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from django.conf import settings

from general_manager.mcp.auth import MCPAuthError, build_context_from_auth_payload
from general_manager.mcp.contract import (
    GatewayOperation,
    MCPGatewayValidationError,
    QueryRequest,
)
from general_manager.mcp.service import GatewayService


JSON = dict[str, Any]


TOOL_SCHEMAS: dict[str, JSON] = {
    "discover_data_domains": {
        "type": "object",
        "properties": {
            "auth": {"type": "object"},
        },
        "required": ["auth"],
    },
    "describe_domain_schema": {
        "type": "object",
        "properties": {
            "auth": {"type": "object"},
            "domain": {"type": "string"},
        },
        "required": ["auth", "domain"],
    },
    "query_domain_data": {
        "type": "object",
        "properties": {
            "auth": {"type": "object"},
            "request": {"type": "object"},
        },
        "required": ["auth", "request"],
    },
    "aggregate_domain_metrics": {
        "type": "object",
        "properties": {
            "auth": {"type": "object"},
            "request": {"type": "object"},
        },
        "required": ["auth", "request"],
    },
    "explain_query_plan": {
        "type": "object",
        "properties": {
            "auth": {"type": "object"},
            "request": {"type": "object"},
        },
        "required": ["auth", "request"],
    },
}


def _gateway_config() -> dict[str, Any]:
    config = getattr(settings, "GENERAL_MANAGER", {})
    if isinstance(config, dict):
        gateway = config.get("MCP_GATEWAY", {})
        if isinstance(gateway, dict):
            return gateway
    return {}


class MCPServer:
    """Minimal JSON-RPC server for MCP tools over stdio."""

    def __init__(self) -> None:
        self._transport_mode: str | None = None
        self.service = GatewayService.from_settings()

    def run(self) -> None:
        while True:
            message = self._read_message()
            if message is None:
                return
            response = self._handle_message(message)
            if response is not None:
                self._write_message(response)

    def _handle_message(self, message: JSON) -> JSON | None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "general-manager-mcp", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            }

        if method == "notifications/initialized":
            return None

        if method == "tools/list":
            tools = [
                {
                    "name": name,
                    "description": f"GeneralManager MCP tool: {name}",
                    "inputSchema": schema,
                }
                for name, schema in TOOL_SCHEMAS.items()
            ]
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if name not in TOOL_SCHEMAS:
                return self._error(msg_id, -32602, f"Unknown tool: {name}")
            if not isinstance(arguments, dict):
                return self._error(msg_id, -32602, "Tool arguments must be an object")
            return self._call_tool(msg_id, str(name), arguments)

        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _call_tool(self, msg_id: Any, name: str, arguments: dict[str, Any]) -> JSON:
        try:
            auth_payload = arguments.get("auth")
            context = build_context_from_auth_payload(auth_payload, _gateway_config())
            if name == "discover_data_domains":
                payload = self.service.discover_data_domains(context).to_dict()
            elif name == "describe_domain_schema":
                domain = self._require_domain(arguments)
                payload = self.service.describe_domain_schema(domain, context).to_dict()
            else:
                request_payload = arguments.get("request")
                query_request = QueryRequest.from_payload(request_payload)
                if name == "aggregate_domain_metrics":
                    query_request.operation = GatewayOperation.AGGREGATE
                elif name == "explain_query_plan":
                    query_request.operation = GatewayOperation.EXPLAIN
                else:
                    query_request.operation = GatewayOperation.QUERY

                if query_request.operation is GatewayOperation.EXPLAIN:
                    payload = self.service.explain_query_plan(
                        query_request, context
                    ).to_dict()
                else:
                    payload = self.service.run_query(query_request, context).to_dict()
        except MCPAuthError as exc:
            payload = {
                "data": {"rows": [], "aggregates": {}, "page_info": {}},
                "provenance": {},
                "errors": [{"code": exc.code, "message": exc.message}],
            }
        except (MCPGatewayValidationError, ValueError) as exc:
            payload = {
                "data": {"rows": [], "aggregates": {}, "page_info": {}},
                "provenance": {},
                "errors": [{"code": "INVALID_REQUEST", "message": str(exc)}],
            }

        is_error = bool(payload.get("errors"))
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": is_error,
            },
        }

    @staticmethod
    def _require_domain(arguments: dict[str, Any]) -> str:
        domain = arguments.get("domain")
        if not isinstance(domain, str) or not domain:
            raise MCPGatewayValidationError.domain_argument_required()
        return domain

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> JSON:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    def _read_message(self) -> JSON | None:
        first_line = sys.stdin.buffer.readline()
        if not first_line:
            return None

        stripped = first_line.strip()
        if stripped.startswith(b"{"):
            self._transport_mode = "jsonl"
            return json.loads(stripped.decode("utf-8"))

        headers: dict[str, str] = {}
        line = first_line
        while True:
            if line in (b"\r\n", b"\n"):
                break
            text = line.decode("utf-8").strip()
            if ":" in text:
                key, value = text.split(":", 1)
                headers[key.strip().lower()] = value.strip()
            line = sys.stdin.buffer.readline()
            if not line:
                return None

        content_length = headers.get("content-length")
        if content_length is None:
            return None

        self._transport_mode = "lsp"
        body = sys.stdin.buffer.read(int(content_length))
        return json.loads(body.decode("utf-8"))

    def _write_message(self, payload: JSON) -> None:
        body = json.dumps(payload).encode("utf-8")
        if self._transport_mode == "jsonl":
            sys.stdout.buffer.write(body + b"\n")
        else:
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            sys.stdout.buffer.write(header)
            sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()


def main() -> None:
    if os.environ.get("DJANGO_SETTINGS_MODULE"):
        import django

        django.setup()
    MCPServer().run()


if __name__ == "__main__":
    main()
