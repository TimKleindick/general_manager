"""Tool registry for chat."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, Protocol

from django.db import connection, transaction

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_resolvers import (
    UnsupportedExcludeNoneRelationFilterError,
)
from general_manager.chat.rate_limits import get_query_timeout_ms
from general_manager.chat.mutation_inputs import mutation_document
from general_manager.chat.graphql_contract import (
    ReadCompiler,
    argument_name,
    argument_value,
    runtime_schema,
    page_shape,
    nested_complete,
    fail_read_contract,
)
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

    def __init__(
        self, *, user: Any, planned_query_timeout_ms: int | None = None
    ) -> None:
        self.user = user
        self.planned_query_timeout_ms = planned_query_timeout_ms

    @classmethod
    def from_scope(cls, scope: Mapping[str, Any]) -> ScopeChatContext:
        timeout = scope.get("planned_query_timeout_ms")
        return cls(
            user=scope.get("user"),
            planned_query_timeout_ms=(
                timeout
                if isinstance(timeout, int) and not isinstance(timeout, bool)
                else None
            ),
        )


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
            root=args.get("root"),
            arguments=args.get("arguments"),
            filters=args.get("filters", {}),
            fields=args.get("fields", []),
            limit=limit,
            offset=offset,
            context=context,
        )
    if name == "search_managers":
        return search_managers(str(args.get("query", "")))
    if name == "get_manager_schema":
        from general_manager.chat.schema_inspection import dispatch_schema_inspection

        return dispatch_schema_inspection(args)
    if name == "find_path":
        return find_path(
            str(args.get("from_manager", "")), str(args.get("to_manager", ""))
        )
    if name == "query":
        limit, offset = _normalize_query_pagination(args)
        return query(
            manager=str(args.get("manager", "")),
            root=args.get("root"),
            arguments=args.get("arguments"),
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
                **TOOL_INPUT_SCHEMAS["query"],
                "properties": {
                    key: value
                    for key, value in TOOL_INPUT_SCHEMAS["query"]["properties"].items()
                    if key != "manager"
                },
                "required": ["fields"],
            },
        }
        for manager_name in build_schema_index()
    ]


def search_managers(query: str) -> list[dict[str, Any]]:
    """Search exposed managers by natural-language text."""
    return search_manager_summaries(query)


def get_manager_schema(
    manager: str,
    *,
    view: str | None = None,
    types: object = None,
    snapshot: object = None,
) -> dict[str, Any] | None:
    """Full Python contract by default; explicit view opts into selective loading.

    execute_chat_tool defaults to overview even when called directly from Python.
    """
    if view is None and types is None and snapshot is None:
        return get_manager_schema_summary(manager)
    from general_manager.chat.schema_inspection import inspect_manager_schema

    return inspect_manager_schema(
        manager, view="full" if view is None else view, types=types, snapshot=snapshot
    )


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


def _ensure_exposed_manager(manager: str) -> None:
    manager_class = GraphQL.manager_registry.get(manager)
    if manager_class is None or not getattr(manager_class, "chat_exposed", False):
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
    root: str | None = None,
    arguments: Mapping[str, Any] | None = None,
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

    native_schema = runtime_schema()
    summary = build_schema_index().get(manager)
    roots = summary.get("roots", []) if summary else []
    if root is None and len(roots) != 1:
        fail_read_contract("Select an explicit advertised GraphQL root.")
    list_field_name = root or roots[0]
    if list_field_name not in roots:
        fail_read_contract("Root is not an advertised read root for this manager.")
    assert native_schema.query_type is not None
    root_field = native_schema.query_type.fields[list_field_name]
    shape = page_shape(root_field.type)
    if shape is None:
        fail_read_contract("Unsupported root pagination shape.")
    item_name, info_name, count_name = shape
    if arguments is not None and not isinstance(arguments, Mapping):
        fail_read_contract(
            "arguments must be an object of native GraphQL argument names."
        )
    if not isinstance(filters, Mapping):
        fail_read_contract("filters must be a native GraphQL input object.")
    native_arguments = dict(arguments or {})
    native_page_name = argument_name(root_field, "page")
    if offset:
        page_size_name = argument_name(root_field, "page_size")
        if (
            native_page_name is None
            or page_size_name is None
            or effective_limit is None
        ):
            fail_read_contract(
                "Offset requires native paging and a finite result limit."
            )
        if native_page_name in native_arguments or page_size_name in native_arguments:
            fail_read_contract(
                "Use either offset or native page/pageSize arguments, not both."
            )
        # Translate the requested window into at most two bounded native pages.
        # Never expand resolver pageSize by the offset.
        first_page, skip = divmod(offset, effective_limit)
        pages = []
        selected: list[dict[str, Any]] = []
        for page_number in (first_page + 1, first_page + 2):
            page_result = query(
                manager=manager,
                filters=filters,
                fields=fields,
                root=list_field_name,
                arguments={
                    **native_arguments,
                    native_page_name: page_number,
                    page_size_name: effective_limit,
                },
                limit=effective_limit,
                context=context,
            )
            pages.append(page_result)
            selected.extend(
                page_result["data"][skip:][: effective_limit - len(selected)]
            )
            skip = 0
            if (
                len(selected) >= effective_limit
                or page_result["has_more"] is False
                or not page_result["data"]
            ):
                break
        counts = [page_result["total_count"] for page_result in pages]
        total = counts[0] if all(value == counts[0] for value in counts) else None
        return {
            "data": selected,
            "total_count": total,
            "has_more": None if total is None else total > offset + len(selected),
            "complete": False,
        }
    native_page = argument_value(root_field, native_arguments, native_page_name, 1)
    if (
        isinstance(native_page, bool)
        or not isinstance(native_page, int)
        or native_page < 1
    ):
        fail_read_contract("page must be a positive integer.")
    if filters:
        filter_name = argument_name(root_field, "filter")
        if filter_name is None:
            fail_read_contract("This root has no supported filter argument.")
        if filter_name in native_arguments:
            fail_read_contract("Specify either filters or arguments.filter, not both.")
        native_arguments[filter_name] = dict(filters)
    page_size_name = argument_name(root_field, "page_size")
    if effective_limit is not None:
        if page_size_name is None:
            fail_read_contract("Root cannot enforce a result limit.")
        requested = argument_value(
            root_field, native_arguments, page_size_name, offset + effective_limit
        )
        if (
            isinstance(requested, bool)
            or not isinstance(requested, int)
            or requested < 1
        ):
            fail_read_contract("pageSize must be a positive integer.")
        native_arguments[page_size_name] = min(requested, offset + effective_limit)
    requested_page_size = argument_value(root_field, native_arguments, page_size_name)
    if requested_page_size is not None and (
        isinstance(requested_page_size, bool)
        or not isinstance(requested_page_size, int)
        or requested_page_size < 1
    ):
        fail_read_contract("pageSize must be a positive integer.")
    if native_page > 1 and requested_page_size is None:
        fail_read_contract(
            "Specify pageSize when using a native page greater than one."
        )
    page_offset = (native_page - 1) * (requested_page_size or 0)
    compiler = ReadCompiler(
        native_schema,
        max_results
        if isinstance(max_results, int)
        and not isinstance(max_results, bool)
        and max_results > 0
        else None,
    )
    query_text, variables = compiler.document(
        list_field_name,
        native_arguments,
        [{item_name: fields}, {info_name: [count_name]}],
    )

    timeout_ms = get_query_timeout_ms()
    planned_timeout_ms = getattr(context, "planned_query_timeout_ms", None)
    if (
        isinstance(planned_timeout_ms, int)
        and not isinstance(planned_timeout_ms, bool)
        and planned_timeout_ms > 0
    ):
        timeout_ms = (
            planned_timeout_ms
            if timeout_ms is None
            else min(timeout_ms, planned_timeout_ms)
        )
    if timeout_ms is not None and getattr(connection, "vendor", None) == "postgresql":
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = %s", [timeout_ms])
            result = schema.execute(
                query_text, variable_values=variables, context_value=context
            )
    else:
        result = schema.execute(
            query_text, variable_values=variables, context_value=context
        )
    errors = getattr(result, "errors", None)
    if errors:
        original_errors = [getattr(error, "original_error", None) for error in errors]
        if all(
            isinstance(error, UnsupportedExcludeNoneRelationFilterError)
            for error in original_errors
        ):
            # Mixed resolver failures must retain the infrastructure error path.
            original = original_errors[0]
            assert isinstance(original, UnsupportedExcludeNoneRelationFilterError)
            raise original
        raise ValueError("; ".join(_extract_error_message(error) for error in errors))

    payload = getattr(result, "data", {}).get(list_field_name, {})
    items = list(payload.get(item_name, []))
    if offset:
        items = items[offset:]
    if effective_limit is not None:
        items = items[:effective_limit]
    raw_count = payload.get(info_name, {}).get(count_name)
    total_count = raw_count if type(raw_count) is int and raw_count >= 0 else None
    return {
        "data": items,
        "total_count": total_count,
        "has_more": None
        if total_count is None
        else total_count > page_offset + offset + len(items),
        "complete": page_offset == 0
        and offset == 0
        and total_count == len(items)
        and nested_complete(items),
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

    query_text, variables = mutation_document(
        runtime_schema(), mutation, input, _camelize
    )
    result = schema.execute(
        query_text, variable_values=variables, context_value=context
    )
    errors = getattr(result, "errors", None)
    if errors:
        raise ValueError("; ".join(_extract_error_message(error) for error in errors))
    payload = getattr(result, "data", {}).get(mutation, {})
    return {"status": "executed", "data": payload}


def execute_confirmed_chat_mutation(
    *,
    mutation: str,
    input: Mapping[str, Any],
    context: ChatToolContext | None,
) -> dict[str, Any]:
    """Execute a mutation after a transport has claimed client approval."""
    return mutate(
        mutation=mutation,
        input=input,
        confirmed=True,
        context=context,
    )
