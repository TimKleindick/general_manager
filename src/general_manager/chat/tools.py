"""Tool registry for chat."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, Protocol

from django.db import connection, transaction

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


_GRAPHQL_IDENTIFIER_RE = re.compile(r"^[_A-Za-z][_0-9A-Za-z]*$")
_LIMIT_ERROR = "limit must be a positive integer"
_OFFSET_ERROR = "offset must be a non-negative integer"


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


class InvalidChatQueryFieldError(ValueError):
    """Raised when a chat query field identifier is malformed."""

    def __init__(self, field: str) -> None:
        super().__init__(f"Invalid chat query field: {field}")


class UnknownChatQueryFieldError(ValueError):
    """Raised when a chat query field is not present in the indexed schema."""

    def __init__(self, field: str) -> None:
        super().__init__(f"Unknown chat query field: {field}")


class UnknownChatQueryFilterError(ValueError):
    """Raised when a chat query filter is not present in the indexed schema."""

    def __init__(self, filter_name: str) -> None:
        super().__init__(f"Unknown chat query filter: {filter_name}")


class InvalidChatQueryFilterValueError(ValueError):
    """Raised when a chat query filter value is malformed for its input type."""

    def __init__(self, filter_name: str) -> None:
        super().__init__(
            f"Chat query filter '{filter_name}' does not accept nested filters."
        )


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


class InvalidChatMutationNameError(ValueError):
    """Raised when a chat mutation identifier is malformed."""

    def __init__(self, mutation: str) -> None:
        super().__init__(f"Invalid chat mutation name: {mutation}")


class InvalidChatMutationInputKeyError(ValueError):
    """Raised when a chat mutation input key identifier is malformed."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Invalid chat mutation input key: {key}")


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
        limit, offset = _normalize_query_pagination(args)
        return query(
            manager=direct_manager,
            filters=args.get("filters", {}),
            fields=args.get("fields", []),
            limit=limit,
            offset=offset,
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
        limit, offset = _normalize_query_pagination(args)
        return query(
            manager=str(args.get("manager", "")),
            filters=args.get("filters", {}),
            fields=args.get("fields", []),
            limit=limit,
            offset=offset,
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


def _normalize_query_limit(limit: object) -> int | None:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError(_LIMIT_ERROR)
    return limit


def _normalize_query_offset(offset: object) -> int:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError(_OFFSET_ERROR)
    return offset


def _normalize_query_pagination(args: Mapping[str, Any]) -> tuple[int | None, int]:
    return _normalize_query_limit(args.get("limit")), _normalize_query_offset(
        args.get("offset", 0)
    )


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
                    "limit": {"type": "integer", "minimum": 1},
                    "offset": {"type": "integer", "minimum": 0},
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


def _validate_mutation_input_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, inner_value in value.items():
            key_text = str(key)
            if _GRAPHQL_IDENTIFIER_RE.fullmatch(_camelize(key_text)) is None:
                raise InvalidChatMutationInputKeyError(key_text)
            _validate_mutation_input_keys(inner_value)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _validate_mutation_input_keys(item)


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


def _relation_targets_by_name(summary: Mapping[str, Any]) -> dict[str, str]:
    targets: dict[str, str] = {}
    for relation in summary.get("relations", []):
        if not isinstance(relation, Mapping):
            continue
        name = relation.get("name")
        target = relation.get("target")
        if isinstance(name, str) and isinstance(target, str):
            targets[name] = target
    return targets


def _validate_chat_query_field_identifier(field: Any) -> str:
    if not isinstance(field, str) or _GRAPHQL_IDENTIFIER_RE.match(field) is None:
        raise InvalidChatQueryFieldError(str(field))
    return field


def _validate_chat_query_fields(
    manager: str | None, fields: Sequence[Any], *, allow_wildcard: bool = True
) -> None:
    summary = get_manager_schema_summary(manager) if manager is not None else None
    scalar_fields = set(summary.get("fields", [])) if summary is not None else set()
    relation_targets = _relation_targets_by_name(summary) if summary is not None else {}

    for field in fields:
        if isinstance(field, str):
            if field == "*":
                if allow_wildcard:
                    continue
                raise InvalidChatQueryFieldError(field)
            field_name = _validate_chat_query_field_identifier(field)
            if summary is not None and field_name not in scalar_fields:
                raise UnknownChatQueryFieldError(field_name)
            continue
        if isinstance(field, Mapping):
            for name, nested in field.items():
                relation_name = _validate_chat_query_field_identifier(name)
                target_manager = relation_targets.get(relation_name)
                if summary is not None and target_manager is None:
                    raise UnknownChatQueryFieldError(relation_name)
                if not isinstance(nested, Sequence):
                    raise InvalidNestedFieldSelectionError()
                _validate_chat_query_fields(
                    target_manager, list(nested), allow_wildcard=False
                )
            continue
        raise InvalidFieldSelectionError()


def _normalize_fields(manager: str, fields: Sequence[Any]) -> list[Any]:
    normalized: list[Any] = []
    summary = get_manager_schema_summary(manager)
    for field in fields:
        if field == "*" and summary is not None:
            normalized.extend(summary.get("fields", []))
            continue
        normalized.append(field)
    return normalized


def _list_query_field_name(manager: str) -> str:
    return f"{manager.lower()}List"


def _ensure_exposed_manager(manager: str) -> None:
    manager_class = GraphQL.manager_registry.get(manager)
    if manager_class is None or not getattr(manager_class, "chat_exposed", True):
        raise ManagerNotChatExposedError(manager)


def _extract_error_message(error: Any) -> str:
    message = getattr(error, "message", None)
    return str(message if message is not None else error)


def _normalize_filter_key(manager: str, key: str) -> str:
    summary = get_manager_schema_summary(manager)
    if summary is None:
        return key
    filters = set(summary.get("filters", []))
    if key in filters or "__" in key:
        return key
    for suffix in ("icontains", "contains", "gte", "lte", "gt", "lt", "in", "exact"):
        marker = f"_{suffix}"
        if not key.endswith(marker):
            continue
        candidate = f"{key[: -len(marker)]}__{suffix}"
        if candidate in filters:
            return candidate
    return key


def _normalize_filters(manager: str, filters: Mapping[str, Any]) -> dict[str, Any]:
    return {
        _normalize_filter_key(manager, str(key)): value
        for key, value in filters.items()
    }


def _unwrap_graphene_type(field_type: Any) -> Any:
    current = field_type
    while hasattr(current, "of_type"):
        current = current.of_type
    return current


def _input_fields(input_type: Any | None) -> Mapping[str, Any]:
    meta = getattr(input_type, "_meta", None)
    fields = getattr(meta, "fields", None)
    if isinstance(fields, Mapping):
        return fields
    return {}


def _is_nested_filter_input_type(input_type: Any | None) -> bool:
    meta = getattr(input_type, "_meta", None)
    return isinstance(getattr(meta, "fields", None), Mapping)


def _nested_filter_input_type(input_type: Any | None, filter_name: str) -> Any | None:
    field = _input_fields(input_type).get(filter_name)
    if field is None:
        return None
    return _unwrap_graphene_type(getattr(field, "type", None))


def _is_non_string_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _contains_mapping(value: Any) -> bool:
    if isinstance(value, Mapping):
        return True
    if _is_non_string_sequence(value):
        return any(_contains_mapping(item) for item in value)
    return False


def _validate_nested_filter_value(
    value: Any,
    *,
    indexed_filters: set[str],
    input_type: Any | None,
    filter_path: tuple[str, ...],
) -> None:
    if isinstance(value, Mapping):
        _validate_filter_mapping(
            value,
            indexed_filters=indexed_filters,
            input_type=input_type,
            filter_path=filter_path,
        )
        return
    if _is_non_string_sequence(value):
        for item in value:
            _validate_nested_filter_value(
                item,
                indexed_filters=indexed_filters,
                input_type=input_type,
                filter_path=filter_path,
            )


def _resolve_indexed_filter_name(
    filter_name: str, indexed_filters: set[str]
) -> str | None:
    if not indexed_filters:
        return filter_name
    if filter_name in indexed_filters:
        return filter_name
    for indexed_filter in indexed_filters:
        if filter_name == _camelize(indexed_filter):
            return indexed_filter
    return None


def _validate_filter_mapping(
    filters: Mapping[str, Any],
    *,
    indexed_filters: set[str],
    input_type: Any | None,
    filter_path: tuple[str, ...] = (),
) -> None:
    for filter_name, value in filters.items():
        indexed_filter_name = (
            _resolve_indexed_filter_name(filter_name, indexed_filters)
            if isinstance(filter_name, str)
            else None
        )
        if (
            not isinstance(filter_name, str)
            or _GRAPHQL_IDENTIFIER_RE.match(filter_name) is None
            or indexed_filter_name is None
        ):
            raise UnknownChatQueryFilterError(str(filter_name))
        nested_input_type = _nested_filter_input_type(input_type, indexed_filter_name)
        current_filter_path = (*filter_path, filter_name)
        if _is_nested_filter_input_type(nested_input_type):
            _validate_nested_filter_value(
                value,
                indexed_filters=set(_input_fields(nested_input_type)),
                input_type=nested_input_type,
                filter_path=current_filter_path,
            )
            continue
        if _contains_mapping(value):
            raise InvalidChatQueryFilterValueError(".".join(current_filter_path))


def _validate_chat_query_filters(manager: str, filters: Mapping[str, Any]) -> None:
    summary = get_manager_schema_summary(manager)
    indexed_filters = set(summary.get("filters", [])) if summary is not None else set()
    _validate_filter_mapping(
        filters,
        indexed_filters=indexed_filters,
        input_type=GraphQL.graphql_filter_type_registry.get(manager),
    )


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
    limit = _normalize_query_limit(limit)
    offset = _normalize_query_offset(offset)
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
        normalized_filters = _normalize_filters(manager, filters)
        _validate_chat_query_filters(manager, normalized_filters)
        arguments.append(f"filter: {_graphql_literal(normalized_filters)}")
    if page_size is not None:
        arguments.append(f"pageSize: {page_size}")
    argument_block = f"({', '.join(arguments)})" if arguments else ""
    normalized_fields = _normalize_fields(manager, fields)
    _validate_chat_query_fields(manager, normalized_fields)
    selection = _build_selection(normalized_fields)
    query_text = (
        "query ChatQuery { "
        f"{list_field_name}{argument_block} "
        "{ items { "
        f"{selection} "
        "} pageInfo { totalCount } } }"
    )

    timeout_ms = get_query_timeout_ms()
    if timeout_ms is not None and getattr(connection, "vendor", None) == "postgresql":
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = %s", [timeout_ms])
            result = schema.execute(query_text, context_value=context)
    else:
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
    if _GRAPHQL_IDENTIFIER_RE.fullmatch(mutation) is None:
        raise InvalidChatMutationNameError(mutation)
    _validate_mutation_input_keys(input)
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
