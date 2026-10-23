"""Tool registry for chat."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from contextlib import ExitStack
from contextvars import ContextVar
import re
from types import TracebackType
from typing import Any, Protocol

from django.db import connection, transaction

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_resolvers import (
    UnsupportedExcludeNoneRelationFilterError,
    _pagination_sizes,
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
_ACTIVE_SQL_BUDGETS: ContextVar[tuple[tuple[object, int], ...]] = ContextVar(
    "chat_active_sql_budgets", default=()
)
_SUSPENDED_SQL_BUDGET_HOOKS: ContextVar[frozenset[object]] = ContextVar(
    "chat_suspended_sql_budget_hooks", default=frozenset()
)


class ChatToolContext(Protocol):
    """Minimal request context needed by chat tools."""

    user: Any


class ScopeChatContext:
    """Keep the current user and native request/ASGI authorization context."""

    def __init__(
        self,
        *,
        user: Any,
        planned_query_timeout_ms: int | None = None,
        scope: Mapping[str, Any] | None = None,
    ) -> None:
        self.user = user
        self.planned_query_timeout_ms = planned_query_timeout_ms
        self._scope = {} if scope is None else scope

    def __getattr__(self, name: str) -> Any:
        if not name.startswith("_"):
            request = self._scope.get("request")
            if request is not None:
                try:
                    return getattr(request, name)
                except AttributeError:
                    pass
            if name in self._scope:
                return self._scope[name]
        raise AttributeError(name)

    def graphql_context(self) -> Any:
        """Preserve HTTP request identity without changing the authenticated user."""
        request = self._scope.get("request")
        if request is None:
            return self
        if getattr(request, "user", None) is not self.user:
            fail_read_contract(
                "GraphQL request and chat scope supply a different user."
            )
        return request

    @classmethod
    def from_scope(cls, scope: Mapping[str, Any]) -> ScopeChatContext:
        timeout = scope.get("planned_query_timeout_ms")
        return cls(
            user=scope.get("user"),
            scope=scope,
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
    """Return chat tools with independent schemas for caller-local use."""
    if get_chat_settings().get("tool_strategy") == "direct":
        return _get_direct_tool_definitions()
    return [
        {
            "name": name,
            "description": description,
            "input_schema": deepcopy(TOOL_INPUT_SCHEMAS[name]),
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
            "input_schema": deepcopy(
                {
                    **TOOL_INPUT_SCHEMAS["query"],
                    "properties": {
                        key: value
                        for key, value in TOOL_INPUT_SCHEMAS["query"][
                            "properties"
                        ].items()
                        if key != "manager"
                    },
                    "required": ["fields"],
                }
            ),
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


def _execute_graphql(
    schema: Any,
    document: str,
    variables: Mapping[str, Any],
    context: ChatToolContext | None,
) -> Any:
    """Use the HTTP GraphQL middleware builder and its class instantiation rule."""
    from graphene_django.debug.middleware import DjangoDebugMiddleware
    from graphene_django.views import instantiate_middleware
    from general_manager.metrics.graphql import build_graphql_middleware

    middleware = list(instantiate_middleware(build_graphql_middleware()))
    execution_context = (
        context.graphql_context() if isinstance(context, ScopeChatContext) else context
    )
    if execution_context is None:
        # Python's omitted context remains anonymous, with a writable middleware
        # context. Mutation authentication is enforced before this entry point.
        execution_context = ScopeChatContext(user=None)
    options: dict[str, Any] = {
        "variable_values": variables,
        "context_value": execution_context,
    }
    if middleware:
        options["middleware"] = middleware
    if not any(isinstance(entry, DjangoDebugMiddleware) for entry in middleware):
        return schema.execute(document, **options)

    from django.db import connections
    from graphene_django.debug.sql.tracking import unwrap_cursor

    # DjangoDebug normally ends instrumentation when its debug field resolves.
    # Structured chat selections do not request that field. End only wrappers
    # created in this execution, preserving instrumentation owned by an outer call.
    uninstrumented = tuple(
        item for item in connections.all() if not hasattr(item, "_graphene_cursor")
    )
    previous_debug = getattr(execution_context, "django_debug", None)
    try:
        return schema.execute(document, **options)
    finally:
        for item in uninstrumented:
            unwrap_cursor(item)
        if previous_debug is None and hasattr(execution_context, "django_debug"):
            del execution_context.django_debug


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
    _, max_page_size = _pagination_sizes()
    if (
        isinstance(max_results, int)
        and not isinstance(max_results, bool)
        and max_results > 0
    ):
        max_page_size = min(max_results, max_page_size)
    effective_limit = max_page_size if limit is None else min(limit, max_page_size)

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
    with ExitStack() as sql_scope:
        if (
            timeout_ms is not None
            and getattr(connection, "vendor", None) == "postgresql"
        ):
            budget_identity = object()
            active_token = _ACTIVE_SQL_BUDGETS.set(
                (*_ACTIVE_SQL_BUDGETS.get(), (budget_identity, timeout_ms))
            )
            sql_scope.callback(_ACTIVE_SQL_BUDGETS.reset, active_token)
            atomic_scope = ExitStack()
            budget_started = False
            budget_setting = False
            applied_timeout_ms: int | None = None
            budget_failure: BaseException | None = None

            def close_atomic_scope(
                exception_type: type[BaseException] | None,
                exception: BaseException | None,
                traceback: TracebackType | None,
            ) -> bool | None:
                # Django's savepoint cleanup still passes through outer hooks.
                # Suspend existing hooks for cleanup. New queries in callbacks
                # still install a budget, retaining any enclosing query's limit.
                closing_token = _SUSPENDED_SQL_BUDGET_HOOKS.set(
                    _SUSPENDED_SQL_BUDGET_HOOKS.get()
                    | frozenset(identity for identity, _ in _ACTIVE_SQL_BUDGETS.get())
                )
                cleanup_active_token = _ACTIVE_SQL_BUDGETS.set(
                    tuple(
                        entry
                        for entry in _ACTIVE_SQL_BUDGETS.get()
                        if entry[0] is not budget_identity
                    )
                )
                try:
                    if exception is None and budget_failure is not None:
                        return atomic_scope.__exit__(
                            type(budget_failure),
                            budget_failure,
                            budget_failure.__traceback__,
                        )
                    return atomic_scope.__exit__(exception_type, exception, traceback)
                finally:
                    _ACTIVE_SQL_BUDGETS.reset(cleanup_active_token)
                    _SUSPENDED_SQL_BUDGET_HOOKS.reset(closing_token)

            sql_scope.push(close_atomic_scope)

            def execute_with_budget(
                execute: Callable[..., Any],
                sql: str,
                params: Any,
                many: bool,
                execution_context: Mapping[str, Any],
            ) -> Any:
                nonlocal budget_started, budget_setting, applied_timeout_ms
                nonlocal budget_failure
                closing = _SUSPENDED_SQL_BUDGET_HOOKS.get()
                if budget_identity in closing:
                    return execute(sql, params, many, execution_context)
                if budget_failure is not None:
                    # GraphQL may catch one resolver's setup error and continue
                    # siblings. None of their SQL may escape this failed budget.
                    raise budget_failure
                try:
                    if not budget_started:
                        # Installing the hook does not connect. Keep the lazy
                        # transaction until the whole execution has completed.
                        budget_started = True
                        starting_token = _SUSPENDED_SQL_BUDGET_HOOKS.set(
                            closing
                            | frozenset(
                                identity for identity, _ in _ACTIVE_SQL_BUDGETS.get()
                            )
                        )
                        try:
                            atomic_scope.enter_context(transaction.atomic())
                        finally:
                            _SUSPENDED_SQL_BUDGET_HOOKS.reset(starting_token)
                    effective_timeout_ms = min(
                        budget for _, budget in _ACTIVE_SQL_BUDGETS.get()
                    )
                    if (
                        not budget_setting
                        and applied_timeout_ms != effective_timeout_ms
                    ):
                        # Nested hooks see the same active minimum. Reapply when
                        # an inner scope ends, before the next outer statement.
                        budget_setting = True
                        try:
                            with connection.cursor() as cursor:
                                cursor.execute(
                                    "SET LOCAL statement_timeout = %s",
                                    [effective_timeout_ms],
                                )
                            applied_timeout_ms = effective_timeout_ms
                        finally:
                            budget_setting = False
                except BaseException as error:
                    budget_failure = error
                    raise
                return execute(sql, params, many, execution_context)

            sql_scope.enter_context(connection.execute_wrapper(execute_with_budget))
        result = _execute_graphql(schema, query_text, variables, context)
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
    result = _execute_graphql(schema, query_text, variables, context)
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
