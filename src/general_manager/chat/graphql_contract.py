"""Version 2 chat reads use the executable GraphQL schema without name aliases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from copy import deepcopy
import hashlib
import json
from typing import Any, Never, cast

from graphql import (
    GraphQLSchema,
    Undefined,
    ast_from_value,
    get_named_type,
    is_enum_type,
    is_input_object_type,
    is_leaf_type,
    is_list_type,
    is_non_null_type,
    is_object_type,
    parse_type,
    print_ast,
    validate,
    value_from_ast_untyped,
)
from graphql.execution.values import get_variable_values
from graphql.language import ast
from graphql.language.ast import OperationType

from general_manager.api.graphql import GraphQL
from general_manager.interface.unit_contract import (
    public_unit_contract,
    public_unit_identity,
)

CONTRACT_VERSION = 2
INSPECTION_VERSION = 3


class ChatReadContractError(ValueError):
    """A request cannot be expressed by the exposed read contract."""


def fail_read_contract(message: str) -> Never:
    raise ChatReadContractError(message)


def runtime_schema() -> GraphQLSchema:
    schema = getattr(GraphQL.get_schema(), "graphql_schema", None)
    if not isinstance(schema, GraphQLSchema):
        fail_read_contract("GraphQL read schema is not initialized.")
    return schema


def argument_name(field: Any, role: str) -> str | None:
    """Find the native argument carrying a generated resolver parameter.

    graphql-core's out_name is the executable schema's resolver binding, not a
    guessed spelling or an accepted alias in a tool request.
    """
    matches = [
        name for name, arg in field.args.items() if (arg.out_name or name) == role
    ]
    return matches[0] if len(matches) == 1 else None


def argument_value(
    field: Any, values: Mapping[str, Any], name: str | None, fallback: Any = None
) -> Any:
    """Resolve an argument exactly as declared before applying chat limits."""
    if name is None:
        return fallback
    if name in values:
        return values[name]
    default = field.args[name].default_value
    return fallback if default is Undefined else default


def _manager_types(
    schema: GraphQLSchema, registry: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    by_identity = {
        id(value): name
        for name, value in (
            GraphQL.graphql_type_registry if registry is None else registry
        ).items()
    }
    return {
        by_identity[id(getattr(value, "graphene_type", None))]: value
        for value in schema.type_map.values()
        if id(getattr(value, "graphene_type", None)) in by_identity
    }


def exposed(manager: str) -> bool:
    return bool(getattr(GraphQL.manager_registry.get(manager), "chat_exposed", False))


def _hidden_type(
    field_type: Any,
    manager_types: Mapping[str, Any],
    visibility: Mapping[str, bool] | None = None,
) -> bool:
    named = get_named_type(field_type)
    if visibility is not None:
        return not visibility.get(named.name, True)
    owner = getattr(
        getattr(named, "graphene_type", None), "_general_manager_filter_owner", None
    )
    if owner is not None and not bool(getattr(owner, "chat_exposed", False)):
        return True
    return any(
        value is named and not exposed(manager)
        for manager, value in manager_types.items()
    )


def _input_allowed(
    input_type: Any,
    value: Any,
    manager_types: Mapping[str, Any],
    *,
    coerced: bool = False,
    visibility: Mapping[str, bool] | None = None,
) -> bool:
    """Check supplied inputs and effective defaults against manager exposure."""
    if _hidden_type(input_type, manager_types, visibility):
        return False
    if value is Undefined or value is None:
        return True
    if is_non_null_type(input_type):
        input_type = input_type.of_type
    if is_list_type(input_type):
        return all(
            _input_allowed(
                input_type.of_type,
                item,
                manager_types,
                coerced=coerced,
                visibility=visibility,
            )
            for item in (
                value
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
                else [value]
            )
        )
    if is_input_object_type(input_type) and isinstance(value, Mapping):
        for name, field in input_type.fields.items():
            key = (field.out_name or name) if coerced else name
            if key in value:
                if not _input_allowed(
                    field.type,
                    value[key],
                    manager_types,
                    coerced=coerced,
                    visibility=visibility,
                ):
                    return False
            elif field.default_value is not Undefined and not _input_allowed(
                field.type,
                field.default_value,
                manager_types,
                coerced=True,
                visibility=visibility,
            ):
                return False
    return True


def _visible_arguments(
    field: Any,
    manager_types: Mapping[str, Any],
    visibility: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    return {
        name: arg
        for name, arg in getattr(field, "args", {}).items()
        if _input_allowed(
            arg.type,
            arg.default_value,
            manager_types,
            coerced=True,
            visibility=visibility,
        )
    }


def _list_item(field_type: Any) -> Any:
    if is_non_null_type(field_type):
        field_type = field_type.of_type
    return get_named_type(field_type.of_type) if is_list_type(field_type) else None


def page_shape(output_type: Any) -> tuple[str, str, str] | None:
    """Recognize the generated page capability, never guess a manager root."""
    named = get_named_type(output_type)
    if not is_object_type(named):
        return None
    item = named.fields.get("items")
    if item is None or _list_item(item.type) is None:
        return None
    for info_name in ("pageInfo", "page_info"):
        info = named.fields.get(info_name)
        info_type = get_named_type(info.type) if info else None
        if info_type is not None and is_object_type(info_type):
            for count_name in ("totalCount", "total_count"):
                if count_name in info_type.fields:
                    return "items", info_name, count_name
    return None


def _relations(
    output_type: Any,
    manager_types: Mapping[str, Any],
    visibility: Mapping[str, bool] | None = None,
) -> list[dict[str, Any]]:
    relations = []
    for name, field in output_type.fields.items():
        named = get_named_type(field.type)
        path = [name]
        shape = page_shape(named)
        if shape:
            named = _list_item(cast(Any, named).fields[shape[0]].type)
            path.append(shape[0])
        for manager, target in manager_types.items():
            if named is target and not _hidden_type(target, manager_types, visibility):
                relations.append({"name": name, "target": manager, "path": path})
    return relations


def _cache_key(schema: GraphQLSchema) -> tuple[Any, ...]:
    # A runtime schema is immutable after construction. Registry exposure/domain
    # changes are independently relevant; replacement of graphql_schema invalidates.
    return (
        schema,
        tuple(
            (name, id(value), exposed(name), getattr(value, "__doc__", ""))
            for name, value in sorted(GraphQL.manager_registry.items())
        ),
        tuple(
            (name, id(value))
            for name, value in sorted(GraphQL.graphql_type_registry.items())
        ),
    )


@lru_cache(maxsize=8)
def _index_cached(key: tuple[Any, ...]) -> dict[str, dict[str, Any]]:
    schema = key[0]
    return _build_index(schema, GraphQL.manager_registry, _manager_types(schema))


def _build_index(
    schema: GraphQLSchema,
    registry: Mapping[str, Any],
    manager_types: Mapping[str, Any],
    visibility: Mapping[str, bool] | None = None,
) -> dict[str, dict[str, Any]]:
    index = {}
    for manager, output in manager_types.items():
        if _hidden_type(output, manager_types, visibility):
            continue
        roots = []
        filters: set[str] = set()
        for name, field in (
            schema.query_type.fields if schema.query_type else {}
        ).items():
            shape = page_shape(field.type)
            if (
                not shape
                or _list_item(
                    cast(Any, get_named_type(field.type)).fields[shape[0]].type
                )
                is not output
            ):
                continue
            roots.append(name)
            filter_key = argument_name(field, "filter")
            filter_arg = field.args.get(filter_key) if filter_key else None
            filter_type = get_named_type(filter_arg.type) if filter_arg else None
            if filter_type is not None and is_input_object_type(filter_type):
                filters.update(
                    key
                    for key, value in filter_type.fields.items()
                    if _input_allowed(
                        value.type,
                        value.default_value,
                        manager_types,
                        coerced=True,
                        visibility=visibility,
                    )
                )
        if not roots:
            continue
        description = (
            output.description or getattr(registry.get(manager), "__doc__", "") or ""
        )
        index[manager] = {
            "contract_version": CONTRACT_VERSION,
            "manager": manager,
            "description": " ".join(description.split()),
            "type": output.name,
            "roots": sorted(roots),
            "fields": sorted(
                name
                for name, field in output.fields.items()
                if is_leaf_type(get_named_type(field.type))
            ),
            "relations": _relations(output, manager_types, visibility),
            "filters": sorted(filters),
        }
    return dict(sorted(index.items()))


def compact_index() -> dict[str, dict[str, Any]]:
    schema = getattr(GraphQL.get_schema(), "graphql_schema", None)
    return (
        _index_cached(_cache_key(schema)) if isinstance(schema, GraphQLSchema) else {}
    )


def clear_contract_cache() -> None:
    _index_cached.cache_clear()


def _argument_info(argument: Any) -> dict[str, Any]:
    frozen = getattr(argument, "_contract_argument_info", None)
    return (
        deepcopy(frozen) if frozen is not None else _serialize_argument_info(argument)
    )


def _serialize_argument_info(argument: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"type": str(argument.type)}
    if argument.description:
        result["description"] = argument.description
    if argument.default_value is not Undefined:
        default_ast = ast_from_value(argument.default_value, argument.type)
        # Preserve JSON-compatible values while retaining custom-scalar defaults.
        result["default"] = (
            value_from_ast_untyped(default_ast) if default_ast is not None else None
        )
        if default_ast is not None:
            result["default_graphql"] = print_ast(default_ast)
    return result


def _contract_fields(
    named: Any,
    manager_types: Mapping[str, Any],
    visibility: Mapping[str, bool],
) -> dict[str, Any]:
    """Select definitions using the same exposure/default rules as inspection."""
    fields = {}
    for name, field in named.fields.items():
        target = get_named_type(field.type)
        if _hidden_type(target, manager_types, visibility):
            continue
        if is_input_object_type(named) and not _input_allowed(
            field.type,
            field.default_value,
            manager_types,
            coerced=True,
            visibility=visibility,
        ):
            continue
        shape = page_shape(target)
        if shape and _hidden_type(
            _list_item(target.fields[shape[0]].type), manager_types, visibility
        ):
            continue
        fields[name] = field
    return fields


def _copy_contract_schema(
    schema: GraphQLSchema,
    *,
    manager: str | None = None,
    roots: Sequence[str] = (),
    manager_types: Mapping[str, Any] | None = None,
    visibility: Mapping[str, bool] | None = None,
) -> GraphQLSchema:
    """Freeze observable definitions; never clone private runtime values.

    Production captures only the selected manager's reachable exposed contract.
    Related managers remain references. Native GraphQL serialization freezes
    public defaults before later formatting hooks; arbitrary internal enum values,
    custom-scalar state, resolvers and metadata are not copied. The concrete shells
    are an inspection graph and must never execute or validate GraphQL requests.
    An omitted manager returns named shells only for internal identity checks.
    """
    manager_types = {} if manager_types is None else manager_types
    visibility = {} if visibility is None else visibility
    selected: dict[str, tuple[Any, dict[str, Any]]] = {}
    root_fields = (
        {name: schema.query_type.fields[name] for name in roots}
        if schema.query_type is not None and manager is not None
        else {}
    )
    pending = (
        [get_named_type(field.type) for field in root_fields.values()]
        if manager is not None
        else []
    )
    pending.extend(
        get_named_type(arg.type)
        for field in root_fields.values()
        for arg in _visible_arguments(field, manager_types, visibility).values()
    )
    while pending:
        named = pending.pop()
        if named.name in selected or _hidden_type(named, manager_types, visibility):
            continue
        fields = {}
        reference = any(
            target is named and name != manager
            for name, target in manager_types.items()
        )
        if not reference and (is_object_type(named) or is_input_object_type(named)):
            fields = _contract_fields(named, manager_types, visibility)
            for field in fields.values():
                pending.append(get_named_type(field.type))
                pending.extend(
                    get_named_type(arg.type)
                    for arg in _visible_arguments(
                        field, manager_types, visibility
                    ).values()
                )
        selected[named.name] = (named, fields)

    # Freeze only explicit public units before default serialization hooks can
    # mutate runtime objects. Opaque extensions remain outside this capture.
    captured_units = {
        id(field): public_unit_contract(field)
        for named, fields in selected.values()
        if is_object_type(named)
        for field in fields.values()
    }
    captured_identities = {
        id(field): public_unit_identity(field)
        for named, fields in selected.values()
        if is_object_type(named)
        for field in fields.values()
    }
    result = object.__new__(type(schema))
    memo: dict[int, Any] = {id(schema): result}
    for named in schema.type_map.values():
        copied = cast(Any, object.__new__(type(named)))
        copied.name, copied.description = named.name, named.description
        if hasattr(named, "graphene_type"):
            copied.graphene_type = named.graphene_type
        if is_object_type(named) or is_input_object_type(named):
            copied.fields = {}
        elif is_enum_type(named):
            copied.values = {}
        elif is_leaf_type(named):
            copied.serialize = cast(Any, named).serialize
        memo[id(named)] = copied

    def type_ref(value: Any) -> Any:
        if id(value) not in memo:
            copied = object.__new__(type(value))
            copied.of_type = type_ref(value.of_type)
            memo[id(value)] = copied
        return memo[id(value)]

    def argument(value: Any) -> Any:
        copied = object.__new__(type(value))
        copied.type = type_ref(value.type)
        copied.description, copied.out_name = value.description, value.out_name
        # Exposure/default admissibility was checked on the original capture.
        # Only its native serialized public representation is observable here.
        copied.default_value = Undefined
        copied._contract_argument_info = _serialize_argument_info(value)
        return copied

    def copy_field(value: Any, *, input_field: bool) -> Any:
        if input_field:
            return argument(value)
        copied = object.__new__(type(value))
        copied.type, copied.description = type_ref(value.type), value.description
        copied._contract_unit_info = deepcopy(captured_units.get(id(value)))
        copied._contract_identity_info = deepcopy(captured_identities.get(id(value)))
        copied.args = {
            name: argument(arg)
            for name, arg in _visible_arguments(
                value, manager_types, visibility
            ).items()
        }
        return copied

    # Freeze enum spellings and field memberships before serializing defaults.
    for named, _ in selected.values():
        if is_enum_type(named):
            memo[id(named)].values = dict.fromkeys(cast(Any, named).values)
    definitions = [
        (memo[id(named)], tuple(fields.items()), is_input_object_type(named))
        for named, fields in selected.values()
        if is_object_type(named) or is_input_object_type(named)
    ]
    for copied, field_items, input_field in definitions:
        copied.fields = {
            name: copy_field(value, input_field=input_field)
            for name, value in field_items
        }
    if schema.query_type is not None and manager is not None:
        memo[id(schema.query_type)].fields = {
            name: copy_field(value, input_field=False)
            for name, value in root_fields.items()
        }
    result.type_map = {name: memo[id(named)] for name, named in schema.type_map.items()}
    result.query_type = (
        memo[id(schema.query_type)] if schema.query_type is not None else None
    )
    return result


def capture_manager_contract(manager: str) -> tuple[dict[str, Any] | None, str]:
    """Capture definitions and exposure once, without ORM/backend reflection.

    Copy GraphQL definitions/defaults so serializers cannot combine later global
    state with this observation. Visibility is captured separately because the
    Graphene classes on copied GraphQL types intentionally retain their identity.
    This inspection path never uses the immutable-schema discovery cache.
    """
    original = getattr(GraphQL.get_schema(), "graphql_schema", None)
    if not isinstance(original, GraphQLSchema):
        return None, ""
    registry = dict(GraphQL.manager_registry)
    type_registry = dict(GraphQL.graphql_type_registry)
    original_managers = _manager_types(original, type_registry)
    exposure = {
        name: bool(getattr(value, "chat_exposed", False))
        for name, value in registry.items()
    }
    visibility = {
        named.name: exposure.get(name, False)
        for name, named in original_managers.items()
    }
    for named in original.type_map.values():
        owner = getattr(
            getattr(named, "graphene_type", None), "_general_manager_filter_owner", None
        )
        if owner is not None:
            visibility[named.name] = bool(getattr(owner, "chat_exposed", False))
    summary = _build_index(original, registry, original_managers, visibility).get(
        manager
    )
    schema = _copy_contract_schema(
        original,
        manager=manager,
        roots=summary["roots"] if summary else (),
        manager_types=original_managers,
        visibility=visibility,
    )
    manager_types = {
        name: schema.get_type(named.name) for name, named in original_managers.items()
    }
    full = (
        None
        if summary is None
        else _manager_contract(manager, summary, schema, manager_types, visibility)
    )
    snapshot = hashlib.sha256(
        json.dumps(
            {
                "projection_version": INSPECTION_VERSION,
                "manager": manager,
                "contract": full,
                "exposure": exposure,
                "visibility": visibility,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    return full, snapshot


def manager_schema(manager: str) -> dict[str, Any] | None:
    """Full Python contract; selective inspection is an explicit separate API."""
    return capture_manager_contract(manager)[0]


def _manager_contract(
    manager: str,
    summary: Mapping[str, Any],
    schema: GraphQLSchema,
    manager_types: Mapping[str, Any],
    visibility: Mapping[str, bool],
) -> dict[str, Any]:
    assert schema.query_type is not None
    roots = {name: schema.query_type.fields[name] for name in summary["roots"]}
    pending = [get_named_type(field.type) for field in roots.values()]
    pending.extend(
        get_named_type(arg.type)
        for field in roots.values()
        for arg in _visible_arguments(field, manager_types, visibility).values()
    )
    types: dict[str, Any] = {}
    while pending:
        named = pending.pop()
        if named.name in types or _hidden_type(named, manager_types, visibility):
            continue
        other_manager = next(
            (
                name
                for name, target in manager_types.items()
                if target is named and name != manager
            ),
            None,
        )
        if other_manager is not None:
            types[named.name] = {"kind": "reference", "manager": other_manager}
            continue
        if is_enum_type(named):
            types[named.name] = {"kind": "enum", "values": list(named.values)}
        elif is_object_type(named) or is_input_object_type(named):
            fields: dict[str, dict[str, Any]] = {}
            types[named.name] = {
                "kind": "object" if is_object_type(named) else "input",
                "fields": fields,
            }
            for name, field in _contract_fields(
                named, manager_types, visibility
            ).items():
                target = get_named_type(field.type)
                fields[name] = (
                    _argument_info(field)
                    if is_input_object_type(named)
                    else {"type": str(field.type)}
                )
                if field.description:
                    fields[name]["description"] = field.description
                if not is_input_object_type(named):
                    unit = getattr(field, "_contract_unit_info", None)
                    if unit is not None:
                        fields[name]["unit_contract"] = deepcopy(unit)
                    identity = getattr(field, "_contract_identity_info", None)
                    if identity is not None:
                        fields[name]["identity_contract"] = deepcopy(identity)
                args = _visible_arguments(field, manager_types, visibility)
                if args:
                    fields[name]["arguments"] = {
                        key: _argument_info(arg) for key, arg in args.items()
                    }
                pending.append(target)
                pending.extend(get_named_type(arg.type) for arg in args.values())
        else:
            types[named.name] = {
                "kind": "scalar" if is_leaf_type(named) else "unsupported"
            }
    return {
        **summary,
        "root_fields": {
            name: {
                "type": str(field.type),
                "arguments": {
                    key: _argument_info(arg)
                    for key, arg in _visible_arguments(
                        field, manager_types, visibility
                    ).items()
                },
            }
            for name, field in roots.items()
        },
        "types": types,
        "unsupported": [
            "unions/interfaces/fragments",
            "arbitrary query roots",
            "Python field aliases",
        ],
    }


class ReadCompiler:
    """Compile structured native selections with schema-typed variables."""

    def __init__(self, schema: GraphQLSchema, max_results: int | None) -> None:
        self.schema = schema
        self.max_results = max_results
        self.manager_types = _manager_types(schema)
        self.variables: dict[str, Any] = {}
        self.definitions: list[ast.VariableDefinitionNode] = []

    def arguments(
        self, field: Any, values: Mapping[str, Any]
    ) -> tuple[ast.ArgumentNode, ...]:
        nodes = []
        for name, argument in field.args.items():
            value = values.get(name, argument.default_value)
            if (name in values or value is not Undefined) and not _input_allowed(
                argument.type, value, self.manager_types, coerced=name not in values
            ):
                fail_read_contract(
                    f"Argument {name!r} targets a manager that is not chat-exposed."
                )
        for name, value in values.items():
            if name not in field.args:
                fail_read_contract(
                    f"Unknown GraphQL argument {name!r}; inspect get_manager_schema (contract 2)."
                )
            argument = field.args[name]
            variable_name = f"v{len(self.variables)}"
            self.variables[variable_name] = value
            variable = ast.VariableNode(name=ast.NameNode(value=variable_name))
            self.definitions.append(
                ast.VariableDefinitionNode(
                    variable=variable, type=parse_type(str(argument.type))
                )
            )
            nodes.append(
                ast.ArgumentNode(name=ast.NameNode(value=name), value=variable)
            )
        return tuple(nodes)

    def selection(self, output: Any, fields: Sequence[Any]) -> ast.SelectionSetNode:
        named = get_named_type(output)
        if not is_object_type(named):
            fail_read_contract(
                f"Unsupported selection on {named}; object selections are required."
            )
        if isinstance(fields, (str, bytes)) or not fields:
            fail_read_contract("A nonempty list of GraphQL fields is required.")
        nodes = []
        seen: set[str] = set()
        expanded: list[Any] = []
        for item in fields:
            if item == "*":
                expanded.extend(
                    name
                    for name, field in named.fields.items()
                    if is_leaf_type(get_named_type(field.type))
                    and not any(
                        is_non_null_type(arg.type) and arg.default_value is Undefined
                        for arg in field.args.values()
                    )
                )
            else:
                expanded.append(item)
        for item in expanded:
            arguments: Mapping[str, Any] = {}
            nested: Sequence[Any] | None = None
            if isinstance(item, str):
                name = item
            elif isinstance(item, Mapping) and "field" in item:
                if set(item) - {"field", "arguments", "fields"}:
                    fail_read_contract("Unknown structured selection keys.")
                name, arguments, nested = (
                    item["field"],
                    item.get("arguments", {}),
                    item.get("fields"),
                )
            elif isinstance(item, Mapping) and len(item) == 1:
                name, nested = next(iter(item.items()))
            else:
                fail_read_contract(
                    "Use a GraphQL field name, {name: fields}, or {field, arguments, fields}."
                )
            if (
                not isinstance(name, str)
                or name not in named.fields
                or name.startswith("__")
            ):
                fail_read_contract(
                    f"Unknown GraphQL field {name!r} on {named.name}; inspect get_manager_schema (contract 2)."
                )
            if name in seen:
                fail_read_contract(
                    f"Duplicate selection {name!r}; select each field once."
                )
            seen.add(name)
            field = named.fields[name]
            target = get_named_type(field.type)
            if _hidden_type(target, self.manager_types):
                fail_read_contract(
                    f"Field {name!r} targets a manager that is not chat-exposed."
                )
            shape = page_shape(target)
            if shape and _hidden_type(
                _list_item(target.fields[shape[0]].type), self.manager_types
            ):
                fail_read_contract(
                    f"Field {name!r} targets a manager that is not chat-exposed."
                )
            if not isinstance(arguments, Mapping):
                fail_read_contract("Field arguments must be an object.")
            arguments = dict(arguments)
            if shape and self.max_results is not None:
                page_size = argument_name(field, "page_size")
                if page_size is None:
                    fail_read_contract(
                        f"Nested page {name!r} cannot enforce the configured result cap."
                    )
                requested = argument_value(
                    field, arguments, page_size, self.max_results
                )
                if (
                    isinstance(requested, bool)
                    or not isinstance(requested, int)
                    or requested < 1
                ):
                    fail_read_contract("pageSize must be a positive integer.")
                arguments[page_size] = min(requested, self.max_results)
            if nested is not None and (
                not isinstance(nested, Sequence) or isinstance(nested, (str, bytes))
            ):
                fail_read_contract("Nested fields must be a list.")
            selection = self.selection(target, nested) if nested is not None else None
            nodes.append(
                ast.FieldNode(
                    name=ast.NameNode(value=name),
                    arguments=self.arguments(field, arguments),
                    selection_set=selection,
                )
            )
        return ast.SelectionSetNode(selections=tuple(nodes))

    def document(
        self, root: str, arguments: Mapping[str, Any], fields: Sequence[Any]
    ) -> tuple[str, dict[str, Any]]:
        assert self.schema.query_type is not None
        root_field = self.schema.query_type.fields[root]
        selection = self.selection(root_field.type, fields)
        field = ast.FieldNode(
            name=ast.NameNode(value=root),
            arguments=self.arguments(root_field, arguments),
            selection_set=selection,
        )
        document = ast.DocumentNode(
            definitions=(
                ast.OperationDefinitionNode(
                    operation=OperationType.QUERY,
                    name=ast.NameNode(value="ChatQuery"),
                    variable_definitions=tuple(self.definitions),
                    selection_set=ast.SelectionSetNode(selections=(field,)),
                ),
            )
        )
        errors = validate(self.schema, document)
        if errors:
            fail_read_contract("; ".join(error.message for error in errors))
        coerced = get_variable_values(self.schema, self.definitions, self.variables)
        if isinstance(coerced, list):
            fail_read_contract("; ".join(error.message for error in coerced))
        return print_ast(document), self.variables


def nested_complete(value: Any) -> bool:
    """Unknown/missing nested page totals cannot prove a complete result."""
    if isinstance(value, list):
        return all(nested_complete(item) for item in value)
    if not isinstance(value, Mapping):
        return True
    if "items" in value:
        info = value.get("pageInfo", value.get("page_info"))
        count = (
            info.get("totalCount", info.get("total_count"))
            if isinstance(info, Mapping)
            else None
        )
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not isinstance(value["items"], list)
            or count < 0
            or count != len(value["items"])
        ):
            return False
    return all(nested_complete(item) for item in value.values())
