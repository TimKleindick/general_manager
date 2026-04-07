"""Tool registry for chat."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from contextlib import nullcontext
from typing import Any, Protocol

from django.db import connection

from general_manager.api.graphql import GraphQL
from general_manager.chat.rate_limits import get_query_timeout_ms
from general_manager.chat.schema_index import (
    build_schema_index,
    find_exposed_path,
    get_manager_schema_summary,
    search_manager_summaries,
)
from general_manager.chat.settings import get_chat_settings
from general_manager.chat.tool_metadata import TOOL_DESCRIPTIONS, TOOL_INPUT_SCHEMAS


class ChatToolContext(Protocol):
    """Minimal request context needed by chat tools."""

    user: Any


class ScopeChatContext:
    """Adapter that exposes only the attributes chat tools need."""

    def __init__(self, *, user: Any) -> None:
        self.user = user

    @classmethod
    def from_scope(cls, scope: Mapping[str, Any]) -> ScopeChatContext:
        return cls(user=scope.get("user"))


class InvalidFieldSelectionError(TypeError):
    """Raised when a chat field-tree selection is malformed."""

    def __init__(self) -> None:
        super().__init__("Field selections must be strings or nested mappings.")


class InvalidNestedFieldSelectionError(TypeError):
    """Raised when a nested field selection is not sequence-shaped."""

    def __init__(self) -> None:
        super().__init__("Nested field selections must be sequences.")


class ManagerNotChatExposedError(ValueError):
    """Raised when a tool targets a manager hidden from chat."""

    def __init__(self, manager: str) -> None:
        super().__init__(f"Manager '{manager}' is not chat-exposed.")


class ChatSchemaNotInitializedError(ValueError):
    """Raised when chat tools run before the GraphQL schema exists."""

    def __init__(self) -> None:
        super().__init__("GraphQL schema is not initialized.")


class MutationNotAllowedError(ValueError):
    """Raised when a chat mutation is not in the allow-list."""

    def __init__(self, mutation: str) -> None:
        super().__init__(f"Mutation '{mutation}' is not allowed.")


class MutationAuthenticationRequiredError(ValueError):
    """Raised when an anonymous user attempts a chat mutation."""

    def __init__(self) -> None:
        super().__init__("Chat mutations require an authenticated user.")


class UnknownChatToolError(ValueError):
    """Raised when the provider requests a tool that does not exist."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown chat tool '{name}'.")


def get_tool_definitions() -> list[dict[str, Any]]:
    """Return the chat tools exposed to the provider."""
    if get_chat_settings().get("tool_strategy") == "direct":
        return _get_direct_tool_definitions()
    return [
        {
            "name": name,
            "description": description,
            "input_schema": dict(TOOL_INPUT_SCHEMAS[name]),
        }
        for name, description in TOOL_DESCRIPTIONS.items()
    ]


def execute_chat_tool(
    name: str, args: Mapping[str, Any], context: ChatToolContext | None
) -> Any:
    """Dispatch a named chat tool."""
    direct_manager = _manager_name_from_direct_tool(name)
    if direct_manager is not None:
        return query(
            manager=direct_manager,
            filters=args.get("filters", {}),
            fields=args.get("fields", []),
            limit=args.get("limit"),
            offset=int(args.get("offset", 0)),
            context=context,
        )
    if name == "search_managers":
        return search_managers(str(args.get("query", "")))
    if name == "get_manager_schema":
        return get_manager_schema(str(args.get("manager", "")))
    if name == "find_path":
        return find_path(
            str(args.get("from_manager", "")), str(args.get("to_manager", ""))
        )
    if name == "query":
        return query(
            manager=str(args.get("manager", "")),
            filters=args.get("filters", {}),
            fields=args.get("fields", []),
            limit=args.get("limit"),
            offset=int(args.get("offset", 0)),
            context=context,
        )
    if name == "mutate":
        return mutate(
            mutation=str(args.get("mutation", "")),
            input=args.get("input", {}),
            confirmed=bool(args.get("confirmed", False)),
            context=context,
        )
    raise UnknownChatToolError(name)


def _manager_name_from_direct_tool(name: str) -> str | None:
    if not name.startswith("query_"):
        return None
    suffix = name.removeprefix("query_")
    for manager_name in build_schema_index():
        if manager_name.lower() == suffix:
            return manager_name
    return None


def _get_direct_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": f"query_{manager_name.lower()}",
            "description": f"Query {manager_name} records directly.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "filters": {"type": "object"},
                    "fields": {
                        "type": "array",
                        "items": {"type": ["string", "object"]},
                    },
                    "limit": {"type": "integer"},
                    "offset": {"type": "integer"},
                },
                "required": ["fields"],
            },
        }
        for manager_name in build_schema_index()
    ]


def search_managers(query: str) -> list[dict[str, Any]]:
    """Search exposed managers by natural-language text."""
    return search_manager_summaries(query)


def get_manager_schema(manager: str) -> dict[str, Any] | None:
    """Return a compact schema description for one manager."""
    return get_manager_schema_summary(manager)


def find_path(from_manager: str, to_manager: str) -> list[str] | None:
    """Return a traversal path between two exposed managers."""
    return find_exposed_path(from_manager, to_manager)


def _camelize_segment(name: str) -> str:
    parts = name.split("_")
    if len(parts) > 1 and all(not part or part[:1].isupper() for part in parts[1:]):
        return name
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _camelize(name: str) -> str:
    if "__" not in name:
        return _camelize_segment(name)
    segments = name.split("__")
    head = _camelize_segment(segments[0])
    tail = []
    for segment in segments[1:]:
        converted = _camelize_segment(segment)
        tail.append(f"_{converted[:1].upper() + converted[1:]}")
    return head + "".join(tail)


def _graphql_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, Mapping):
        inner = ", ".join(
            f"{_camelize(str(key))}: {_graphql_literal(inner_value)}"
            for key, inner_value in value.items()
        )
        return "{" + inner + "}"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        inner = ", ".join(_graphql_literal(item) for item in value)
        return "[" + inner + "]"
    return str(value)


def _build_selection(fields: Sequence[Any]) -> str:
    selections: list[str] = []
    for field in fields:
        if isinstance(field, str):
            selections.append(field)
            continue
        if isinstance(field, Mapping):
            for name, nested in field.items():
                if not isinstance(nested, Sequence):
                    raise InvalidNestedFieldSelectionError()
                selections.append(f"{name} {{ {_build_selection(list(nested))} }}")
            continue
        raise InvalidFieldSelectionError()
    return " ".join(selections)


def _list_query_field_name(manager: str) -> str:
    return f"{manager.lower()}List"


def _ensure_exposed_manager(manager: str) -> None:
    manager_class = GraphQL.manager_registry.get(manager)
    if manager_class is None or not getattr(manager_class, "chat_exposed", True):
        raise ManagerNotChatExposedError(manager)


def _extract_error_message(error: Any) -> str:
    message = getattr(error, "message", None)
    return str(message if message is not None else error)


def _get_authenticated_user(context: ChatToolContext | None) -> Any:
    user = getattr(context, "user", None)
    if user is None or not bool(getattr(user, "is_authenticated", False)):
        raise MutationAuthenticationRequiredError()
    return user


def query(
    *,
    manager: str,
    filters: Mapping[str, Any],
    fields: Sequence[Any],
    limit: int | None = None,
    offset: int = 0,
    context: ChatToolContext | None = None,
) -> dict[str, Any]:
    """Execute a structured GraphQL list query for an exposed manager."""
    _ensure_exposed_manager(manager)
    schema = GraphQL.get_schema()
    if schema is None:
        raise ChatSchemaNotInitializedError()

    max_results = get_chat_settings().get("max_results")
    effective_limit = limit
    if isinstance(max_results, int) and max_results > 0:
        effective_limit = max_results if limit is None else min(limit, max_results)

    page_size = (offset + effective_limit) if effective_limit is not None else None
    list_field_name = _list_query_field_name(manager)
    arguments: list[str] = []
    if filters:
        arguments.append(f"filter: {_graphql_literal(filters)}")
    if page_size is not None:
        arguments.append(f"pageSize: {page_size}")
    argument_block = f"({', '.join(arguments)})" if arguments else ""
    selection = _build_selection(fields)
    query_text = (
        "query ChatQuery { "
        f"{list_field_name}{argument_block} "
        "{ items { "
        f"{selection} "
        "} pageInfo { totalCount } } }"
    )

    timeout_ms = get_query_timeout_ms()
    timeout_context: AbstractContextManager[Any | None] = nullcontext()
    if timeout_ms is not None and getattr(connection, "vendor", None) == "postgresql":
        timeout_context = connection.cursor()
    with timeout_context as cursor:
        if cursor is not None:
            cursor.execute("SET LOCAL statement_timeout = %s", [timeout_ms])
        result = schema.execute(query_text, context_value=context)
    errors = getattr(result, "errors", None)
    if errors:
        raise ValueError("; ".join(_extract_error_message(error) for error in errors))

    payload = getattr(result, "data", {}).get(list_field_name, {})
    items = list(payload.get("items", []))
    if offset:
        items = items[offset:]
    if effective_limit is not None:
        items = items[:effective_limit]
    total_count = int(payload.get("pageInfo", {}).get("totalCount", len(items)))
    return {
        "data": items,
        "total_count": total_count,
        "has_more": total_count > offset + len(items),
    }


def mutate(
    *,
    mutation: str,
    input: Mapping[str, Any],
    confirmed: bool = False,
    context: ChatToolContext | None = None,
) -> dict[str, Any]:
    """Execute an allow-listed GraphQL mutation for an authenticated user."""
    settings = get_chat_settings()
    allowed_mutations = set(settings["allowed_mutations"])
    if mutation not in allowed_mutations:
        raise MutationNotAllowedError(mutation)
    _get_authenticated_user(context)
    if mutation in set(settings["confirm_mutations"]) and not confirmed:
        return {
            "status": "confirmation_required",
            "mutation": mutation,
            "input": dict(input),
        }

    schema = GraphQL.get_schema()
    if schema is None:
        raise ChatSchemaNotInitializedError()

    arguments = ", ".join(
        f"{_camelize(str(key))}: {_graphql_literal(value)}"
        for key, value in input.items()
    )
    query_text = f"mutation ChatMutation {{ {mutation}({arguments}) {{ success }} }}"
    result = schema.execute(query_text, context_value=context)
    errors = getattr(result, "errors", None)
    if errors:
        raise ValueError("; ".join(_extract_error_message(error) for error in errors))
    payload = getattr(result, "data", {}).get(mutation, {})
    return {"status": "executed", "data": payload}
